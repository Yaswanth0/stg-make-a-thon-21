"""Researcher: looks things up online. The only agent that needs internet.

Weather questions go to Open-Meteo (wttr.in as a backup), which return real
measurements, so no LLM is needed. Scores, news and anything "latest" go to
DuckDuckGo News, whose dated headlines carry the facts (live-score sites build
their pages with JavaScript, which can't be read here). Everything else goes
to DuckDuckGo web search; its results only carry a short page description, so
the text of the top pages is read too. The LLM then answers from all that,
and any number it states that isn't in there is dropped (grounding.py).
"""

import json
import logging
import re
import socket
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import quote
from urllib.request import Request, urlopen

import config
import leds
import prompts
from agents.base import Agent
from db import keywords
from grounding import keep_supported

log = logging.getLogger("researcher")

SYSTEM_PROMPT = prompts.RESEARCHER

LEADING = re.compile(
    r"^(please |can you |could you )?(search( the (web|internet))?( for)?|google|look up|find out"
    r"|browse( for)?|check( on)?|tell me)\s*",
    re.IGNORECASE,
)
OFFLINE_REPLY = "I can't search the web right now because I'm not connected to the internet."
NOT_FOUND_REPLY = "I couldn't find a reliable answer to that online."
# Questions about what's happening now: answered from news headlines.
NEWSY = re.compile(
    r"\b(scores?|match|matches|news|headlines|latest|live|who won|results?|breaking|election|"
    r"today'?s?|yesterday'?s?|this week)\b", re.IGNORECASE)
USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

WEATHER = re.compile(r"\b(weather|temperature|forecast|rain(ing|y)?|humid(ity)?|how (hot|cold))\b", re.IGNORECASE)
PLACE = re.compile(
    r"\b(?:in|at|for|of)\s+([a-z][a-z .'-]*?)\s*(?:\b(?:today|tomorrow|now|right now|currently|this week)\b.*)?[.?!]*$",
    re.IGNORECASE,
)


def search_query(text):
    """"Search for the Pi 5 price" -> "the Pi 5 price"."""
    query = LEADING.sub("", text.strip()).rstrip(".!?")
    query = re.sub(r"\s+(on(line| the (internet|web)))$", "", query, flags=re.IGNORECASE)
    return query.strip() or text.strip()


