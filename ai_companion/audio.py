"""Microphone, Whisper and speech output.

The heavy libraries are imported inside the classes, so text mode
(python main.py --text) runs on a machine without a mic or Whisper.
"""

import logging
import os
import re
import subprocess
import threading
import time
from ctypes import CFUNCTYPE, c_char_p, c_int, cdll

import config
import display
import music
from tts import EspeakTTS, clean_for_speech, make_tts, speech_chunks

log = logging.getLogger("audio")


# ---------------------------------------------------------------- audio out
class Speaker:
    """Speaks through Kokoro, Piper or espeak-ng (see tts.py) and PipeWire.

    A reply is spoken piece by piece: while one piece plays, the next is being
    synthesized, so speech starts after the first piece instead of after the
    whole reply. Fixed replies ("Yes?", "Switched on.") are synthesized once,
    in the background at startup, and then play instantly. The main loop and
    the reminder thread both speak, so a lock makes one wait for the other
    instead of talking over it."""

    def __init__(self, tts=None, phrases=config.TTS_CACHED_PHRASES):
        self._tts = tts or make_tts()
        self._lock = threading.Lock()
        self._speaking = False
        self._last_spoke = 0.0  # time.monotonic() when the last speech ended
        self._muted = False
        self._player = None     # the pw-play process while speech is playing
        # Two files, so one can play while the next sentence is written.
        base = config.TTS_WAV[:-4] if config.TTS_WAV.endswith(".wav") else config.TTS_WAV
        self._wavs = (f"{base}_a.wav", f"{base}_b.wav")
        self._cache_dir = f"{base}_cache"
        self._cache = {}        # piece of text -> its ready-made WAV file
        self.prewarm_thread = None
        if phrases:
            self.prewarm_thread = threading.Thread(target=self._prewarm, args=(phrases,), name="tts-cache",
                                                   daemon=True)
            self.prewarm_thread.start()

    def say(self, text):
        if self._muted:
            log.info("AI (muted): %s", text)
            return
        log.info("AI: %s", text)
        chunks = speech_chunks(clean_for_speech(text))
        if not chunks:
            return
        with self._lock:
            if self._muted:
                return
            self._speaking = True
            try:
                ready = self._prepare(chunks[0], self._wavs[0])
                # The rabbit's mouth moves, and any song plays quietly underneath.
                with display.screen.mood("speaking"), music.player.ducked():
                    for i in range(len(chunks)):
                        if self._muted:
                            break
                        self._player = subprocess.Popen(["pw-play", ready])
                        if i + 1 < len(chunks):
                            ready = self._prepare(chunks[i + 1], self._wavs[(i + 1) % 2])  # while this one plays
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

    def _prepare(self, text, wav_path):
        """Path of a WAV saying `text`: the cached one, or a new one at wav_path."""
        cached = self._cache.get(text)
        if cached:
            return cached
        self._synthesize(text, wav_path)
        return wav_path

    def _prewarm(self, phrases):
        """Synthesizes the fixed replies into the cache, one at a time, letting
        real speech go first."""
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            for phrase in phrases:
                for chunk in speech_chunks(clean_for_speech(phrase)):
                    with self._lock:
                        if chunk in self._cache:
                            continue
                        path = os.path.join(self._cache_dir, f"{len(self._cache)}.wav")
                        self._tts.synthesize(chunk, path)
                        self._cache[chunk] = path
            log.info("%d fixed replies ready", len(self._cache))
        except Exception as e:
            log.warning("Could not prepare fixed replies (%s); they will be synthesized when said", e)

    def _synthesize(self, text, wav_path):
        try:
            self._tts.synthesize(text, wav_path)
        except (FileNotFoundError, subprocess.CalledProcessError):
            raise
        except Exception as e:
            # The voice failed on this sentence: say it with espeak-ng rather than not at all.
            log.error("%s failed (%s); using espeak-ng", self._tts.name, e)
            EspeakTTS().synthesize(text, wav_path)

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
        # The default (0.3 s of sound) threw away a quick "yes", "no" or "three" as noise.
        self._recognizer.phrase_threshold = config.MIC_PHRASE_THRESHOLD
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
        kept, unclear = [], []
        short = len(samples) / 16000 < config.WHISPER_SHORT_CLIP_SECONDS  # likely one or two words
        with display.screen.mood("hearing"):  # ears up while it works out what was said
            segments, _ = self._whisper.transcribe(
                samples,
                language="en",
                beam_size=config.WHISPER_BEAM_SIZE,
                # The voice detector can cut a single short word; short clips skip it.
                vad_filter=not short,
                # Words it should expect to hear. A long hint pulls a one-word
                # clip off course, so short clips get a short one.
                initial_prompt=config.WHISPER_SHORT_PROMPT if short else config.WHISPER_PROMPT,
            )
            heard_any = False
            for s in segments:  # transcription happens as this loop runs
                heard_any = True
                if is_confident(s):
                    kept.append(s.text.strip())
                else:
                    if s.no_speech_prob < 0.6:  # speech, just not clear (rather than noise)
                        unclear.append(s.text.strip())
                    log.info("Ignored unclear speech (logprob %.2f, no-speech %.2f): %s",
                             s.avg_logprob, s.no_speech_prob, s.text.strip())
        if not heard_any:
            log.info("Heard a sound, but no words in it")
        text = " ".join(kept).strip()
        if is_repetitive(text):
            log.info("Ignored repetitive speech: %s", text)
            text = ""
        if not text and (unclear or kept):
            display.screen.flash("confused")  # someone spoke, but it couldn't make it out
        return text or None


# What Whisper typically "hears" in silence or noise. Said alone, these must
# be clearly confident to count.
NOISE_WORDS = {"you", "thank you", "thanks", "thanks for watching", "bye", "so", "uh", "um", "hmm",
               "oh", "ah", "the", "i", "a"}


def is_confident(segment):
    """Whisper's own confidence: drops background noise it turned into words.
    One or two words get a lower bar, since a single word gives Whisper little
    context ("three" scores lower than "select three"), except for its usual
    noise words."""
    if segment.compression_ratio > 2.4:  # repeated words: a known hallucination
        return False
    words = re.findall(r"[a-z0-9']+", segment.text.lower())
    if not words:
        return False
    if len(words) <= 2:
        if " ".join(words) in NOISE_WORDS:
            return segment.no_speech_prob < 0.3 and segment.avg_logprob >= -0.8
        return segment.no_speech_prob < 0.6 and segment.avg_logprob >= config.WHISPER_SHORT_MIN_LOGPROB
    return segment.no_speech_prob < 0.6 and segment.avg_logprob >= config.WHISPER_MIN_LOGPROB


def is_repetitive(text):
    """"Good. Good. Good. Good." is noise Whisper heard as words."""
    words = re.findall(r"[a-z']+", text.lower())
    return len(words) >= 3 and len(set(words)) == 1
