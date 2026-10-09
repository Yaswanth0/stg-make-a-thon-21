"""Offline voice companion with three states.

SLEEP    - listens only for the wake word ("rabbit") or the exit word ("mayday")
RUNNING  - listens and answers; "sleep" or 2 minutes of silence returns to SLEEP
ABORTED  - "mayday" in any state ends the program

The rocker switch (switch.py) overrides both: switching it ON starts the
companion in RUNNING, switching it OFF goes to ABORTED from any state. If it
is OFF when the program starts, the program waits until it is switched on.

Each sentence in RUNNING goes to the Conductor, which hands it to one agent:
Scheduler (reminders), Researcher (web search), Archivist (saving facts) or
Responder (everything else).

    python main.py           voice mode (mic and speaker)
    python main.py --text    type instead of speak; replies are printed
"""

import _thread
import argparse
import logging
import os
import re
import signal
import sys
import threading
import time
from enum import Enum

import config
from db import Database
from switch import open_switch

log = logging.getLogger("main")


class State(Enum):
    SLEEP = "sleep"
    RUNNING = "running"
    ABORTED = "aborted"


# ---------------------------------------------------------------- keywords
def to_words(text):
    """Lowercases, strips punctuation and splits into words."""
    return re.sub(r"[^a-z\s]", "", text.lower()).split()


def has_exit_word(words):
    if config.EXIT_WORD in words:
        return True
    # Whisper sometimes writes it as two words: "may day"
    return any(a == "may" and b == "day" for a, b in zip(words, words[1:]))


def split_on_wake_word(words):
    """Returns (found, words_after_wake_word)."""
    for i, word in enumerate(words):
        if word in config.WAKE_WORDS:
            return True, words[i + 1:]
    return False, []


def is_sleep_command(words):
    return config.SLEEP_WORD in words and len(words) <= config.SLEEP_COMMAND_MAX_WORDS


# ---------------------------------------------------------------- state machine
class Companion:
    """The state machine. Takes heard (or typed) sentences, speaks through
    `say`, and hands real requests to `answer` (the Conductor)."""

    def __init__(self, answer, say, state=State.SLEEP):
        self.answer = answer
        self.say = say
        self.state = state
        self.last_activity = time.monotonic()
        log.info("--- State: %s ---", state.value.upper())

    def switch_off(self):
        """Rocker switch turned OFF: any state -> ABORTED."""
        if self.state is State.ABORTED:
            return
        self.say("Switched off. Shutting down.")
        self.set_state(State.ABORTED)

    def set_state(self, new_state):
        log.info("--- State: %s ---", new_state.value.upper())
        self.state = new_state

    def respond(self, text):
        self.say(self.answer(text))
        self.last_activity = time.monotonic()

    def check_timeout(self):
        """RUNNING -> SLEEP after SILENCE_TIMEOUT seconds without understood speech."""
        if self.state is State.RUNNING and time.monotonic() - self.last_activity >= config.SILENCE_TIMEOUT:
            self.say("No activity. Going to sleep.")
            self.set_state(State.SLEEP)

    def on_text(self, text):
        log.info("Heard (%s): %s", self.state.value, text)
        words = to_words(text)

        # Any state -> ABORTED
        if has_exit_word(words):
            self.say("Shutting down.")
            self.set_state(State.ABORTED)
            return

        if self.state is State.SLEEP:
            woke, rest = split_on_wake_word(words)
            if woke:
                self.set_state(State.RUNNING)
                if rest:
                    # "Rabbit, what is the capital of India" -> answer right away
                    self.respond(" ".join(rest))
                else:
                    self.say("Yes?")
                    self.last_activity = time.monotonic()
            return

        # state is RUNNING
        if is_sleep_command(words):
            self.say("Going to sleep.")
            self.set_state(State.SLEEP)
            return

        self.respond(text)


# ---------------------------------------------------------------- setup
def build_conductor(llm, db):
    from agents import Archivist, Conductor, Researcher, Responder, Scheduler

    return Conductor(llm, db, {
        "schedule": Scheduler(llm, db),
        "search": Researcher(llm, db),
        "remember": Archivist(llm, db),
        "answer": Responder(llm, db),
    })


def load_llm():
    from llm import LLM

    log.info("Loading language model %s...", config.LLM_MODEL)
    llm = LLM()
    ready = llm.load()
    log.info("Language model loaded." if ready else "Language model failed to load.")
    return llm, ready


