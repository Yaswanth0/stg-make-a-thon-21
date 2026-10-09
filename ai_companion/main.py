"""Offline voice companion with four states.

OFF      - rocker switch is off: mic and speaker off, nothing is heard or said
SLEEP    - listens only for the wake word ("rabbit") or the exit word ("mayday")
RUNNING  - listens and answers; "sleep" or 2 minutes of silence returns to SLEEP
ABORTED  - "mayday" in any state ends the program

The rocker switch (switch.py) moves between OFF and RUNNING: switching it OFF
goes to OFF from any state, switching it ON goes to RUNNING. Reminders that
come due while OFF are announced once it is switched back on.

Each sentence in RUNNING goes to the Conductor, which hands it to one agent:
Scheduler (reminders), Researcher (web search), Archivist (saving facts) or
Responder (everything else).

    python main.py           voice mode (mic and speaker)
    python main.py --text    type instead of speak; replies are printed
"""

import _thread
import argparse
import logging
import re
import signal
import sys
import threading
import time
from enum import Enum

import config
import leds
from db import Database
from leds import open_leds
from switch import open_switch

log = logging.getLogger("main")


class State(Enum):
    OFF = "off"
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
def is_on(state):
    """Red LED: lit while Rabbit is on (asleep or awake)."""
    return state in (State.SLEEP, State.RUNNING)


class Companion:
    """The state machine. Takes heard (or typed) sentences, speaks through
    `say`, and hands real requests to `answer` (the Conductor)."""

    def __init__(self, answer, say, state=State.SLEEP):
        self.answer = answer
        self.say = say
        self.state = state
        self.last_activity = time.monotonic()
        log.info("--- State: %s ---", state.value.upper())
        leds.status.power(is_on(state))

    def set_state(self, new_state):
        log.info("--- State: %s ---", new_state.value.upper())
        self.state = new_state
        leds.status.power(is_on(new_state))

    def switch_off(self):
        """Rocker switch turned OFF: SLEEP or RUNNING -> OFF."""
        if self.state in (State.OFF, State.ABORTED):
            return
        self.say("Switched off.")
        self.set_state(State.OFF)

    def switch_on(self):
        """Rocker switch turned ON: OFF -> RUNNING."""
        if self.state is not State.OFF:
            return
        self.set_state(State.RUNNING)
        self.say("Switched on.")
        self.last_activity = time.monotonic()

    def respond(self, text):
        with leds.status.thinking():  # white LED while the reply is worked out
            reply = self.answer(text)
        self.say(reply)
        self.last_activity = time.monotonic()

    def check_timeout(self):
        """RUNNING -> SLEEP after SILENCE_TIMEOUT seconds without understood speech."""
        if self.state is State.RUNNING and time.monotonic() - self.last_activity >= config.SILENCE_TIMEOUT:
            self.say("No activity. Going to sleep.")
            self.set_state(State.SLEEP)

    def on_text(self, text):
        if self.state is State.OFF:
            return  # the mic is off; nothing should get here, but never answer
        log.info("Heard (%s): %s", self.state.value, text)
        words = to_words(text)

        # Any state -> ABORTED
        if has_exit_word(words):
            self.say("Shutting down.")
            self.set_state(State.ABORTED)
            return

        woke, rest = split_on_wake_word(words)
        if self.state is State.SLEEP:
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

        if woke and not rest:
            # Just "rabbit" while already awake: don't send it to the LLM.
            self.say("Yes?")
            self.last_activity = time.monotonic()
            return

        self.respond(" ".join(rest) if woke else text)


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


# ---------------------------------------------------------------- rocker switch
def interrupt_main_thread():
    """Raises KeyboardInterrupt in the main thread. On Linux a real SIGINT is
    sent to that thread, which also wakes it from a blocking call (reading
    the mic, waiting for Ollama, playing speech). Elsewhere it takes effect
    once that call returns."""
    if hasattr(signal, "pthread_kill"):
        signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)
    else:
        _thread.interrupt_main()


