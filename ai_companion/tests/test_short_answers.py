"""One-word answers ("sleep", "rabbit", "yes", "no", "3") and the wake reply."""

from types import SimpleNamespace

import pytest

import config
from audio import is_confident
from main import Companion, State


def segment(text, logprob=-0.3, no_speech=0.1, compression=1.5):
    return SimpleNamespace(text=text, avg_logprob=logprob, no_speech_prob=no_speech,
                           compression_ratio=compression)


# ---------------------------------------------------------------- speech filter
@pytest.mark.parametrize("text,logprob,no_speech", [
    ("Sleep.", -1.2, 0.3),
    ("Rabbit.", -1.3, 0.2),
    ("Yes.", -1.4, 0.4),
    ("No.", -1.1, 0.35),
    ("3", -1.2, 0.3),
    ("Three.", -1.45, 0.5),
    ("Thank you.", -0.3, 0.05),   # clearly said
])
def test_one_word_answers_are_kept(text, logprob, no_speech):
    assert is_confident(segment(text, logprob=logprob, no_speech=no_speech))


@pytest.mark.parametrize("text,logprob,no_speech", [
    ("you", -0.9, 0.2),           # what Whisper "hears" in silence
    ("Thank you.", -1.2, 0.4),
    ("Bye.", -1.0, 0.35),
    ("Yes.", -1.8, 0.3),          # too unsure even for one word
    ("No.", -0.5, 0.7),           # probably not speech at all
    ("", -0.1, 0.1),
])
def test_noise_still_needs_confidence(text, logprob, no_speech):
    assert not is_confident(segment(text, logprob=logprob, no_speech=no_speech))


def test_sentences_keep_the_stricter_bar():
    assert not is_confident(segment("Manti Web baby.", logprob=-1.2))
    assert is_confident(segment("What is the capital of Kerala?", logprob=-0.8))
    assert not is_confident(segment("thank you thank you thank you", compression=2.8))


# ---------------------------------------------------------------- one word, end to end
def make(state=State.SLEEP):
    said, asked = [], []
    companion = Companion(lambda t: asked.append(t) or "ok", said.append, state=state)
    return companion, said, asked


@pytest.mark.parametrize("text", ["Rabbit.", "Hello Rabbit.", "Hey rabbit"])
def test_wake_reply(text):
    companion, said, _ = make()
    companion.on_text(text)
    assert said == ["Yes? How can I help you?"] and companion.state is State.RUNNING


def test_wake_reply_while_awake():
    companion, said, asked = make(State.RUNNING)
    companion.on_text("Hello Rabbit")
    assert said == [config.WAKE_REPLY] and asked == []


@pytest.mark.parametrize("text", ["Yes.", "No.", "3", "Three."])
def test_single_words_are_answered_when_awake(text):
    companion, said, asked = make(State.RUNNING)
    companion.on_text(text)
    assert asked == [text]


def test_single_word_sleep():
    companion, said, _ = make(State.RUNNING)
    companion.on_text("Sleep.")
    assert companion.state is State.SLEEP and said == ["Going to sleep."]


def test_wake_reply_is_ready_to_play_instantly():
    assert config.WAKE_REPLY in config.TTS_CACHED_PHRASES
