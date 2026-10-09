"""The rocker switch on a GPIO pin.

ON  -> the companion starts in RUNNING (if it was off at startup, the program
       waits for it to be switched on)
OFF -> ABORTED: the program ends

Wiring: one switch terminal to GPIO17 (physical pin 11), the other to any
ground pin (e.g. physical pin 9). The pin's internal pull-up keeps it high
while the switch is open (off); closing it (on) pulls it to ground.
"""

import logging
import threading
import time

import config

log = logging.getLogger("switch")


class RockerSwitch:
    """Reads the switch through gpiozero. Changes shorter than
    SWITCH_DEBOUNCE seconds are ignored, so contact bounce or a bumped
    switch doesn't shut the program down."""

    def __init__(self, pin=config.SWITCH_PIN):
        from gpiozero import Button

        self._button = Button(pin, pull_up=True, bounce_time=0.05)
        self._on_change = None
        self._button.when_pressed = lambda: self._changed(True)
        self._button.when_released = lambda: self._changed(False)
        log.info("Rocker switch on GPIO%d is %s", pin, "ON" if self.is_on() else "OFF")

    def is_on(self):
        return self._button.is_pressed

    def on_change(self, callback):
        """`callback(is_on)` runs in a gpiozero thread after every settled change."""
        self._on_change = callback

    def wait_until_on(self, stop=None):
        """Blocks until the switch is ON. Returns False if `stop` (an Event)
        was set first."""
        while not self.is_on():
            if stop is not None and stop.is_set():
                return False
            self._button.wait_for_press(timeout=0.5)
        return True

    def _changed(self, is_on):
        # Wait a moment and confirm, so a glitch isn't taken as a real change.
        time.sleep(config.SWITCH_DEBOUNCE)
        if self.is_on() != is_on:
            return
        log.info("Switch turned %s", "ON" if is_on else "OFF")
        if self._on_change:
            self._on_change(is_on)

    def close(self):
        self._button.close()


class FakeSwitch:
    """For tests and machines without GPIO: flip it with set()."""

    def __init__(self, on=True):
        self._on = on
        self._on_change = None
        self._event = threading.Event()
        if on:
            self._event.set()

    def is_on(self):
        return self._on

    def on_change(self, callback):
        self._on_change = callback

    def set(self, on):
        self._on = on
        (self._event.set if on else self._event.clear)()
        if self._on_change:
            self._on_change(on)

    def wait_until_on(self, stop=None):
        while not self._event.wait(0.05):
            if stop is not None and stop.is_set():
                return False
        return True

    def close(self):
        pass


def open_switch(enabled=True):
    """The real switch, or None if disabled or the GPIO library or hardware
    isn't available (e.g. text mode on a laptop)."""
    if not enabled or config.SWITCH_PIN is None:
        return None
    try:
        return RockerSwitch()
    except Exception as e:
        log.warning("No rocker switch (%s); running without it", e)
        return None
