"""The rocker switch: ON starts in RUNNING, OFF goes to ABORTED from any state."""

import signal
import threading
import time

import config
from main import Companion, State, SwitchControl, run_loop
from switch import FakeSwitch, open_switch


def flip_later(switch, on, delay=0.05):
    """Flips the switch from another thread, the way gpiozero calls back."""
    threading.Timer(delay, switch.set, args=(on,)).start()


def make_companion(control, default=State.SLEEP):
    said = []
    companion = Companion(lambda text: f"answer to {text}", said.append, state=control.start_state(default))
    return companion, said


def test_switch_on_starts_in_running():
    control = SwitchControl(FakeSwitch(on=True))
    control.wait_until_on()
    companion, _ = make_companion(control)
    assert companion.state is State.RUNNING


def test_without_switch_voice_mode_starts_asleep():
    control = SwitchControl(None)
    control.wait_until_on()  # returns at once
    companion, _ = make_companion(control)
    assert companion.state is State.SLEEP


def test_waits_while_switch_is_off():
    switch = FakeSwitch(on=False)
    control = SwitchControl(switch)
    flip_later(switch, True, delay=0.2)
    started = time.monotonic()
    control.wait_until_on()
    assert time.monotonic() - started >= 0.15
    assert not control.turned_off.is_set()


def test_switch_off_interrupts_a_busy_main_loop():
    switch = FakeSwitch(on=True)
    control = SwitchControl(switch)
    control.wait_until_on()
    companion, said = make_companion(control)
    heard = iter(["what time is it"])

    def next_text():
        text = next(heard, None)
        if text is None:
            flip_later(switch, False)
            time.sleep(3)  # stands in for a long listen() or LLM call
        return text

    started = time.monotonic()
    try:
        run_loop(companion, control, next_text)
    finally:
        control.close()
    if hasattr(signal, "pthread_kill"):  # Linux: the blocking call is cut short
        assert time.monotonic() - started < 2
    assert companion.state is State.ABORTED
    assert said == ["answer to what time is it", "Switched off. Shutting down."]
    assert control.turned_off.is_set()


def test_switch_off_aborts_from_sleep():
    switch = FakeSwitch(on=True)
    control = SwitchControl(switch)
    control.wait_until_on()
    companion, said = make_companion(control)
    companion.on_text("go to sleep")
    assert companion.state is State.SLEEP

    def next_text():
        flip_later(switch, False)
        time.sleep(3)

    try:
        run_loop(companion, control, next_text)
    finally:
        control.close()
    assert companion.state is State.ABORTED and said[-1] == "Switched off. Shutting down."


def test_switch_off_before_arming_is_ignored():
    """Flicking it while the program waits at startup doesn't shut it down."""
    switch = FakeSwitch(on=True)
    control = SwitchControl(switch)
    switch.set(False)
    assert not control.turned_off.is_set()


def test_mayday_still_works_and_is_not_a_switch_exit():
    control = SwitchControl(FakeSwitch(on=True))
    control.wait_until_on()
    companion, said = make_companion(control)
    heard = iter(["mayday"])
    run_loop(companion, control, lambda: next(heard))
    control.close()
    assert companion.state is State.ABORTED and said == ["Shutting down."]
    assert not control.turned_off.is_set()


def test_no_gpio_means_no_switch(monkeypatch):
    # On this test machine gpiozero/GPIO isn't available, so it falls back.
    assert open_switch(enabled=False) is None
    monkeypatch.setattr(config, "SWITCH_PIN", None)
    assert open_switch() is None
