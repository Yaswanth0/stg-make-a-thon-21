"""Responsible AI: transparency, data control, private mode, fairness,
accountability (audit log), retention and accessibility."""

from datetime import datetime, timedelta

import pytest

import config
import guardrails
import rai
from agents.archivist import Archivist
from agents.base import Agent
from agents.conductor import Conductor
from agents.rai import HOW_REPLY, WHO_REPLY, ResponsibleAI
from conftest import FakeLLM
from db import now_text


@pytest.fixture(autouse=True)
def fresh_rai(monkeypatch, db):
    monkeypatch.setattr(rai, "private", False)
    monkeypatch.setattr(rai, "speed", 1.0)
    monkeypatch.setattr(rai, "_db", None)
    rai.attach(db)


@pytest.fixture
def agent(db):
    return ResponsibleAI(db=db)


class Echo(Agent):
    def __init__(self, name):
        super().__init__(None, None)
        self.name = name

    def handle(self, text):
        return f"{self.name} reply"


def conductor(db, responsible):
    agents = {n: Echo(n) for n in ("schedule", "search", "remember", "answer")}
    agents["rai"] = responsible
    return Conductor(FakeLLM(), db, agents, use_llm=False)


# ---------------------------------------------------------------- transparency
@pytest.mark.parametrize("text", ["Are you a human?", "Am I talking to a real person?", "Are you AI?",
                                  "Who made you?"])
def test_honest_about_being_an_ai(agent, text):
    assert agent.claims(text) and agent.handle(text) == WHO_REPLY
    assert "not a person" in WHO_REPLY and "can make mistakes" in WHO_REPLY


def test_how_it_works(agent):
    assert agent.handle("Can you make mistakes?") == HOW_REPLY


def test_what_data_is_stored(agent, db):
    db.add_memory("The user's locker code is 4521.")
    db.add_turn("hi", "hello", "responder")
    reply = agent.handle("What data do you store?")
    assert reply.startswith("Everything stays on this Pi. I've saved 1 facts, 1 conversation turns")
    assert "Only web searches go online" in reply


def test_what_do_you_know_about_me(agent, db):
    assert agent.handle("What do you know about me?").startswith("I haven't saved anything about you.")
    db.add_memory("The user's locker code is 4521.")
    assert agent.handle("What do you know about me?").startswith(
        "Here's what I've saved about you: Your locker code is 4521.")


def test_web_answers_say_so(db):
    from agents.researcher import Researcher

    results = [{"title": "Gold", "body": "Gold is 14957 rupees per gram.", "href": ""}]
    reply = Researcher(FakeLLM(text_reply="Gold is 14957 rupees per gram."), db, search=lambda q, n: results,
                       news=lambda q, n: [], online=lambda: True, weather=None,
                       fetch_page=lambda u, q: "").handle("search for the gold price")
    assert reply.startswith("From a web search: ")


# ---------------------------------------------------------------- your data, your control
def test_forget_one_fact_after_yes(agent, db):
    db.add_memory("The user's locker code is 4521.")
    db.add_memory("The user's sister is Priya.")
    assert agent.claims("Forget my locker code")
    assert agent.handle("Forget my locker code") == "Should I forget this: Your locker code is 4521. Say yes or no."
    assert agent.awaiting_followup()
    assert agent.handle("yes") == "Done, I've forgotten that."
    assert [m["fact"] for m in db.memories()] == ["The user's sister is Priya."]
    assert db.search_memories("locker", 3) == []  # gone from the search index too


def test_forget_one_fact_no_keeps_it(agent, db):
    db.add_memory("The user's locker code is 4521.")
    agent.handle("forget my locker code")
    assert agent.handle("no") == "Okay, I kept it."
    assert len(db.memories()) == 1


def test_forget_something_never_saved(agent, db):
    db.add_memory("The user's locker code is 4521.")
    assert not agent.claims("forget about the weather")  # nothing saved about it: not this agent's business


