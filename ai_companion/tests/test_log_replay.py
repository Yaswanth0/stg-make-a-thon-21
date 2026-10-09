"""Situations from a real session log on the Pi, replayed."""

from types import SimpleNamespace

import pytest

from agents.base import Agent
from agents.conductor import Conductor, shortcut_label
from agents.researcher import Researcher
from agents.responder import CANT_SEARCH_REPLY, Responder
from audio import is_confident, is_repetitive
from conftest import FakeLLM


class Echo(Agent):
    def __init__(self, name, reply=None):
        super().__init__(None, None)
        self.name, self.reply, self.got = name, reply, []

    def handle(self, text):
        self.got.append(text)
        return self.reply or f"{self.name}: {text}"


def conductor(db, answer_reply=None):
    agents = {"schedule": Echo("schedule"), "search": Echo("search"), "remember": Echo("remember"),
              "answer": Echo("answer", answer_reply)}
    return Conductor(FakeLLM(), db, agents, use_llm=False), agents


# ---------------------------------------------------------------- "search it" follow-ups
@pytest.mark.parametrize("followup", [
    "Searching Internet.", "search it", "Search again.", "look it up online",
    "I think what you said 43 is wrong answer. Can you search again?",
])
def test_search_followup_searches_the_previous_question(db, followup):
    c, agents = conductor(db)
    c.handle("How many runs did Virat Kohli score in total?")
    c.handle(followup)
    assert agents["search"].got[-1] == "How many runs did Virat Kohli score in total?"


def test_yes_after_offer_to_search(db):
    c, agents = conductor(db, answer_reply="I'm not sure. Say search it and I'll look it up online.")
    c.handle("Give me the recipe of chicken tikka masala")
    c.handle("Yes, go ahead. Let's go ahead.")
    assert agents["search"].got == ["Give me the recipe of chicken tikka masala"]


def test_yes_without_an_offer_is_just_an_answer(db):
    c, agents = conductor(db, answer_reply="Paris is the capital of France.")
    c.handle("what is the capital of France")
    c.handle("yes")
    assert agents["search"].got == [] and agents["answer"].got[-1] == "yes"


def test_new_search_replaces_the_previous_question(db):
    c, agents = conductor(db)
    c.handle("search for the gold price")
    c.handle("search again")
    assert agents["search"].got == ["search for the gold price", "search for the gold price"]


# ---------------------------------------------------------------- Responder can't search
@pytest.mark.parametrize("reply", [
    "I found that Virat Kohli has scored 43 centuries in international cricket.",
    "I've searched again, but I'm not sure what the correct number is.",
    "According to my search, it is 50.",
])
def test_responder_never_claims_to_have_searched(db, reply):
    assert Responder(FakeLLM(text_reply=reply), db).handle("how many centuries") == CANT_SEARCH_REPLY


def test_responder_answers_normally(db):
    reply = "Marinate chicken in yogurt and spices, grill it, then simmer in a tomato and cream sauce."
    assert Responder(FakeLLM(text_reply=reply), db).handle("recipe for chicken tikka masala") == reply


# ---------------------------------------------------------------- routing
@pytest.mark.parametrize("text", [
    "How many centuries did Virat Kohli score in total?",
    "What is the present India score against West Indies cricket?",
    "who is the prime minister of India",
])
def test_sports_and_public_figures_go_to_search(text):
    assert shortcut_label(text) == "search"


# ---------------------------------------------------------------- news for scores
def test_score_questions_use_news_headlines(db):
    news = [{"title": "India beat West Indies by 5 wickets", "body": "India chased 187 in 18.2 overs.",
             "url": "https://news.example/ind-wi", "href": "https://news.example/ind-wi",
             "date": "2026-10-09T18:00:00", "source": "Cricbuzz"}]
    llm = FakeLLM(text_reply="India beat West Indies by 5 wickets, chasing 187 in 18.2 overs.")
    fetched = []
    researcher = Researcher(llm, db, search=lambda q, n: pytest.fail("used web search"),
                            news=lambda q, n: news, online=lambda: True, weather=None,
                            fetch_page=lambda url, q: fetched.append(url) or "")
    reply = researcher.handle("What is the present India score against West Indies cricket?")
    assert reply == "India beat West Indies by 5 wickets, chasing 187 in 18.2 overs."
    assert fetched == []  # news pages aren't read
    assert "2026-10-09, Cricbuzz" in llm.calls[0]["user"]


def test_long_snippets_are_trimmed(db):
    huge = "Beavis and Butt-Head " * 200
    llm = FakeLLM(text_reply="ok")
    Researcher(llm, db, search=lambda q, n: [{"title": "T", "body": huge, "href": ""}], news=lambda q, n: [],
               online=lambda: True, weather=None, fetch_page=lambda u, q: "").handle("search for x")
    assert len(llm.calls[0]["user"]) < 600


# ---------------------------------------------------------------- misheard speech
def segment(text, logprob=-0.3, no_speech=0.1, compression=1.5):
    return SimpleNamespace(text=text, avg_logprob=logprob, no_speech_prob=no_speech,
                           compression_ratio=compression)


def test_unclear_speech_is_ignored():
    assert is_confident(segment("What is the capital of Kerala?"))
    assert not is_confident(segment("Manti Web baby.", logprob=-1.3))
    assert not is_confident(segment("thank you thank you thank you", compression=2.8))
    assert not is_confident(segment("you", no_speech=0.8))


def test_repetitive_speech_is_ignored():
    assert is_repetitive("Good. Good. Good. Good.")
    assert not is_repetitive("Good morning, Rabbit.")


# ---------------------------------------------------------------- "what is my age?"
@pytest.mark.parametrize("question", ["What is my age?", "how old am I?", "When was I born?"])
def test_age_is_found_though_the_fact_never_says_age(db, question):
    db.add_memory("The user is 24 years old.")  # exactly as saved on the Pi
    llm = FakeLLM(text_reply="You are 24 years old.")
    assert Responder(llm, db).handle(question) == "You are 24 years old."
    assert "The user is 24 years old." in llm.calls[0]["system"]


def test_synonyms_find_facts():
    from db import with_synonyms

    assert {"old", "years"} <= set(with_synonyms(["age"]))
    assert "number" in with_synonyms(["phone"])


def test_personal_question_with_no_match_sees_all_facts(db):
    db.add_memory("The user's employer is Infosys.")
    llm = FakeLLM(text_reply="You work at Infosys.")
    assert Responder(llm, db).handle("Where is my office?") == "You work at Infosys."


def test_wrong_age_is_still_caught(db):
    db.add_memory("The user is 24 years old.")
    llm = FakeLLM(text_reply="You are 25 years old.")
    assert Responder(llm, db).handle("What is my age?") == "Here's what I have saved: You are 24 years old."


def test_nothing_saved_at_all_still_says_so(db):
    llm = FakeLLM(text_reply="You are 30.")
    assert "don't have that saved" in Responder(llm, db).handle("What is my age?")
    assert llm.calls == []
