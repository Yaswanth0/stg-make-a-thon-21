"""Times each text-to-speech setup on this machine, to pick the fastest one
that still sounds good. Run on the Pi (downloads any missing models once):

    python tts_benchmark.py

"First audio" is what you notice: how long after the reply is ready until
Rabbit starts talking (the first piece; the rest is made while it plays).
"x" is synthesis time / audio length: below 1.0 it keeps up with playback.
"""

import logging
import tempfile
import time
import wave
from pathlib import Path

from tts import EspeakTTS, KokoroTTS, PiperTTS, clean_for_speech, speech_chunks

REPLIES = [
    "Yes?",
    "Okay, I'll remind you to call Mom tomorrow at 6 PM.",
    "Right now in Hyderabad it's clear and 23 degrees, feels like 25, humidity 68 percent. "
    "Today, a high of 33 and a low of 22 degrees, with an 8 percent chance of rain.",
]

SETUPS = [
    ("kokoro int8", lambda: KokoroTTS(model="int8")),
    ("kokoro fp32", lambda: KokoroTTS(model="fp32")),
    ("piper", PiperTTS),
    ("espeak", EspeakTTS),
]


def audio_seconds(path):
    with wave.open(path, "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def main():
    logging.basicConfig(level=logging.WARNING)
    wav = str(Path(tempfile.gettempdir()) / "tts_benchmark.wav")
    print(f"{'setup':<13} {'load':>6} | " + " | ".join(f"reply {n + 1}: first audio, x" for n in range(len(REPLIES))))
    for name, make in SETUPS:
        try:
            started = time.monotonic()
            engine = make()
            load = time.monotonic() - started
        except Exception as e:
            print(f"{name:<13} not available: {e}")
            continue
        cells = []
        for reply in REPLIES:
            chunks = speech_chunks(clean_for_speech(reply))
            total_synth = total_audio = first = 0.0
            for i, chunk in enumerate(chunks):
                started = time.monotonic()
                engine.synthesize(chunk, wav)
                took = time.monotonic() - started
                if i == 0:
                    first = took
                total_synth += took
                total_audio += audio_seconds(wav)
            cells.append(f"{first:5.2f}s, x{total_synth / total_audio:.2f}")
        print(f"{name:<13} {load:5.1f}s | " + " | ".join(f"{c:>26}" for c in cells))
    print("\nPick the best-sounding setup whose 'first audio' feels short enough; set TTS_ENGINE "
          "and KOKORO_MODEL in config.py.")


if __name__ == "__main__":
    main()