class SwitchControl:
    """Connects the rocker switch to the main loop. The switch is read in a
    gpiozero thread, while the main thread may be busy for several seconds
    (listening, transcribing, waiting for the LLM). So turning it OFF
    interrupts the main thread, the same way Ctrl+C does."""

    def __init__(self, switch):
        self.switch = switch
        self.turned_off = threading.Event()
        self._armed = False
        self._force_quit_timer = None
        if switch:
            switch.on_change(self._changed)

    def wait_until_on(self):
        """If the switch is OFF, waits until it is switched ON. From then on,
        switching it OFF ends the program."""
        if self.switch and not self.switch.is_on():
            log.info("Switch is OFF. Waiting for it to be switched ON...")
            self.switch.wait_until_on()
        self._armed = True

    def start_state(self, default):
        """With a switch, the companion starts awake: it was just switched ON."""
        return State.RUNNING if self.switch else default

    def _changed(self, is_on):
        if is_on or not self._armed or self.turned_off.is_set():
            return
        self.turned_off.set()
        interrupt_main_thread()
        # Last resort, if the main thread is stuck somewhere it can't be interrupted.
        self._force_quit_timer = threading.Timer(config.SWITCH_OFF_FORCE_QUIT, self._force_quit)
        self._force_quit_timer.daemon = True
        self._force_quit_timer.start()

    @staticmethod
    def _force_quit():
        log.error("Shutdown took too long; quitting now")
        os._exit(config.SWITCH_OFF_EXIT_CODE)

    def close(self):
        if self._force_quit_timer:
            self._force_quit_timer.cancel()
        if self.switch:
            self.switch.close()


def interrupt_main_thread():
    """Raises KeyboardInterrupt in the main thread. On Linux a real SIGINT is
    sent to that thread, which also wakes it from a blocking call (reading
    the mic, waiting for Ollama). Elsewhere it takes effect once that call
    returns."""
    if hasattr(signal, "pthread_kill"):
        signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)
    else:
        _thread.interrupt_main()


def run_loop(companion, control, next_text):
    """Feeds sentences from `next_text()` to the companion until ABORTED."""
    while companion.state is not State.ABORTED:
        try:
            if control.turned_off.is_set():
                companion.switch_off()
                continue
            text = next_text()
            if text:
                companion.on_text(text)
        except KeyboardInterrupt:
            if not control.turned_off.is_set():
                raise  # a real Ctrl+C
            companion.switch_off()


def run_text(conductor, db, control, speak):
    """Typed input. Starts awake, so you can test agents straight away."""
    from agents import ReminderWatcher
    from audio import Speaker, print_say

    say = Speaker().say if speak else print_say
    control.wait_until_on()
    watcher = ReminderWatcher(db, say)
    watcher.start()
    companion = Companion(conductor.handle, say, state=State.RUNNING)
    print("Type to talk. 'sleep', 'rabbit' and 'mayday' work as in voice mode. Ctrl+D quits.", flush=True)

    def next_text():
        try:
            return input("> ").strip()
        except EOFError:
            companion.set_state(State.ABORTED)
            return None

    try:
        run_loop(companion, control, next_text)
    finally:
        watcher.stop()


def run_voice(conductor, db, control, llm_ready):
    from agents import ReminderWatcher
    from audio import Speaker, VoiceInput

    speaker = Speaker()
    with VoiceInput(speaker) as voice:
        control.wait_until_on()
        speaker.say("System ready." if llm_ready else "System ready, but the language model did not load.")
        watcher = ReminderWatcher(db, speaker.say)
        watcher.start()
        companion = Companion(conductor.handle, speaker.say, state=control.start_state(State.SLEEP))

        def next_text():
            companion.check_timeout()
            if companion.state is State.ABORTED:
                return None
            return voice.listen(5 if companion.state is State.SLEEP else 15)

        try:
            run_loop(companion, control, next_text)
        finally:
            watcher.stop()


def main(argv=None):
    """Returns the exit code: 0 for "mayday" or Ctrl+C, SWITCH_OFF_EXIT_CODE
    when the rocker switch was turned off."""
    parser = argparse.ArgumentParser(description="Offline voice companion")
    parser.add_argument("--text", action="store_true", help="type instead of speaking")
    parser.add_argument("--speak", action="store_true", help="with --text: also speak replies")
    parser.add_argument("--no-switch", action="store_true", help="ignore the rocker switch")
    parser.add_argument("--debug", action="store_true", help="more detailed logs")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # Libraries that log every HTTP request to Ollama or DuckDuckGo.
    for noisy in ("httpx", "httpcore", "primp", "urllib3", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    control = SwitchControl(open_switch(enabled=not args.no_switch))
    db = Database(config.DB_FILE)
    llm, llm_ready = load_llm()
    conductor = build_conductor(llm, db)
    try:
        if args.text:
            run_text(conductor, db, control, args.speak)
        else:
            run_voice(conductor, db, control, llm_ready)
    except KeyboardInterrupt:
        pass
    finally:
        control.close()
        db.close()
    return config.SWITCH_OFF_EXIT_CODE if control.turned_off.is_set() else 0


if __name__ == "__main__":
    sys.exit(main())
