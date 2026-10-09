"""Microphone, Whisper and speech output.

The heavy libraries are imported inside the classes, so text mode
(python main.py --text) runs on a machine without a mic or Whisper.
"""

import logging
import subprocess
import threading
import time
from ctypes import CFUNCTYPE, c_char_p, c_int, cdll

import config

log = logging.getLogger("audio")


# ---------------------------------------------------------------- audio out
class Speaker:
    """Speaks through espeak-ng and PipeWire. The main loop and the reminder
    thread both speak, so a lock makes one wait for the other instead of
    talking over it."""

    def __init__(self):
        self._lock = threading.Lock()
        self._speaking = False
        self._last_spoke = 0.0  # time.monotonic() when the last speech ended
        self._muted = False
        self._player = None     # the pw-play process while speech is playing

    def say(self, text):
        if self._muted:
            log.info("AI (muted): %s", text)
            return
        log.info("AI: %s", text)
        with self._lock:
            if self._muted:
                return
            self._speaking = True
            try:
                subprocess.run(
                    ["espeak-ng", "-s", str(config.SPEECH_RATE), "-w", config.TTS_WAV, "--stdin"],
                    input=text,
                    text=True,
                    check=True,
                )
                self._player = subprocess.Popen(["pw-play", config.TTS_WAV])
                self._player.wait()
            except FileNotFoundError as e:
                log.error("Speech error: %s is not installed.", e.filename)
            except subprocess.CalledProcessError as e:
                log.error("Speech error: %s", e)
            finally:
                # Still playing here only if we were interrupted (switch OFF).
                if self._player is not None and self._player.poll() is None:
                    self._player.terminate()
                self._player = None
                self._speaking = False
                self._last_spoke = time.monotonic()

    def mute(self):
        """Stops any speech playing now (from any thread) and stays silent
        until unmute()."""
        self._muted = True
        player = self._player
        if player is not None and player.poll() is None:
            player.terminate()

    def unmute(self):
        self._muted = False

    def spoke_since(self, moment):
        """True if the speaker was talking at any point after `moment`."""
        return self._speaking or self._last_spoke > moment


def print_say(text):
    """Text mode output."""
    print(f"AI: {text}", flush=True)


# ---------------------------------------------------------------- audio in
def silence_alsa_warnings():
    """Stops ALSA from printing its wall of harmless warnings."""
    try:
        handler_type = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
        handler = handler_type(lambda *args: None)
        cdll.LoadLibrary("libasound.so.2").snd_lib_error_set_handler(handler)
        return handler  # must stay referenced for as long as the program runs
    except OSError:
        return None


class VoiceInput:
    """Microphone + Whisper. Use as a context manager: the mic stays open
    for the whole run."""

    def __init__(self, speaker):
        import speech_recognition as sr

        self._sr = sr
        self._speaker = speaker
        self._alsa_handler = silence_alsa_warnings()
        log.info("Loading speech recognition model...")
        self._whisper = self._load_whisper()
        self._recognizer = sr.Recognizer()
        self._mic = sr.Microphone(device_index=self._find_mic_index(), sample_rate=config.MIC_SAMPLE_RATE)
        self._source = None

    def __enter__(self):
        self._source = self._mic.__enter__()
        self._recognizer.adjust_for_ambient_noise(self._source, duration=1)
        return self

    def __exit__(self, *exc):
        return self._mic.__exit__(*exc)

    def _find_mic_index(self):
        for index, name in enumerate(self._sr.Microphone.list_microphone_names()):
            if config.MIC_NAME_HINT.lower() in name.lower():
                log.info("Using microphone %d: %s", index, name)
                return index
        log.warning("No microphone matching '%s', using index %d", config.MIC_NAME_HINT, config.MIC_FALLBACK_INDEX)
        return config.MIC_FALLBACK_INDEX

    @staticmethod
    def _load_whisper():
        """Loads the Whisper model from disk. Only if it has never been
        downloaded does it go online, once, to fetch it."""
        import numpy as np
        from faster_whisper import WhisperModel

        options = dict(device="cpu", compute_type="int8", cpu_threads=4)
        try:
            model = WhisperModel(config.WHISPER_MODEL, local_files_only=True, **options)
        except Exception:
            log.info("Whisper model '%s' not found locally. Downloading it once...", config.WHISPER_MODEL)
            model = WhisperModel(config.WHISPER_MODEL, **options)
        # One dummy pass over a second of silence, so the first real one is fast.
        list(model.transcribe(np.zeros(16000, dtype=np.float32), language="en")[0])
        return model

    def pause(self):
        """Turns the mic off: the audio stream stops capturing."""
        try:
            self._source.stream.pyaudio_stream.stop_stream()
            log.info("Microphone off")
        except Exception as e:
            log.warning("Could not stop the microphone: %s", e)

    def resume(self):
        try:
            stream = self._source.stream.pyaudio_stream
            if stream.is_stopped():
                stream.start_stream()
            log.info("Microphone on")
        except Exception as e:
            log.warning("Could not restart the microphone: %s", e)
        self._discard_buffered_audio()

    def _discard_buffered_audio(self):
        """Throws away audio captured while we were busy (mostly our own voice
        coming out of the speaker), so it is not mistaken for the user."""
        try:
            stream = self._source.stream.pyaudio_stream
            frames = stream.get_read_available()
            if frames:
                stream.read(frames, exception_on_overflow=False)
        except Exception:
            pass

    def listen(self, phrase_limit):
        """Waits up to 5 seconds for speech. Returns the text, or None if
        nothing was said or understood."""
        import numpy as np

        self._discard_buffered_audio()
        started = time.monotonic()
        try:
            audio = self._recognizer.listen(self._source, timeout=5, phrase_time_limit=phrase_limit)
        except self._sr.WaitTimeoutError:
            return None

        # A reminder spoke while we were recording: what we heard is our own voice.
        if self._speaker.spoke_since(started):
            log.debug("Dropped audio recorded while the speaker was talking")
            return None

        # Whisper expects 16 kHz float samples; the mic delivers 48 kHz 16-bit.
        raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._whisper.transcribe(
            samples,
            language="en",
            beam_size=1,
            vad_filter=True,
            initial_prompt="Rabbit. Mayday. Sleep.",  # nudges spelling of the keywords
        )
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6).strip()
        return text or None
