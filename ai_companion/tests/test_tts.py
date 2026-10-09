"""Text to speech: symbol cleanup, sentence chunks, the voice fallback order,
and playing one sentence while the next is synthesized."""

import pytest

import audio
import tts
from tts import EspeakTTS, clean_for_speech, make_tts, speech_chunks


@pytest.mark.parametrize("text,spoken", [
    ("Gold is ₹14,957 per gram.", "Gold is 14957 rupees per gram."),
    ("22k Gold ₹ 13,710/gm", "22k Gold 13710 rupees per gram"),
    ("It costs Rs. 2,35,000.", "It costs 235000 rupees."),
    ("A high of 32.8°C, humidity 68%.", "A high of 32.8 degrees Celsius, humidity 68 percent."),
    ("Wind at 7 km/h", "Wind at 7 kilometres per hour"),
    ("The Pi 5 costs $60.", "The Pi 5 costs 60 dollars."),
    ("**Paris** is the capital.", "Paris is the capital."),
    ("See https://example.com for more", "See for more"),
])
def test_clean_for_speech(text, spoken):
    assert clean_for_speech(text) == spoken


def test_speech_chunks():
    assert speech_chunks("Sure. The capital of France is Paris. It has about two million people.") == [
        "Sure. The capital of France is Paris.", "It has about two million people."]
    assert speech_chunks("Yes?") == ["Yes?"]
    assert speech_chunks("The weather is sunny today. Enjoy!") == ["The weather is sunny today. Enjoy!"]
    assert speech_chunks("") == []


# ---------------------------------------------------------------- fallback order
def fake_engines(monkeypatch, working):
    """Engines that load only if their name is in `working`."""
    def engine(name):
        class Engine:
            def __init__(self):
                if name not in working:
                    raise ImportError(f"No module named '{name}'")
                self.name = name
        return Engine

    monkeypatch.setattr(tts, "ENGINES", {name: engine(name) for name in ("kokoro", "piper", "espeak")})


@pytest.mark.parametrize("configured,working,chosen", [
    ("kokoro", {"kokoro", "piper", "espeak"}, "kokoro"),
    ("kokoro", {"piper", "espeak"}, "piper"),
    ("kokoro", {"espeak"}, "espeak"),
    ("piper", {"kokoro", "piper", "espeak"}, "piper"),
    ("espeak", {"kokoro", "piper", "espeak"}, "espeak"),
])
def test_fallback_order(monkeypatch, configured, working, chosen):
    fake_engines(monkeypatch, working)
    assert make_tts(configured).name == chosen


def test_espeak_uses_the_female_voice(monkeypatch):
    calls = []
    monkeypatch.setattr(tts.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    EspeakTTS().synthesize("Hello.", "/tmp/x.wav")
    assert calls[0][:3] == ["espeak-ng", "-v", "en-us+f3"]


# ---------------------------------------------------------------- Speaker
class FakePlayer:
    def __init__(self, log, wav):
        self.log, self.wav = log, wav
        log.append(("play", wav))

    def wait(self):
        self.log.append(("done", self.wav))

    def poll(self):
        return 0

    def terminate(self):
        pass


class RecordingTTS:
    name = "fake"

    def __init__(self, log):
        self.log = log

    def synthesize(self, text, wav_path):
        self.log.append(("synth", text, wav_path))


def make_speaker(monkeypatch):
    log = []
    monkeypatch.setattr(audio.subprocess, "Popen", lambda cmd: FakePlayer(log, cmd[1]))
    return audio.Speaker(tts=RecordingTTS(log), phrases=()), log


def test_next_sentence_is_synthesized_while_one_plays(monkeypatch):
    speaker, log = make_speaker(monkeypatch)
    a, b = speaker._wavs
    speaker.say("The capital of France is Paris. It has about two million people.")
    assert log == [
        ("synth", "The capital of France is Paris.", a),
        ("play", a),
        ("synth", "It has about two million people.", b),  # while the first plays
        ("done", a),
        ("play", b),
        ("done", b),
    ]


def test_mute_stops_the_remaining_sentences(monkeypatch):
    speaker, log = make_speaker(monkeypatch)
    original = FakePlayer.wait

    def wait_then_mute(self):
        original(self)
        speaker.mute()  # the switch goes OFF during the first sentence

    monkeypatch.setattr(FakePlayer, "wait", wait_then_mute)
    speaker.say("The capital of France is Paris. It has about two million people.")
    assert [entry[0] for entry in log].count("play") == 1


def test_speaker_uses_espeak_for_a_sentence_the_voice_fails_on(monkeypatch):
    class BrokenVoice:
        name = "kokoro"

        def synthesize(self, text, wav_path):
            raise RuntimeError("onnx error")

    spoken = []
    monkeypatch.setattr(audio.EspeakTTS, "synthesize", lambda self, text, path: spoken.append(text))
    audio.Speaker(tts=BrokenVoice(), phrases=())._synthesize("Hello there.", "/tmp/x.wav")
    assert spoken == ["Hello there."]


# ---------------------------------------------------------------- speed
def test_long_first_sentence_is_split_at_a_comma():
    reply = ("Right now in Hyderabad it's clear and 23 degrees, feels like 25, humidity 68 percent. "
             "Today, a high of 33 and a low of 22 degrees.")
    assert speech_chunks(reply) == [
        "Right now in Hyderabad it's clear and 23 degrees,",
        "feels like 25, humidity 68 percent.",
        "Today, a high of 33 and a low of 22 degrees.",
    ]


@pytest.mark.parametrize("reply", [
    "Okay, I'll remind you to call Mom at 6 PM.",          # short enough already
    "The capital of France is Paris and it is very old.",   # no comma to split at
    "In short, yes.",                                       # the rest would be too short
])
def test_first_piece_left_whole(reply):
    assert speech_chunks(reply) == [reply]


def test_fixed_phrases_play_from_the_cache(monkeypatch):
    log = []
    monkeypatch.setattr(audio.subprocess, "Popen", lambda cmd: FakePlayer(log, cmd[1]))
    speaker = audio.Speaker(tts=RecordingTTS(log), phrases=("Yes?", "Switched on."))
    speaker.prewarm_thread.join(timeout=5)
    log.clear()
    speaker.say("Yes?")
    assert [entry[0] for entry in log] == ["play", "done"]  # nothing synthesized
    assert log[0][1].endswith(".wav") and "_cache" in log[0][1]
