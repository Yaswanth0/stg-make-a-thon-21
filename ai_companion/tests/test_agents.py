"""Archivist, Responder and Researcher, with a fake LLM."""

from datetime import datetime, timedelta

from agents.archivist import Archivist, strip_command
from agents.base import to_second_person
from agents.researcher import OFFLINE_REPLY, Researcher, search_query
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


def test_researcher_offline(db):
    researcher = Researcher(FakeLLM(), db, search=None, online=lambda: False)
    assert researcher.handle("search for news") == OFFLINE_REPLY


def test_researcher_summarises_results(db):
    results = [{"title": "Pi 5", "body": "The Raspberry Pi 5 costs $60.", "href": "x"}]
    seen = {}

    def search(query, n):
        seen["query"] = query
        return results

    llm = FakeLLM(text_reply="It costs about 60 dollars.")
    reply = Researcher(llm, db, search=search, online=lambda: True).handle("search for the pi 5 price")
    assert reply == "It costs about 60 dollars."
    assert seen["query"] == "the pi 5 price"
    assert "costs $60" in llm.calls[0]["user"]


def test_researcher_search_failure(db):
    def broken(query, n):
        raise RuntimeError("rate limited")

    reply = Researcher(FakeLLM(), db, search=broken, online=lambda: True).handle("search x")
    assert "failed" in reply
