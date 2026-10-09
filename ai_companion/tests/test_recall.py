"""Older history: "what was the recipe you told me?", "what did we talk about
yesterday?"."""

from datetime import datetime, timedelta

import pytest

from agents.base import Agent
from agents.conductor import Conductor
from agents.responder import Responder
from conftest import FakeLLM
from db import Database, now_text
from recall import is_recall, period, topic_words

NOW = datetime.now()
YESTERDAY_EVENING = (NOW - timedelta(days=1)).replace(hour=21, minute=30, second=0, microsecond=0)
LAST_WEEK = NOW - timedelta(days=5)


def add_turn_at(db, when, user_text, reply, agent="responder"):
    """Saves a turn as if it happened at `when`."""
    with db._conn:
        cur = db._conn.execute(
            "INSERT INTO conversations (created_at, user_text, agent, reply) VALUES (?, ?, ?, ?)",
            (now_text(when), user_text, agent, reply))
        db._conn.execute("INSERT INTO conversations_fts (rowid, user_text, reply) VALUES (?, ?, ?)",
                         (cur.lastrowid, user_text, reply))


@pytest.fixture
def history(db):
    add_turn_at(db, LAST_WEEK, "what is the capital of Kerala", "Thiruvananthapuram is the capital of Kerala.")
    add_turn_at(db, YESTERDAY_EVENING, "give me the recipe of chicken tikka masala",
                "Marinate chicken in yogurt and spices, grill it, then simmer it in a tomato cream sauce.")
    add_turn_at(db, YESTERDAY_EVENING + timedelta(minutes=2), "check today's gold price",
                "24 carat gold is 14957 rupees per gram.", agent="researcher")
    add_turn_at(db, YESTERDAY_EVENING + timedelta(minutes=3), "Manti Web baby", "Sorry, I didn't catch that.")
    return db


# ---------------------------------------------------------------- spotting them
@pytest.mark.parametrize("text,recall", [
    ("What was the recipe you told me?", True),
    ("What did we talk about yesterday?", True),
    ("Remind me what you said about gold", True),
    ("What did I ask you last time?", True),
    ("Give me the recipe of chicken tikka masala", False),
    ("Who won the match yesterday?", False),        # yesterday alone isn't about our chats
    ("Remind me to call Mom at 6", False),
])
def test_is_recall(text, recall):
    assert is_recall(text) is recall


def test_topic_and_period():
    assert topic_words("What was the gold price you told me yesterday?") == ["gold", "price"]
    since, until, label = period("what did we talk about yesterday", NOW)
    assert label == "yesterday" and since <= YESTERDAY_EVENING < until
    assert period("what was the recipe you told me", NOW) is None


@pytest.mark.parametrize("text", ["What was the gold price you told me?", "Remind me what you said about gold"])
def test_recall_questions_go_to_the_responder(db, text):
    class Echo(Agent):
        def __init__(self, name):
            super().__init__(None, None)
            self.name = name

        def handle(self, t):
            return self.name

    agents = {n: Echo(n) for n in ("schedule", "search", "remember", "answer")}
    assert Conductor(FakeLLM(), db, agents, use_llm=False).handle(text) == "answer"


# ---------------------------------------------------------------- answering them
def test_recalls_by_topic(history):
    llm = FakeLLM(text_reply="Yesterday I said to marinate chicken in yogurt and spices, then grill and simmer it.")
    reply = Responder(llm, history).handle("What was the recipe you told me?")
    assert reply.startswith("Yesterday I said")
    system = llm.calls[0]["system"]
    assert "tikka masala" in system and "Kerala" not in system


def test_recalls_a_whole_day(history):
    llm = FakeLLM(text_reply="We talked about chicken tikka masala and the gold price.")
    Responder(llm, history).handle("What did we talk about yesterday?")
    system = llm.calls[0]["system"]
    assert "tikka masala" in system and "gold" in system
    assert "Kerala" not in system          # last week
    assert "Manti" not in system           # junk turn left out


def test_misquoted_number_falls_back_to_what_was_said(history):
    llm = FakeLLM(text_reply="I told you gold was 15200 rupees per gram.")
    reply = Responder(llm, history).handle("Remind me what you said about gold")
    assert "I said: 24 carat gold is 14957 rupees per gram." in reply
    assert "15200" not in reply


def test_nothing_found(history):
    llm = FakeLLM()
    assert Responder(llm, history).handle("What did you tell me about bitcoin?") == (
        "I don't remember us talking about that.")
    assert llm.calls == []


def test_turns_still_in_view_are_not_recalled_twice(db):
    add_turn_at(db, NOW - timedelta(days=1), "gold price", "Gold was 14000 rupees.")
    add_turn_at(db, NOW - timedelta(minutes=1), "gold price", "Gold is 14957 rupees.")
    skip = db.recent_turn_ids(2, 30 * 60)
    assert [t["reply"] for t in db.search_conversations(["gold"], 5, skip_ids=skip)] == ["Gold was 14000 rupees."]


def test_existing_history_is_indexed_on_upgrade(tmp_path):
    path = str(tmp_path / "old.db")
    old = Database(path)
    with old._conn:  # a database from before the conversation index existed
        old._conn.execute("INSERT INTO conversations (created_at, user_text, agent, reply) "
                          "VALUES ('2026-10-01 10:00:00', 'biryani recipe', 'responder', 'Layer rice and chicken.')")
        old._conn.execute("DROP TABLE conversations_fts")
    old.close()
    upgraded = Database(path)
    assert upgraded.search_conversations(["biryani"], 5)[0]["reply"] == "Layer rice and chicken."
    upgraded.close()
