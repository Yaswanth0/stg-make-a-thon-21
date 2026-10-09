"""Text to speech: symbol cleanup, and falling back to espeak-ng."""

import pytest

import audio
import tts
from tts import EspeakTTS, clean_for_speech, make_tts


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


def test_falls_back_to_espeak_without_piper(monkeypatch):
    def no_piper(*args, **kwargs):
        raise ImportError("No module named 'piper'")

    monkeypatch.setattr(tts, "PiperTTS", no_piper)
    assert isinstance(make_tts("piper"), EspeakTTS)


def test_falls_back_to_espeak_when_voice_fails_to_load(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("voice download failed")

    monkeypatch.setattr(tts, "PiperTTS", broken)
    assert isinstance(make_tts("piper"), EspeakTTS)


def test_espeak_when_configured():
    assert isinstance(make_tts("espeak"), EspeakTTS)


def test_speaker_uses_espeak_for_a_sentence_piper_fails_on(monkeypatch):
    class BrokenPiper:
        name = "piper"

        def synthesize(self, text, wav_path):
            raise RuntimeError("onnx error")

    spoken = []
    monkeypatch.setattr(audio.EspeakTTS, "synthesize", lambda self, text, path: spoken.append(text))
    audio.Speaker(tts=BrokenPiper())._synthesize("Hello there.")
    assert spoken == ["Hello there."]
