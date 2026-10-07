"""Researcher: looks things up online. The only agent that needs internet.

Weather questions go to wttr.in, which returns real measurements. Everything
else goes to DuckDuckGo. Search results only carry a short page description
("Live gold prices in multiple currencies"), so the text of the top pages is
read too, which is where the actual numbers are.
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
from agents.base import Agent
from db import keywords

log = logging.getLogger("researcher")

SYSTEM_PROMPT = """You answer questions using web results that were fetched from the internet a moment ago, so they are current.
Today is {today}.
Answer in at most two short sentences and give the actual figures found in the results (prices, scores, dates, names).
Use only the results. If they really don't contain the answer, say you couldn't find it.
Never say that you can't access real-time information: these results are real-time.
Your reply is spoken aloud: no lists, no links, no markdown."""

LEADING = re.compile(
    r"^(please |can you |could you )?(search( the (web|internet))?( for)?|google|look up|find out"
    r"|browse( for)?|check( on)?|tell me)\s*",
    re.IGNORECASE,
)
OFFLINE_REPLY = "I can't search the web right now because I'm not connected to the internet."
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


def get_weather(place):
    """wttr.in's JSON report; place "" = located by the Pi's IP address."""
    return json.loads(http_get(f"https://wttr.in/{quote(place)}?format=j1"))


def weather_reply(report, place, tomorrow=False):
    """Turns a wttr.in report into a spoken sentence."""
    if not place:
        try:
            place = report["nearest_area"][0]["areaName"][0]["value"]
        except (KeyError, IndexError):
            place = "your area"
    days = report["weather"]
    day = days[1] if tomorrow and len(days) > 1 else days[0]
    rain = max(int(h.get("chanceofrain", 0)) for h in day["hourly"])
    outlook = (f"a high of {day['maxtempC']} and a low of {day['mintempC']} degrees, "
               f"with a {rain} percent chance of rain")
    if tomorrow:
        return f"Tomorrow in {place}, expect {outlook}."
    now = report["current_condition"][0]
    condition = now["weatherDesc"][0]["value"].strip().lower()
    return (f"Right now in {place} it's {condition} and {now['temp_C']} degrees, "
            f"feels like {now['FeelsLikeC']}, humidity {now['humidity']} percent. "
            f"Today, {outlook}.")


# ---------------------------------------------------------------- web search
def web_search(query, max_results):
    """Returns a list of {"title", "body", "href"} dicts."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS  # the package's older name
    return list(DDGS(timeout=config.SEARCH_TIMEOUT).text(query, max_results=max_results) or [])


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
                 fetch_page=fetch_page_text, weather=get_weather):
        super().__init__(llm, db)
        self._search = search
        self._online = online
        self._fetch_page = fetch_page
        self._weather = weather

    def handle(self, text):
        if not self._online():
            return OFFLINE_REPLY
        if WEATHER.search(text):
            reply = self.weather(text)
            if reply:
                return reply
        return self.search(search_query(text))

    def weather(self, text):
        place = weather_place(text)
        log.info("Weather lookup: %s", place or "(current location)")
        try:
            reply = weather_reply(self._weather(place), place, tomorrow="tomorrow" in text.lower())
        except Exception as e:
            log.warning("Weather service failed (%s); falling back to web search", e)
            return None
        log.info("Weather: %s", reply)
        return reply

    def search(self, query):
        log.info("Searching: %s", query)
        try:
            results = self._search(query, config.SEARCH_RESULTS)
        except ImportError:
            log.error("Web search needs the 'ddgs' package: pip install ddgs")
            return "Web search isn't installed on me yet."
        except Exception as e:
            log.error("Search failed: %s", e)
            return "The web search failed, so I can't look that up right now."
        if not results:
            log.info("No results")
            return f"I couldn't find anything about {query}."

        sources = []
        for n, r in enumerate(results, 1):
            title, body, url = r.get("title", ""), r.get("body", ""), r.get("href", "")
            log.info("Result %d: %s <%s>\n    %s", n, title, url, body)
            page = ""
            if n <= config.PAGES_TO_READ and url:
                try:
                    page = self._fetch_page(url, query)
                    log.info("Page %d text: %s", n, page or "(nothing relevant)")
                except Exception as e:
                    log.info("Page %d unreadable: %s", n, e)
            sources.append(f"[{n}] {title}\n{body}" + (f"\nFrom the page: {page}" if page else ""))

        prompt = SYSTEM_PROMPT.format(today=datetime.now().strftime("%A, %B %d, %Y"))
        reply = self.llm.chat(prompt, f"Question: {query}\n\nWeb results:\n\n" + "\n\n".join(sources))
        return reply or "I found some results, but my language model is not responding."
