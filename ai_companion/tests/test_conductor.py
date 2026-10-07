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


def make_conductor(db, llm):
    agents = {label: Echo(label) for label in ("schedule", "search", "remember", "answer")}
    return Conductor(llm, db, agents), agents


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
