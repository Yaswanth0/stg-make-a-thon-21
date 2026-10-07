"""Offline voice companion with three states.

SLEEP    - listens only for the wake word ("rabbit") or the exit word ("mayday")
RUNNING  - listens and answers; "sleep" or 2 minutes of silence returns to SLEEP
ABORTED  - "mayday" in any state ends the program

Each sentence in RUNNING goes to the Conductor, which hands it to one agent:
Scheduler (reminders), Researcher (web search), Archivist (saving facts) or
Responder (everything else).

    python main.py           voice mode (mic and speaker)
    python main.py --text    type instead of speak; replies are printed
"""

import argparse
import logging
import re
import sys
import time
from enum import Enum

import config
from db import Database

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


def run_text(conductor, db, speak):
    """Typed input. Starts awake, so you can test agents straight away."""
    from agents import ReminderWatcher
    from audio import Speaker, print_say

    say = Speaker().say if speak else print_say
    watcher = ReminderWatcher(db, say)
    watcher.start()
    companion = Companion(conductor.handle, say, state=State.RUNNING)
    print("Type to talk. 'sleep', 'rabbit' and 'mayday' work as in voice mode. Ctrl+D quits.", flush=True)
    try:
        while companion.state is not State.ABORTED:
            try:
                text = input("> ").strip()
            except EOFError:
                break
            if text:
                companion.on_text(text)
    finally:
        watcher.stop()


def run_voice(conductor, db, llm_ready):
    from agents import ReminderWatcher
    from audio import Speaker, VoiceInput

    speaker = Speaker()
    with VoiceInput(speaker) as voice:
        speaker.say("System ready." if llm_ready else "System ready, but the language model did not load.")
        watcher = ReminderWatcher(db, speaker.say)
        watcher.start()
        companion = Companion(conductor.handle, speaker.say)
        try:
            while companion.state is not State.ABORTED:
                companion.check_timeout()
                phrase_limit = 5 if companion.state is State.SLEEP else 15
                text = voice.listen(phrase_limit)
                if text:
                    companion.on_text(text)
        finally:
            watcher.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline voice companion")
    parser.add_argument("--text", action="store_true", help="type instead of speaking")
    parser.add_argument("--speak", action="store_true", help="with --text: also speak replies")
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

    db = Database(config.DB_FILE)
    llm, llm_ready = load_llm()
    conductor = build_conductor(llm, db)
    try:
        if args.text:
            run_text(conductor, db, args.speak)
        else:
            run_voice(conductor, db, llm_ready)
    except KeyboardInterrupt:
        pass
    finally:
        db.close()


if __name__ == "__main__":
    main()
