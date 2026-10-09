"""Text to speech, best voice first:

    kokoro  - most human-like (natural rhythm and intonation), heavier
    piper   - natural but flatter, very fast
    espeak  - robotic, tiny; always available

make_tts() starts with config.TTS_ENGINE and falls back down this list if an
engine isn't installed or its model can't be loaded, so Rabbit always talks.
All three run offline; Kokoro and Piper download their model once.
"""

import io
import logging
import re
import subprocess
import wave
from pathlib import Path
from urllib.request import urlopen

import config

log = logging.getLogger("tts")

KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_MODEL = "kokoro-v1.0.int8.onnx"  # 92 MB; int8 = the fastest on a Pi CPU
KOKORO_VOICES = "voices-v1.0.bin"       # 28 MB, every Kokoro voice


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
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
MIN_CHUNK = 25  # characters; shorter sentences are joined to the next one


def clean_for_speech(text):
    """Writes symbols out as words: "₹14,957/gm, 32.8°C" ->
    "14957 rupees per gram, 32.8 degrees Celsius"."""
    for pattern, replacement in _REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text.strip()


def speech_chunks(text):
    """Splits a reply into sentences to synthesize one at a time, so the first
    can play while the next is generated. Very short ones ("Sure.") are kept
    with the next sentence."""
    chunks, current = [], ""
    for sentence in _SENTENCE_END.split(text.strip()):
        current = f"{current} {sentence}".strip()
        if len(current) >= MIN_CHUNK:
            chunks.append(current)
            current = ""
    if current:
        if chunks and len(current) < MIN_CHUNK:
            chunks[-1] = f"{chunks[-1]} {current}"
        else:
            chunks.append(current)
    return chunks


# ---------------------------------------------------------------- engines
class EspeakTTS:
    name = "espeak-ng"

    def synthesize(self, text, wav_path):
        subprocess.run(
            ["espeak-ng", "-v", config.ESPEAK_VOICE, "-s", str(config.SPEECH_RATE), "-w", wav_path, "--stdin"],
            input=text, text=True, check=True,
        )


class KokoroTTS:
    name = "kokoro"

    def __init__(self, voice=config.KOKORO_VOICE, models_dir=config.TTS_MODELS_DIR):
        from kokoro_onnx import Kokoro

        models_dir = Path(models_dir)
        model = download_once(KOKORO_URL + KOKORO_MODEL, models_dir / KOKORO_MODEL)
        voices = download_once(KOKORO_URL + KOKORO_VOICES, models_dir / KOKORO_VOICES)
        self._kokoro = Kokoro(str(model), str(voices))
        self._voice = voice
        # Voice names start with the accent: a = American, b = British.
        self._lang = "en-gb" if voice.startswith("b") else "en-us"
        self._kokoro.create("Ready.", voice=voice, speed=config.KOKORO_SPEED, lang=self._lang)  # warm-up
        log.info("Kokoro voice %s loaded", voice)

    def synthesize(self, text, wav_path):
        samples, sample_rate = self._kokoro.create(
            text, voice=self._voice, speed=config.KOKORO_SPEED, lang=self._lang,
        )
        write_wav(wav_path, samples, sample_rate)


class PiperTTS:
    name = "piper"

    def __init__(self, voice=config.PIPER_VOICE, voices_dir=config.TTS_MODELS_DIR):
        from piper import PiperVoice, SynthesisConfig

        model = piper_voice_path(voice, voices_dir)
        self._voice = PiperVoice.load(str(model))
        self._options = SynthesisConfig(length_scale=config.PIPER_LENGTH_SCALE)
        # The first synthesis is slow (model warm-up); do it now, not on the first answer.
        with wave.open(io.BytesIO(), "wb") as wav:
            self._voice.synthesize_wav("Ready.", wav, syn_config=self._options)
        log.info("Piper voice %s loaded", voice)

    def synthesize(self, text, wav_path):
        with wave.open(wav_path, "wb") as wav:
            self._voice.synthesize_wav(text, wav, syn_config=self._options)


ENGINES = {"kokoro": KokoroTTS, "piper": PiperTTS, "espeak": EspeakTTS}
FALLBACK_ORDER = ["kokoro", "piper", "espeak"]


def make_tts(engine=config.TTS_ENGINE):
    """The configured engine, or the next one down the list that loads."""
    order = FALLBACK_ORDER[FALLBACK_ORDER.index(engine):] if engine in FALLBACK_ORDER else ["espeak"]
    for name in order[:-1]:
        try:
            return ENGINES[name]()
        except ImportError as e:
            log.warning("%s is not installed (%s); trying the next voice", name, e)
        except Exception as e:
            log.warning("%s voice failed to load (%s); trying the next voice", name, e)
    return ENGINES[order[-1]]()


# ---------------------------------------------------------------- files
def write_wav(path, samples, sample_rate):
    """Float samples in -1..1 -> 16-bit mono WAV."""
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def download_once(url, path):
    """Downloads `url` to `path` unless it is already there."""
    path = Path(path)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading %s (once)...", path.name)
    partial = path.with_name(path.name + ".part")
    with urlopen(url, timeout=60) as response, open(partial, "wb") as out:
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            out.write(block)
    partial.rename(path)  # only a complete file gets the real name
    return path


def piper_voice_path(voice, voices_dir):
    """Path to a Piper voice's .onnx file, downloading it (and its .json) once."""
    voices_dir = Path(voices_dir)
    model = voices_dir / f"{voice}.onnx"
    if not (model.exists() and Path(f"{model}.json").exists()):
        from piper.download_voices import download_voice

        log.info("Downloading Piper voice %s (about 60 MB, once)...", voice)
        voices_dir.mkdir(parents=True, exist_ok=True)
        download_voice(voice, voices_dir)
    return model
