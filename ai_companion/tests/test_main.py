"""The SLEEP / RUNNING / ABORTED state machine."""

import time

import config
from main import Companion, State, has_exit_word, is_sleep_command, split_on_wake_word, to_words


def make():
    said, asked = [], []

    def answer(text):
        asked.append(text)
        return f"answer to {text}"

    return Companion(answer, said.append), said, asked


def test_keywords():
    assert has_exit_word(to_words("May day!"))
    assert split_on_wake_word(to_words("Hey Rabbit, what time is it?")) == (True, ["what", "time", "is", "it"])
    assert is_sleep_command(to_words("Go to sleep."))
    assert not is_sleep_command(to_words("how many hours should I sleep at night"))


def test_sleep_ignores_everything_but_wake_word():
    companion, said, asked = make()
    companion.on_text("what is the time")
    assert companion.state is State.SLEEP and said == [] and asked == []


def test_wake_then_question_then_sleep():
    companion, said, asked = make()
    companion.on_text("Rabbit")
    assert companion.state is State.RUNNING and said == ["Yes?"]
    companion.on_text("what is two plus two")
    assert asked == ["what is two plus two"]
    companion.on_text("sleep")
    assert companion.state is State.SLEEP


def test_wake_word_with_question_answers_right_away():
    companion, said, asked = make()
    companion.on_text("Rabbit, what is the capital of India?")
    assert asked == ["what is the capital of india"]


def test_mayday_from_any_state():
    companion, said, _ = make()
    companion.on_text("mayday")
    assert companion.state is State.ABORTED and said == ["Shutting down."]


def test_silence_timeout():
    companion, said, _ = make()
    companion.on_text("rabbit")
    companion.last_activity = time.monotonic() - config.SILENCE_TIMEOUT
    companion.check_timeout()
    assert companion.state is State.SLEEP


def test_wake_word_while_running_is_not_sent_to_the_llm():
    companion, said, asked = make()
    companion.on_text("rabbit")
    companion.on_text("rabbit")
    assert said == ["Yes?", "Yes?"] and asked == []
    companion.on_text("Rabbit, what time is it?")
    assert asked == ["what time is it"]
