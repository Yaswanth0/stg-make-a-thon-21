"""Archivist, Responder and Researcher, with a fake LLM."""

import json
from datetime import datetime, timedelta

from agents.archivist import Archivist, strip_command
from agents.base import to_second_person
from agents.researcher import (OFFLINE_REPLY, Researcher, relevant_text, search_query,
                               weather_place, weather_reply)
from agents.responder import Responder
from conftest import FakeLLM


def test_second_person():
    assert to_second_person("my locker code is 4521") == "your locker code is 4521"
    assert to_second_person("I parked on level 3") == "you parked on level 3"


def test_strip_command():
    assert strip_command("Remember that my locker code is 4521.") == "my locker code is 4521"
    assert strip_command("please note, I parked on level 3") == "I parked on level 3"


def test_archivist_saves_fact_and_reads_back(db):
    llm = FakeLLM([{"fact": "The user's locker code is 4521."}])
    reply = Archivist(llm, db).handle("Remember my locker code is 4521")
    assert reply == "Got it. I'll remember that your locker code is 4521."
    assert db.recent_memories(5) == ["The user's locker code is 4521."]


def test_archivist_keeps_user_words_if_llm_fails(db):
    Archivist(FakeLLM([None]), db).handle("remember the spare key is under the mat")
    assert db.recent_memories(5) == ["The user said: the spare key is under the mat."]


def test_responder_context_has_history_memories_and_reminders(db):
    db.add_turn("hi", "Hello!", "responder")
    db.add_turn("", "Reminder: drink water.", "scheduler")
    db.add_memory("The user's locker code is 4521.")
    db.add_memory("The user likes tea.")
    db.add_reminder("call mom", datetime.now() + timedelta(days=1))
    llm = FakeLLM(text_reply="It's 4521.")

    assert Responder(llm, db).handle("what's my locker code?") == "It's 4521."
    call = llm.calls[0]
    assert "The user's locker code is 4521." in call["system"]
    assert "likes tea" not in call["system"]
    assert "call mom, tomorrow at" in call["system"]
    assert call["history"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "assistant", "content": "Reminder: drink water."},
    ]


def test_responder_lists_facts_when_asked_about_me(db):
    db.add_memory("The user likes tea.")
    llm = FakeLLM()
    Responder(llm, db).handle("what do you know about me?")
    assert "likes tea" in llm.calls[0]["system"]


def test_search_query():
    assert search_query("Search for the price of a Raspberry Pi 5.") == "the price of a Raspberry Pi 5"
    assert search_query("look up cricket scores online") == "cricket scores"
    assert search_query("Check today's gold price.") == "today's gold price"


def researcher(llm, db, **fakes):
    fakes.setdefault("online", lambda: True)
    fakes.setdefault("search", lambda query, n: [])
    fakes.setdefault("fetch_page", lambda url, query: "")
    fakes.setdefault("weather", lambda place: WEATHER)
    fakes.setdefault("news", lambda query, n: [])
    return Researcher(llm, db, **fakes)


def test_researcher_offline(db):
    assert researcher(FakeLLM(), db, online=lambda: False).handle("search for news") == OFFLINE_REPLY


def test_researcher_reads_snippets_and_pages(db):
    results = [
        {"title": "Gold rate", "body": "Live gold prices in multiple currencies.", "href": "http://a"},
        {"title": "Gold news", "body": "Gold rises.", "href": "http://b"},
        {"title": "Third", "body": "Only a snippet.", "href": "http://c"},
    ]
    seen, fetched = {}, []

    def search(query, n):
        seen["query"] = query
        return results

    def fetch_page(url, query):
        fetched.append(url)
        return "24K gold: Rs 12,450 per gram" if url == "http://a" else ""

    llm = FakeLLM(text_reply="24 carat gold is 12,450 rupees per gram.")
    reply = researcher(llm, db, search=search, fetch_page=fetch_page).handle("Check today's gold price.")
    assert reply == "From a web search: 24 carat gold is 12,450 rupees per gram."
    assert seen["query"] == "today's gold price"
    assert fetched == ["http://a", "http://b"]  # PAGES_TO_READ = 2
    user = llm.calls[0]["user"]
    assert "Rs 12,450 per gram" in user and "Only a snippet." in user
    assert "real-time" in llm.calls[0]["system"]


def test_researcher_survives_unreadable_page(db):
    def fetch_page(url, query):
        raise OSError("403 Forbidden")

    results = [{"title": "T", "body": "B", "href": "http://a"}]
    llm = FakeLLM(text_reply="ok")
    assert researcher(llm, db, search=lambda q, n: results, fetch_page=fetch_page).handle("search x") == "From a web search: ok"


def test_researcher_search_failure(db):
    def broken(query, n):
        raise RuntimeError("rate limited")

    assert "failed" in researcher(FakeLLM(), db, search=broken).handle("search x")


