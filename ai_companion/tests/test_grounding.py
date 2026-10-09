"""Hallucination guards: unsupported numbers are dropped, and questions about
the user are only answered from saved facts."""

import pytest

from agents.researcher import Researcher
from agents.responder import Responder, fact_to_speech, is_personal
from conftest import FakeLLM
from grounding import keep_supported, numbers


def test_numbers_are_normalised():
    assert numbers("Rs 14,957 at 6:45 PM, 32.80 degrees, 5.0 stars") == {"14957", "6:45", "32.8", "5"}


def test_supported_sentences_are_kept():
    reply, dropped = keep_supported("24 carat gold is ₹14,957 per gram.", "24K gold stands at ₹14,957 per gram")
    assert reply == "24 carat gold is ₹14,957 per gram." and dropped == []


def test_weather_log_replay_drops_the_invented_sunrise():
    """The real reply from the Pi's log: the sunrise/sunset times were in no result."""
    page = ("The weather in Hyderabad today is Sunny with a high of 32.8°C and a low of 22.6°C. | "
            "2. When is the sunrise and sunset in Hyderabad today?")
    reply = ("The current weather in Hyderabad is sunny, with a high of 32.8°C and a low of 22.6°C. "
             "The sunrise is at 6:45 AM and the sunset is at 6:22 PM today.")
    kept, dropped = keep_supported(reply, page)
    assert kept == "The current weather in Hyderabad is sunny, with a high of 32.8°C and a low of 22.6°C."
    assert dropped == ["The sunrise is at 6:45 AM and the sunset is at 6:22 PM today."]


def test_sentences_without_numbers_are_kept():
    kept, dropped = keep_supported("Paris is the capital of France. It has 2 airports.", "")
    assert kept == "Paris is the capital of France." and len(dropped) == 1


# ---------------------------------------------------------------- Researcher
def research(reply, results):
    llm = FakeLLM(text_reply=reply)
    researcher = Researcher(llm, None, search=lambda q, n: results, online=lambda: True,
                            fetch_page=lambda url, q: "", weather=None,
                            news=lambda q, n: [])
    return researcher.handle("search for the gold price"), llm


def test_researcher_drops_invented_figures():
    results = [{"title": "Gold", "body": "Gold is ₹14,957 per gram today.", "href": ""}]
    reply, llm = research("Gold is 14,957 rupees per gram. Silver is 98 rupees.", results)
    assert reply == "Gold is 14,957 rupees per gram."
    assert llm.calls[0]["temperature"] == 0.1


def test_researcher_with_nothing_grounded_says_so():
    results = [{"title": "Gold", "body": "Live gold rates in many currencies.", "href": ""}]
    reply, _ = research("Gold is 6,100 rupees per gram.", results)
    assert "couldn't find a reliable answer" in reply


# ---------------------------------------------------------------- Responder
@pytest.mark.parametrize("text,personal", [
    ("what's my locker code", True),
    ("where did I park my car", True),
    ("who is my doctor", True),
    ("do you remember my wifi password", True),
    ("what's my plan for tomorrow", False),        # reminders answer this
    ("what is the capital of France", False),
    ("what should I eat for dinner", False),
])
def test_is_personal(text, personal):
    assert is_personal(text) is personal


def test_unknown_personal_fact_is_not_guessed(db):
    llm = FakeLLM(text_reply="Your locker code is 1234.")
    reply = Responder(llm, db).handle("what's my locker code?")
    assert "don't have that saved" in reply
    assert llm.calls == []  # never asked, so it can't invent one


def test_saved_fact_is_answered(db):
    db.add_memory("The user's locker code is 4521.")
    llm = FakeLLM(text_reply="Your locker code is 4521.")
    assert Responder(llm, db).handle("what's my locker code?") == "Your locker code is 4521."
    assert llm.calls[0]["temperature"] == 0.3


def test_wrong_number_about_the_user_is_replaced_by_the_fact(db):
    db.add_memory("The user's locker code is 4521.")
    llm = FakeLLM(text_reply="Your locker code is 4512.")
    assert Responder(llm, db).handle("what's my locker code?") == (
        "Here's what I have saved: Your locker code is 4521.")


def test_fact_to_speech():
    assert fact_to_speech("The user's sister Priya has her birthday on May 4th.") == (
        "Your sister Priya has her birthday on May 4th.")
    assert fact_to_speech("The user parked on level 3.") == "You parked on level 3."
