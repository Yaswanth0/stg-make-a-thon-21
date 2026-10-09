import pytest

from agents.base import Agent
from agents.conductor import Conductor, shortcut_label
from conftest import FakeLLM
from tests.conductor_cases import CASES


@pytest.mark.parametrize("text,expected", CASES)
def test_shortcuts_are_never_wrong(text, expected):
    """A shortcut may pass a sentence on to the LLM, but must not mislabel it."""
    label = shortcut_label(text)
    assert label in (None, expected)


def test_shortcuts_cover_the_obvious_cases():
    hits = sum(1 for text, _ in CASES if shortcut_label(text))
    assert hits >= len(CASES) // 2


class Echo(Agent):
    def __init__(self, name):
        super().__init__(None, None)
        self.name = name
        self.followup = False

    def handle(self, text):
        return f"{self.name}: {text}"

    def awaiting_followup(self):
        return self.followup


def make_conductor(db, llm, use_llm=True):
    agents = {label: Echo(label) for label in ("schedule", "search", "remember", "answer")}
    return Conductor(llm, db, agents, use_llm=use_llm), agents


def test_llm_picks_agent_and_turn_is_stored(db):
    conductor, _ = make_conductor(db, FakeLLM([{"label": "search"}]))
    assert conductor.handle("who won the match") == "search: who won the match"
    assert db.recent_turns(5, 60) == [
        {"user_text": "who won the match", "agent": "search", "reply": "search: who won the match"}
    ]


@pytest.mark.parametrize("reply", [None, {}, {"label": "dance"}, {"agent": "search"}])
def test_bad_llm_output_falls_back_to_answer(db, reply):
    conductor, _ = make_conductor(db, FakeLLM([reply]))
    assert conductor.classify("hello there") == ("answer", "default")


def test_shortcut_skips_llm(db):
    llm = FakeLLM()
    conductor, _ = make_conductor(db, llm)
    assert conductor.classify("remind me to stretch in 5 minutes") == ("schedule", "shortcut")
    assert llm.calls == []


def test_followup_goes_to_same_agent(db):
    conductor, agents = make_conductor(db, FakeLLM())
    conductor.handle("remind me to stretch")
    agents["schedule"].followup = True
    assert conductor.handle("at 5 pm") == "schedule: at 5 pm"


def test_agent_crash_gives_spoken_error(db):
    conductor, agents = make_conductor(db, FakeLLM([{"label": "answer"}]))
    agents["answer"].handle = lambda text: 1 / 0
    assert "went wrong" in conductor.handle("hello")


@pytest.mark.parametrize("text,expected", [
    ("Check today's gold price", "search"),
    ("what is the price of a raspberry pi 5", "search"),
    ("what's the score in the india match", "search"),
    ("tell me the latest on the elections", "search"),
    ("what's the temperature in Hyderabad", "search"),
    ("wake me up at 7", "schedule"),
    ("set a timer for 10 minutes", "schedule"),
    ("remind me to check the price of gold", "schedule"),   # reminders win over search words
    ("remember the price of my bike was 90000", "remember"),
    ("check my reminders", "schedule"),
])
def test_new_shortcuts(text, expected):
    assert shortcut_label(text) == expected


@pytest.mark.parametrize("text", [
    "check if my locker code is saved", "what time is it right now", "what's today's date",
    "tell me a joke", "check my locker code",
])
def test_shortcuts_leave_ordinary_questions_alone(text):
    assert shortcut_label(text) in (None, "answer")


def test_without_llm_routing_unmatched_goes_to_responder(db):
    llm = FakeLLM([{"label": "search"}])
    conductor, _ = make_conductor(db, llm, use_llm=False)
    assert conductor.classify("what is the capital of France") == ("answer", "default")
    assert conductor.classify("search for pi 5") == ("search", "shortcut")
    assert llm.calls == []
