"""Text to speech: Piper (natural, human-like voice) with espeak-ng as the
fallback, so Rabbit can always talk.

Piper runs fully offline. The voice model (~60 MB) is downloaded once, the
first time it's needed, into config.PIPER_VOICES_DIR.
"""

import io
import logging
import re
import subprocess
import wave
from pathlib import Path

import config

log = logging.getLogger("tts")


# ---------------------------------------------------------------- text cleanup
_REPLACEMENTS = [
    (re.compile(r"(?<=\d),(?=\d)"), ""),                                  # 14,957 -> 14957
    (re.compile(r"(?:₹|\bRs\.?|\bINR)\s*(\d+(?:\.\d+)?)"), r"\1 rupees"),
    (re.compile(r"\$\s*(\d+(?:\.\d+)?)"), r"\1 dollars"),
    (re.compile(r"€\s*(\d+(?:\.\d+)?)"), r"\1 euros"),
    (re.compile(r"£\s*(\d+(?:\.\d+)?)"), r"\1 pounds"),
    (re.compile(r"\s*°\s*C\b"), " degrees Celsius"),
    (re.compile(r"\s*°\s*F\b"), " degrees Fahrenheit"),
    (re.compile(r"\s*°"), " degrees"),
    (re.compile(r"\s*%"), " percent"),
    (re.compile(r"\s*km/h\b"), " kilometres per hour"),
    (re.compile(r"\s*/\s*(gm|g|gram)\b"), " per gram"),
    (re.compile(r"\s*/\s*kg\b"), " per kilogram"),
    (re.compile(r"https?://\S+"), ""),
    (re.compile(r"[*_#`~>|]+"), " "),                                     # stray markdown
    (re.compile(r"\s{2,}"), " "),
]


def clean_for_speech(text):
    """Writes symbols out as words: "₹14,957/gm, 32.8°C" ->
    "14957 rupees per gram, 32.8 degrees Celsius"."""
    for pattern, replacement in _REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text.strip()


# ---------------------------------------------------------------- engines
class EspeakTTS:
    name = "espeak-ng"

    def synthesize(self, text, wav_path):
        subprocess.run(
            ["espeak-ng", "-s", str(config.SPEECH_RATE), "-w", wav_path, "--stdin"],
            input=text, text=True, check=True,
        )


class PiperTTS:
    name = "piper"

    def __init__(self, voice=config.PIPER_VOICE, voices_dir=config.PIPER_VOICES_DIR):
        from piper import PiperVoice, SynthesisConfig

        model = voice_model_path(voice, voices_dir)
        self._voice = PiperVoice.load(str(model))
        self._options = SynthesisConfig(length_scale=config.PIPER_LENGTH_SCALE)
        # The first synthesis is slow (model warm-up); do it now, not on the first answer.
        with wave.open(io.BytesIO(), "wb") as wav:
            self._voice.synthesize_wav("Ready.", wav, syn_config=self._options)
        log.info("Piper voice %s loaded", voice)

    def synthesize(self, text, wav_path):
        with wave.open(wav_path, "wb") as wav:
            self._voice.synthesize_wav(text, wav, syn_config=self._options)


def voice_model_path(voice, voices_dir):
    """Path to the voice's .onnx file, downloading it (and its .json) once."""
    voices_dir = Path(voices_dir)
    model = voices_dir / f"{voice}.onnx"
    if not (model.exists() and Path(f"{model}.json").exists()):
        from piper.download_voices import download_voice

        log.info("Downloading Piper voice %s (about 60 MB, once)...", voice)
        voices_dir.mkdir(parents=True, exist_ok=True)
        download_voice(voice, voices_dir)
    return model


def make_tts(engine=config.TTS_ENGINE):
    """Piper if it's installed and its voice is available, else espeak-ng."""
    if engine == "piper":
        try:
            return PiperTTS()
        except ImportError:
            log.warning("Piper is not installed (pip install piper-tts); using espeak-ng")
        except Exception as e:
            log.warning("Piper voice failed to load (%s); using espeak-ng", e)
    return EspeakTTS()
