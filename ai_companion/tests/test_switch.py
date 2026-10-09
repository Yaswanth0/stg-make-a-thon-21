"""The rocker switch: OFF mutes the companion (mic and speaker off) from any
state, ON brings it back in RUNNING. The program keeps running throughout."""

import signal
import threading
import time
from datetime import datetime, timedelta

import config
from agents.scheduler import ReminderWatcher
from main import Companion, State, SwitchControl, run_loop
from switch import FakeSwitch, open_switch


def flip_later(switch, on, delay=0.05):
    """Flips the switch from another thread, the way gpiozero calls back."""
    threading.Timer(delay, switch.set, args=(on,)).start()


def make_companion(control, default=State.SLEEP):
    said, asked = [], []

    def answer(text):
        asked.append(text)
        return f"answer to {text}"

    companion = Companion(answer, said.append, state=control.start_state(default))
    return companion, said, asked


def test_start_state_follows_the_switch():
    assert SwitchControl(FakeSwitch(on=True)).start_state(State.SLEEP) is State.RUNNING
    assert SwitchControl(FakeSwitch(on=False)).start_state(State.SLEEP) is State.OFF
    assert SwitchControl(None).start_state(State.SLEEP) is State.SLEEP


def test_off_mutes_then_on_resumes_without_exiting():
    switch = FakeSwitch(on=True)
    control = SwitchControl(switch)
    companion, said, asked = make_companion(control)
    events = []
    script = iter(["what time is it", "BUSY", "tell me a joke", "mayday"])
    busy_started = []

    def next_text():
        text = next(script)
        if text == "BUSY":
            busy_started.append(time.monotonic())
            flip_later(switch, False)
            time.sleep(3)  # stands in for a long listen(), LLM call or speech
            return "this must never be answered"
        return text

    def pause():
        events.append(("pause", companion.state))
        flip_later(switch, True, delay=0.2)

    def resume():
        events.append(("resume", companion.state))

    try:
        run_loop(companion, control, next_text, pause, resume)
    finally:
        control.close()

    assert events == [("pause", State.OFF), ("resume", State.OFF)]
    assert asked == ["what time is it", "tell me a joke"]
    assert said == ["answer to what time is it", "Switched off.", "Switched on.",
                    "answer to tell me a joke", "Shutting down."]
    assert companion.state is State.ABORTED  # only because of "mayday"
    if hasattr(signal, "pthread_kill"):  # Linux: the busy call is cut short
        assert len(busy_started) == 1


def test_starts_off_and_waits_silently_for_on():
    switch = FakeSwitch(on=False)
    control = SwitchControl(switch)
    companion, said, asked = make_companion(control)
    assert companion.state is State.OFF
    listened = []

    def next_text():
        listened.append(companion.state)
        return "mayday"

    flip_later(switch, True, delay=0.2)
    try:
        run_loop(companion, control, next_text)
    finally:
        control.close()
    assert said == ["Switched on.", "Shutting down."]
    assert listened == [State.RUNNING]  # never listened while OFF


def test_off_from_sleep():
    switch = FakeSwitch(on=True)
    control = SwitchControl(switch)
    companion, said, _ = make_companion(control)
    companion.on_text("go to sleep")
    assert companion.state is State.SLEEP
    states = []

    def pause():
        states.append(companion.state)
        flip_later(switch, True, delay=0.1)

    script = iter(["BUSY", "mayday"])

    def next_text():
        if next(script) == "BUSY":
            flip_later(switch, False)
            time.sleep(3)
            return None
        return "mayday"

    try:
        run_loop(companion, control, next_text, pause)
    finally:
        control.close()
    assert states == [State.OFF]
    assert said[-3:] == ["Switched off.", "Switched on.", "Shutting down."]


def test_nothing_is_answered_while_off():
    companion, said, asked = make_companion(SwitchControl(FakeSwitch(on=False)))
    companion.on_text("rabbit what time is it")
    companion.on_text("mayday")
    assert companion.state is State.OFF and said == [] and asked == []


def test_switch_off_before_arming_is_ignored():
    """Flicking it while the models are still loading doesn't interrupt anything."""
    switch = FakeSwitch(on=True)
    control = SwitchControl(switch)
    switch.set(False)
    assert not control.turned_off.is_set()


def test_real_ctrl_c_still_quits():
    control = SwitchControl(FakeSwitch(on=True))
    companion, _, _ = make_companion(control)

    def next_text():
        raise KeyboardInterrupt

    try:
        run_loop(companion, control, next_text)
        raised = False
    except KeyboardInterrupt:
        raised = True
    control.close()
    assert raised


def test_reminders_wait_while_off(db):
    said = []
    watcher = ReminderWatcher(db, said.append)
    due = datetime.now() - timedelta(seconds=config.MISSED_REMINDER_GRACE + 60)
    db.add_reminder("check the oven", due)

    watcher.pause()
    watcher.check()
    assert said == [] and len(db.pending_reminders()) == 1

    watcher.resume()
    watcher.check()
    assert len(said) == 1 and said[0].startswith("Missed reminder") and "check the oven" in said[0]
    assert db.pending_reminders() == []


def test_no_gpio_means_no_switch(monkeypatch):
    assert open_switch(enabled=False) is None
    monkeypatch.setattr(config, "SWITCH_PIN", None)
    assert open_switch() is None