# ---------------------------------------------------------------- weather
WEATHER = {
    "place": "Hyderabad", "temp": 23, "feels": 25, "humidity": 68, "condition": "clear",
    "today": {"max": 31, "min": 21, "rain": 20}, "tomorrow": {"max": 29, "min": 20, "rain": 70},
}


def test_weather_place():
    assert weather_place("Can you check today's weather in Hyderabad?") == "Hyderabad"
    assert weather_place("what's the temperature in new delhi right now") == "New Delhi"
    assert weather_place("will it rain in Chennai tomorrow") == "Chennai"
    assert weather_place("what's the weather like") == ""
    assert weather_place("how hot is it outside") == ""


def test_weather_reply():
    now = weather_reply(WEATHER)
    assert now.startswith("Right now in Hyderabad it's clear and 23 degrees")
    assert "high of 31" in now and "20 percent chance of rain" in now
    assert weather_reply(WEATHER, tomorrow=True) == (
        "Tomorrow in Hyderabad, expect a high of 29 and a low of 20 degrees, "
        "with a 70 percent chance of rain.")


def test_open_meteo_prefers_the_home_country(monkeypatch):
    import agents.researcher as researcher_module

    pages = {
        "geocoding": {"results": [
            {"name": "Hyderabad", "country_code": "PK", "latitude": 25.4, "longitude": 68.4},
            {"name": "Hyderabad", "country_code": "IN", "latitude": 17.4, "longitude": 78.5},
        ]},
        "forecast": {
            "current": {"temperature_2m": 28.4, "apparent_temperature": 29.4,
                        "relative_humidity_2m": 46, "weather_code": 0},
            "daily": {"temperature_2m_max": [33.6, 34.5], "temperature_2m_min": [24.5, 24.0],
                      "precipitation_probability_max": [0, 10]},
        },
    }
    asked = []

    def fake_get(url):
        asked.append(url)
        return json.dumps(pages["geocoding" if "geocoding" in url else "forecast"])

    monkeypatch.setattr(researcher_module, "http_get", fake_get)
    weather = researcher_module.open_meteo_weather("Hyderabad")
    assert "latitude=17.4" in asked[1]  # India, not Pakistan
    assert weather == {"place": "Hyderabad", "temp": 28, "feels": 29, "humidity": 46, "condition": "clear",
                       "today": {"max": 34, "min": 24, "rain": 0}, "tomorrow": {"max": 34, "min": 24, "rain": 10}}


def test_weather_falls_back_to_wttr(monkeypatch):
    import agents.researcher as researcher_module

    def down(place):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(researcher_module, "open_meteo_weather", down)
    monkeypatch.setattr(researcher_module, "wttr_weather", lambda place: dict(WEATHER, place=place))
    assert researcher_module.get_weather("")["place"] == "Hyderabad"  # config.HOME_CITY


def test_weather_question_skips_search_and_llm(db):
    places, llm = [], FakeLLM()

    def weather(place):
        places.append(place)
        return WEATHER

    reply = researcher(llm, db, weather=weather).handle("Can you check today's weather in Hyderabad?")
    assert places == ["Hyderabad"] and "23 degrees" in reply and llm.calls == []


def test_weather_failure_falls_back_to_search(db):
    def broken(place):
        raise OSError("timeout")

    results = [{"title": "Weather", "body": "Hyderabad 30C sunny", "href": ""}]
    llm = FakeLLM(text_reply="It's 30 degrees.")
    reply = researcher(llm, db, weather=broken, search=lambda q, n: results).handle("weather in Hyderabad")
    assert reply == "From a web search: It's 30 degrees."


def test_relevant_text_prefers_lines_with_numbers():
    html = """<html><head><title>x</title><script>var gold = 1;</script></head><body>
      <nav>Gold Silver Home</nav>
      <h1>Gold price today</h1>
      <p>Gold is a precious metal loved by many.</p>
      <table><tr><td>24K gold price per gram</td><td>Rs 12,450</td></tr></table>
      <p>Today's 22K gold price is Rs 11,410 per gram.</p>
      <p>Unrelated text about cars.</p>
    </body></html>"""
    text = relevant_text(html, "today's gold price", max_chars=80)
    assert "Rs 11,410" in text
    assert "var gold" not in text and "cars" not in text and "Silver Home" not in text


def test_relevant_text_keeps_inline_numbers_in_their_sentence():
    html = "<div><p>Today's gold price stands at <span>Rs 14,957</span> per gram.</p><div>Gold<br>Silver</div></div>"
    assert relevant_text(html, "gold price", max_chars=500) == "Today's gold price stands at Rs 14,957 per gram. | Gold"


def test_every_agent_knows_its_name_is_rabbit(db):
    import prompts

    for prompt in (prompts.RESPONDER, prompts.CONDUCTOR, prompts.SCHEDULER, prompts.ARCHIVIST, prompts.RESEARCHER):
        assert "Rabbit" in prompt
    llm = FakeLLM(text_reply="I'm Rabbit, your voice assistant.")
    Responder(llm, db).handle("what's your name?")
    assert "You are Rabbit" in llm.calls[0]["system"]
