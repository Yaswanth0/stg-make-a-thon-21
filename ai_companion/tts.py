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
import time
import wave
from pathlib import Path
from urllib.request import urlopen

import config
import rai

log = logging.getLogger("tts")

KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_MODELS = {
    "int8": "kokoro-v1.0.int8.onnx",  # 92 MB
    "fp32": "kokoro-v1.0.onnx",       # 310 MB; on ARM CPUs often faster than int8
}
KOKORO_VOICES = "voices-v1.0.bin"     # 28 MB, every Kokoro voice


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
_CLAUSE_END = re.compile(r"(?<=[,;:])\s+")
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
    if chunks:
        chunks[:1] = split_first(chunks[0])
    return chunks


def split_first(chunk):
    """Synthesis time grows with length, so a long first sentence is split
    at its first comma: "Today in Hyderabad, it's sunny and 32 degrees." ->
    ["Today in Hyderabad,", "it's sunny and 32 degrees."]. The listener
    hears the start sooner; the rest is made while it plays."""
    if len(chunk.split()) <= config.TTS_FIRST_CHUNK_WORDS:
        return [chunk]
    parts = _CLAUSE_END.split(chunk, maxsplit=1)
    if len(parts) == 2 and len(parts[0].split()) >= 3 and len(parts[1]) >= MIN_CHUNK:
        return parts
    return [chunk]


# ---------------------------------------------------------------- engines
class EspeakTTS:
    name = "espeak-ng"

    def synthesize(self, text, wav_path):
        subprocess.run(
            ["espeak-ng", "-v", config.ESPEAK_VOICE, "-s", str(int(config.SPEECH_RATE * rai.speed)),
             "-w", wav_path, "--stdin"],
            input=text, text=True, check=True,
        )


class KokoroTTS:
    name = "kokoro"

    def __init__(self, voice=config.KOKORO_VOICE, models_dir=config.TTS_MODELS_DIR,
                 model=config.KOKORO_MODEL):
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        models_dir = Path(models_dir)
        filename = KOKORO_MODELS[model]
        model_path = download_once(KOKORO_URL + filename, models_dir / filename)
        voices = download_once(KOKORO_URL + KOKORO_VOICES, models_dir / KOKORO_VOICES)

        options = ort.SessionOptions()
        options.intra_op_num_threads = config.TTS_THREADS  # all 4 Pi 5 cores
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(str(model_path), sess_options=options,
                                       providers=["CPUExecutionProvider"])
        self._kokoro = Kokoro.from_session(session, str(voices))
        self._voice = voice
        # Voice names start with the accent: a = American, b = British.
        self._lang = "en-gb" if voice.startswith("b") else "en-us"
        self._kokoro.create("Ready.", voice=voice, speed=config.KOKORO_SPEED, lang=self._lang)  # warm-up
        log.info("Kokoro voice %s loaded (%s model, %d threads)", voice, model, config.TTS_THREADS)

    def synthesize(self, text, wav_path):
        started = time.monotonic()
        samples, sample_rate = self._kokoro.create(
            text, voice=self._voice, speed=config.KOKORO_SPEED * rai.speed, lang=self._lang,
        )
        write_wav(wav_path, samples, sample_rate)
        log_timing(self.name, text, time.monotonic() - started, len(samples) / sample_rate)


class PiperTTS:
    name = "piper"

    def __init__(self, voice=config.PIPER_VOICE, voices_dir=config.TTS_MODELS_DIR):
        from piper import PiperVoice, SynthesisConfig

        model = piper_voice_path(voice, voices_dir)
        self._voice = PiperVoice.load(str(model))
        self._make_options = SynthesisConfig
        self._options = SynthesisConfig(length_scale=config.PIPER_LENGTH_SCALE)
        # The first synthesis is slow (model warm-up); do it now, not on the first answer.
        with wave.open(io.BytesIO(), "wb") as wav:
            self._voice.synthesize_wav("Ready.", wav, syn_config=self._options)
        log.info("Piper voice %s loaded", voice)

    def synthesize(self, text, wav_path):
        started = time.monotonic()
        # "Speak slower / faster" (rai.speed): a longer length_scale is slower speech.
        options = self._make_options(length_scale=config.PIPER_LENGTH_SCALE / rai.speed)
        with wave.open(wav_path, "wb") as wav:
            self._voice.synthesize_wav(text, wav, syn_config=options)
        with wave.open(wav_path, "rb") as wav:
            audio_seconds = wav.getnframes() / wav.getframerate()
        log_timing(self.name, text, time.monotonic() - started, audio_seconds)


def log_timing(engine, text, took, audio_seconds):
    """"kokoro: 1.40s for 2.9s of audio (x0.48)": below x1.0 = faster than real time."""
    ratio = took / audio_seconds if audio_seconds else 0.0
    log.info("%s: %.2fs for %.1fs of audio (x%.2f): %s", engine, took, audio_seconds, ratio, text[:40])


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