class SwitchControl:
    """Connects the rocker switch to the main loop. The switch is read in a
    gpiozero thread, while the main thread may be busy for several seconds
    (listening, transcribing, waiting for the LLM, speaking). So turning it
    OFF interrupts the main thread, the same way Ctrl+C does, and the main
    loop turns that into the OFF state."""

    def __init__(self, switch):
        self.switch = switch
        self.turned_off = threading.Event()  # set = an OFF interrupt was sent
        self._armed = False
        if switch:
            switch.on_change(self._changed)

    def is_on(self):
        return self.switch is None or self.switch.is_on()

    def start_state(self, default):
        """OFF if the switch is off, RUNNING if on, `default` without a switch."""
        if self.switch is None:
            return default
        return State.RUNNING if self.switch.is_on() else State.OFF

    def arm(self):
        """From now on, switching OFF interrupts the main thread."""
        self._armed = True

    def wait_until_on(self):
        """Blocks while the switch is OFF, then allows the next OFF interrupt."""
        if self.switch:
            self.switch.wait_until_on()
        self.turned_off.clear()

    def _changed(self, is_on):
        if is_on or not self._armed or self.turned_off.is_set():
            return  # ON is picked up by wait_until_on()
        self.turned_off.set()
        interrupt_main_thread()

    def close(self):
        self._armed = False
        if self.switch:
            self.switch.close()


def run_loop(companion, control, next_text, pause=lambda: None, resume=lambda: None):
    """Feeds sentences from `next_text()` to the companion until ABORTED.
    `pause` turns the mic and speaker off when the switch goes OFF; `resume`
    turns them back on."""
    control.arm()
    if not control.is_on() and companion.state is not State.OFF:
        companion.set_state(State.OFF)  # switched off while the models loaded

    # The OFF interrupt can arrive at any line, so the whole loop is inside
    # the try; after handling it, the loop simply starts again.
    while True:
        try:
            while True:
                if companion.state is State.ABORTED:
                    return
                if companion.state is State.OFF:
                    pause()
                    control.wait_until_on()
                    if not control.is_on():
                        continue  # flicked off again straight away
                    resume()
                    companion.switch_on()
                    continue
                text = next_text()
                if text:
                    companion.on_text(text)
        except KeyboardInterrupt:
            if not control.turned_off.is_set():
                raise  # a real Ctrl+C
            companion.switch_off()


# ---------------------------------------------------------------- modes
def flush_typed_input():
    """Throws away anything typed while switched off."""
    try:
        import termios

        if sys.stdin.isatty():
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except (ImportError, OSError):
        pass


def run_text(conductor, db, control, speak):
    """Typed input. Starts awake, so you can test agents straight away."""
    from agents import ReminderWatcher
    from audio import Speaker, print_say

    speaker = Speaker() if speak else None
    say = speaker.say if speaker else print_say
    watcher = ReminderWatcher(db, say)
    companion = Companion(conductor.handle, say, state=control.start_state(State.RUNNING))
    if companion.state is State.OFF:
        watcher.pause()
    watcher.start()
    print("Type to talk. 'sleep', 'rabbit' and 'mayday' work as in voice mode. Ctrl+D quits.", flush=True)

    def next_text():
        try:
            return input("> ").strip()
        except EOFError:
            companion.set_state(State.ABORTED)
            return None

    def pause():
        watcher.pause()
        if speaker:
            speaker.mute()
        print("(Switched off: typing is ignored until the switch is turned ON.)", flush=True)

    def resume():
        flush_typed_input()
        if speaker:
            speaker.unmute()
        watcher.resume()

    try:
        run_loop(companion, control, next_text, pause, resume)
    finally:
        watcher.stop()


def run_voice(conductor, db, control, llm_ready):
    from agents import ReminderWatcher
    from audio import Speaker, VoiceInput

    speaker = Speaker()
    with VoiceInput(speaker) as voice:
        watcher = ReminderWatcher(db, speaker.say)
        companion = Companion(conductor.handle, speaker.say, state=control.start_state(State.SLEEP))
        if companion.state is State.OFF:
            watcher.pause()
        else:
            speaker.say("System ready." if llm_ready else "System ready, but the language model did not load.")
        watcher.start()

        def next_text():
            companion.check_timeout()
            if companion.state not in (State.SLEEP, State.RUNNING):
                return None
            return voice.listen(5 if companion.state is State.SLEEP else 15)

        def pause():
            watcher.pause()
            speaker.mute()
            voice.pause()

        def resume():
            voice.resume()
            speaker.unmute()
            watcher.resume()

        try:
            run_loop(companion, control, next_text, pause, resume)
        finally:
            watcher.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline voice companion")
    parser.add_argument("--text", action="store_true", help="type instead of speaking")
    parser.add_argument("--speak", action="store_true", help="with --text: also speak replies")
    parser.add_argument("--no-switch", action="store_true", help="ignore the rocker switch")
    parser.add_argument("--no-leds", action="store_true", help="don't use the status LEDs")
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
    leds.status = open_leds(enabled=not args.no_leds)
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
        leds.status.close()  # all LEDs off when the program ends
        db.close()


if __name__ == "__main__":
    main()