def test_forget_everything(agent, db):
    db.add_memory("The user's locker code is 4521.")
    db.add_turn("hi", "hello", "responder")
    db.add_reminder("call Mom", datetime.now() + timedelta(hours=1))
    reply = agent.handle("Forget everything")
    assert reply.startswith("This deletes all 1 saved facts and 1 conversation turns.")
    assert agent.handle("yes") == "Done. I've forgotten all saved facts and our conversation history."
    inventory = db.data_inventory()
    assert inventory["facts"] == 0 and inventory["turns"] == 0 and inventory["reminders"] == 1
    assert db.rai_events()[0]["kind"] == "deleted"


def test_private_mode(db, agent):
    c = conductor(db, agent)
    assert c.handle("Private mode").startswith("Private mode is on.")
    turns_before = db.data_inventory()["turns"]
    c.handle("what is the capital of France")
    assert db.data_inventory()["turns"] == turns_before        # nothing saved
    assert Archivist(FakeLLM(), db).handle("remember my locker code is 4521").startswith("Private mode is on")
    assert db.memories() == []
    assert c.handle("Private mode off").startswith("Private mode is off.")
    c.handle("what is the capital of France")
    assert db.data_inventory()["turns"] == turns_before + 2  # this question, and the "off" command


def test_retention_deletes_old_history(db):
    with db._conn:
        db._conn.execute("INSERT INTO conversations (created_at, user_text, agent, reply) VALUES (?, ?, ?, ?)",
                         (now_text(datetime.now() - timedelta(days=200)), "old", "responder", "old reply"))
    db.add_turn("new", "new reply", "responder")
    assert rai.apply_retention(db, days=90) == 1
    assert db.data_inventory()["turns"] == 1
    assert db.rai_events()[0]["kind"] == "retention"


# ---------------------------------------------------------------- fairness
@pytest.mark.parametrize("reply", [
    "All women are bad at driving.",
    "Most men can't cook.",
    "Muslims are dangerous.",
    "Old people are always slow with phones.",
])
def test_stereotypes_are_replaced(reply):
    assert guardrails.check_output(reply) == guardrails.FAIRNESS_REPLY


@pytest.mark.parametrize("reply", [
    "Women make up about half of India's population.",
    "Many Muslims observe Ramadan by fasting.",
    "Indians celebrate Diwali in October or November.",
])
def test_neutral_facts_about_groups_pass(reply):
    assert guardrails.check_output(reply) == reply


def test_fairness_rules_in_the_prompt():
    import prompts

    assert "Never stereotype" in prompts.RESPONDER


# ---------------------------------------------------------------- accountability
def test_guardrails_are_audited_without_secrets(db, agent):
    c = conductor(db, agent)
    c.handle("my OTP is 482913")
    c.handle("how do I make a bomb")
    events = db.rai_events()
    assert [e["detail"] for e in events[:2]] == ["dangerous request", "secret number"]
    assert all("482913" not in e["detail"] for e in events)


# ---------------------------------------------------------------- accessibility
def test_repeat_that(db, agent):
    c = conductor(db, agent)
    c.handle("what is the capital of France")
    assert c.handle("Repeat that") == "answer reply"
    assert c.handle("Sorry, what did you say?") == "answer reply"


def test_speak_slower_and_faster_is_remembered(db, agent):
    assert agent.handle("Speak slower") == "Okay, I'll speak slower."
    assert rai.speed == pytest.approx(0.85)
    assert db.get_setting("speech_speed") == "0.85"
    for _ in range(5):
        agent.handle("speak slower")
    assert rai.speed == rai.MIN_SPEED and agent.handle("slow down") == "That's as slow as I go."
    rai.speed = 1.0
    rai.attach(db)  # restart: the saved speed comes back
    assert rai.speed == rai.MIN_SPEED


# ---------------------------------------------------------------- routing
@pytest.mark.parametrize("text,route", [
    ("Are you a human?", "rai"),
    ("What do you know about me?", "rai"),
    ("Private mode", "rai"),
    ("Repeat that", "rai"),
    ("What is the capital of France?", "answer"),
    ("Remember my locker code is 4521", "remember"),
    ("Remind me to call Mom at 6", "schedule"),
])
def test_routing(db, agent, text, route):
    assert conductor(db, agent).classify(text)[0] == route