def is_online(timeout=2):
    """Quick check: can we open a connection to a public DNS server?"""
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            socket.create_connection((host, 53), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


def http_get(url):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urlopen(request, timeout=config.SEARCH_TIMEOUT) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read(2_000_000).decode(charset, errors="replace")


# ---------------------------------------------------------------- weather
def weather_place(text):
    """"weather in Hyderabad today" -> "Hyderabad"; "" means "where I am"."""
    m = PLACE.search(text.strip())
    if not m:
        return ""
    place = re.sub(r"^(the )", "", m.group(1).strip(), flags=re.IGNORECASE)
    return "" if place.lower() in ("here", "my area", "my city", "outside", "the moment") else place.title()


# WMO weather codes (used by Open-Meteo) -> words.
WMO_CODES = [
    ((0,), "clear"), ((1,), "mostly clear"), ((2,), "partly cloudy"), ((3,), "cloudy"),
    ((45, 48), "foggy"), (range(51, 58), "drizzling"), (range(61, 68), "raining"),
    (range(71, 78), "snowing"), (range(80, 83), "showery"), (range(85, 87), "snowing"),
    (range(95, 100), "stormy"),
]


def wmo_condition(code):
    for codes, words in WMO_CODES:
        if code in codes:
            return words
    return "changeable"


def open_meteo_weather(place):
    """Weather from Open-Meteo (free, no key). The place is looked up by name,
    preferring config.HOME_COUNTRY, so "Hyderabad" is the one in India."""
    found = json.loads(http_get(
        "https://geocoding-api.open-meteo.com/v1/search?count=5&language=en&format=json&name=" + quote(place)
    )).get("results") or []
    if not found:
        raise LookupError(f"no place called {place!r}")
    spot = next((r for r in found if r.get("country_code") == config.HOME_COUNTRY), found[0])
    data = json.loads(http_get(
        f"https://api.open-meteo.com/v1/forecast?latitude={spot['latitude']}&longitude={spot['longitude']}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&timezone=auto&forecast_days=2"
    ))
    now, daily = data["current"], data["daily"]

    def day(i):
        return {"max": round(daily["temperature_2m_max"][i]), "min": round(daily["temperature_2m_min"][i]),
                "rain": round(daily["precipitation_probability_max"][i] or 0)}

    return {"place": spot["name"], "temp": round(now["temperature_2m"]),
            "feels": round(now["apparent_temperature"]), "humidity": round(now["relative_humidity_2m"]),
            "condition": wmo_condition(now["weather_code"]), "today": day(0), "tomorrow": day(1)}


def wttr_weather(place):
    """Backup: wttr.in."""
    report = json.loads(http_get(f"https://wttr.in/{quote(place)}?format=j1"))
    now = report["current_condition"][0]

    def day(d):
        return {"max": int(d["maxtempC"]), "min": int(d["mintempC"]),
                "rain": max(int(h.get("chanceofrain", 0)) for h in d["hourly"])}

    days = report["weather"]
    return {"place": place, "temp": int(now["temp_C"]), "feels": int(now["FeelsLikeC"]),
            "humidity": int(now["humidity"]), "condition": now["weatherDesc"][0]["value"].strip().lower(),
            "today": day(days[0]), "tomorrow": day(days[1] if len(days) > 1 else days[0])}


def get_weather(place):
    """Current weather and a 2-day outlook as a dict; "" = config.HOME_CITY."""
    place = place or config.HOME_CITY
    try:
        return open_meteo_weather(place)
    except Exception as e:
        log.warning("Open-Meteo failed (%s); trying wttr.in", e)
        return wttr_weather(place)


def weather_reply(weather, tomorrow=False):
    """Turns get_weather()'s dict into spoken sentences."""
    day = weather["tomorrow" if tomorrow else "today"]
    outlook = (f"a high of {day['max']} and a low of {day['min']} degrees, "
               f"with a {day['rain']} percent chance of rain")
    if tomorrow:
        return f"Tomorrow in {weather['place']}, expect {outlook}."
    return (f"Right now in {weather['place']} it's {weather['condition']} and {weather['temp']} degrees, "
            f"feels like {weather['feels']}, humidity {weather['humidity']} percent. "
            f"Today, {outlook}.")


# ---------------------------------------------------------------- web search
def _ddgs():
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS  # the package's older name
    return DDGS(timeout=config.SEARCH_TIMEOUT)


def web_search(query, max_results):
    """Returns a list of {"title", "body", "href"} dicts."""
    return list(_ddgs().text(query, max_results=max_results) or [])


def news_search(query, max_results):
    """Headlines from the past week, newest first, as {"title", "body", "href",
    "date", "source"} dicts. Live-score and news sites are JavaScript pages
    that can't be read, but their headlines carry the facts."""
    results = list(_ddgs().news(query, max_results=max_results, timelimit="w") or [])
    for r in results:
        r.setdefault("href", r.get("url", ""))
    return results


class _TextExtractor(HTMLParser):
    """Collects the visible text of a page, one line per block (paragraph,
    table row, list item). Inline tags don't break lines, so a price inside
    <span> stays in its sentence: "Gold stands at <b>Rs 12,450</b> per gram"."""

    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form", "button", "select"}
    BLOCK = {"p", "div", "tr", "li", "ul", "ol", "table", "section", "article", "br",
             "h1", "h2", "h3", "h4", "h5", "h6", "header", "main", "aside", "dd", "dt", "blockquote"}

    def __init__(self):
        super().__init__()
        self.lines = []
        self._current = []
        self._skipping = 0

    def _flush(self):
        text = " ".join(" ".join(self._current).split())
        self._current = []
        if len(text) > 2:
            self.lines.append(text)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skipping += 1
        elif tag in self.BLOCK:
            self._flush()

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self._flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skipping:
            self._skipping -= 1
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self._skipping:
            self._current.append(data)

    def close(self):
        super().close()
        self._flush()


def relevant_text(html, query, max_chars):
    """The lines of a page most likely to answer `query`: lines sharing words
    with the query, preferring those that also contain numbers."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    words = keywords(query)
    scored = []
    for i, line in enumerate(dict.fromkeys(parser.lines)):
        lowered = line.lower()
        hits = sum(1 for w in words if w in lowered)
        if not hits or len(line) > 400:
            continue
        has_number = bool(re.search(r"\d", line))
        scored.append((hits + 2 * has_number, i, line))
    scored.sort(key=lambda s: -s[0])
    picked, total = [], 0
    for _, i, line in scored:
        if total + len(line) > max_chars:
            break
        picked.append((i, line))
        total += len(line)
    return " | ".join(line for _, line in sorted(picked))


def fetch_page_text(url, query):
    return relevant_text(http_get(url), query, config.PAGE_TEXT_CHARS)


# ---------------------------------------------------------------- agent
class Researcher(Agent):
    name = "researcher"

    def __init__(self, llm, db, search=web_search, online=is_online,
                 fetch_page=fetch_page_text, weather=get_weather, news=news_search):
        super().__init__(llm, db)
        self._search = search
        self._news = news
        self._online = online
        self._fetch_page = fetch_page
        self._weather = weather

    def handle(self, text):
        with leds.status.internet():  # yellow LED while using the internet
            if not self._online():
                return OFFLINE_REPLY
            if WEATHER.search(text):
                reply = self.weather(text)
                if reply:
                    return reply
            return self.search(search_query(text))

    def weather(self, text):
        place = weather_place(text)
        log.info("Weather lookup: %s", place or config.HOME_CITY)
        try:
            reply = weather_reply(self._weather(place), tomorrow="tomorrow" in text.lower())
        except Exception as e:
            log.warning("Weather services failed (%s); falling back to web search", e)
            return None
        log.info("Weather: %s", reply)
        return reply

    def find(self, query):
        """News headlines for "what's happening" questions, else web results."""
        if NEWSY.search(query):
            try:
                results = self._news(query, config.SEARCH_RESULTS)
                if results:
                    log.info("Searching news: %s", query)
                    return results, True
            except Exception as e:
                log.warning("News search failed (%s); using web search", e)
        log.info("Searching: %s", query)
        return self._search(query, config.SEARCH_RESULTS), False

    def search(self, query):
        try:
            results, is_news = self.find(query)
        except ImportError:
            log.error("Web search needs the 'ddgs' package: pip install ddgs")
            return "Web search isn't installed on me yet."
        except Exception as e:
            log.error("Search failed: %s", e)
            return "The web search failed, so I can't look that up right now."
        if not results:
            log.info("No results")
            return NOT_FOUND_REPLY

        sources = []
        for n, r in enumerate(results, 1):
            title, url = r.get("title", ""), r.get("href", "")
            body = r.get("body", "")[:config.SNIPPET_CHARS]  # some snippets are whole pages
            dated = f" ({r['date'][:10]}, {r.get('source', '')})" if r.get("date") else ""
            log.info("Result %d%s: %s <%s>\n    %s", n, dated, title, url, body)
            page = ""
            if not is_news and n <= config.PAGES_TO_READ and url:
                try:
                    page = self._fetch_page(url, query)
                    log.info("Page %d text: %s", n, page or "(nothing relevant)")
                except Exception as e:
                    log.info("Page %d unreadable: %s", n, e)
            sources.append(f"[{n}]{dated} {title}\n{body}" + (f"\nFrom the page: {page}" if page else ""))

        today = datetime.now().strftime("%A, %B %d, %Y")
        reply = self.llm.chat(SYSTEM_PROMPT.format(today=today),
                              f"Question: {query}\n\nWeb results:\n\n" + "\n\n".join(sources),
                              temperature=config.RESEARCHER_TEMPERATURE, max_tokens=120)
        if not reply:
            return "I found some results, but my language model is not responding."
        # Drop any sentence stating a number the results don't contain.
        grounded, _ = keep_supported(reply, query, today, *sources)
        return grounded or NOT_FOUND_REPLY
