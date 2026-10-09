"""Status LEDs.

    red     on while Rabbit is on (SLEEP or RUNNING); off when switched OFF or exited
    white   on while Rabbit is thinking (working out a reply)
    yellow  on while the Researcher is using the internet

Each LED: GPIO pin -> resistor -> LED long leg (+); LED short leg (-) -> GND.
Pins are set in config.py. Without GPIO (a laptop, or gpiozero missing) every
call does nothing, so the rest of the program doesn't need to care.
"""

import logging
import threading
from contextlib import contextmanager

import config

log = logging.getLogger("leds")


class StatusLEDs:
    def __init__(self, leds=None):
        """`leds` maps "power", "thinking", "internet" to objects with on()
        and off(); missing ones are skipped."""
        self._leds = leds or {}
        self._lock = threading.Lock()
        self._users = {"thinking": 0, "internet": 0}  # nested/overlapping uses

    def power(self, on):
        self._set("power", on)

    @contextmanager
    def thinking(self):
        """Lights the white LED for the duration of a `with` block."""
        with self._held("thinking"):
            yield

    @contextmanager
    def internet(self):
        """Lights the yellow LED for the duration of a `with` block."""
        with self._held("internet"):
            yield

    @contextmanager
    def _held(self, name):
        with self._lock:
            self._users[name] += 1
            self._set(name, True)
        try:
            yield
        finally:
            with self._lock:
                self._users[name] -= 1
                if self._users[name] == 0:
                    self._set(name, False)

    def _set(self, name, on):
        led = self._leds.get(name)
        if led is None:
            return
        try:
            led.on() if on else led.off()
        except Exception as e:
            log.debug("LED %s: %s", name, e)

    def close(self):
        """All off, pins released."""
        for led in self._leds.values():
            try:
                led.off()
                led.close()
            except Exception:
                pass
        self._leds = {}


def open_leds(enabled=True):
    """StatusLEDs on the configured pins; a do-nothing one without GPIO."""
    pins = {"power": config.LED_POWER_PIN, "thinking": config.LED_THINKING_PIN,
            "internet": config.LED_INTERNET_PIN}
    pins = {name: pin for name, pin in pins.items() if pin is not None}
    if not enabled or not pins:
        return StatusLEDs()
    try:
        from gpiozero import LED

        leds = {name: LED(pin) for name, pin in pins.items()}
    except Exception as e:
        log.warning("No status LEDs (%s); running without them", e)
        return StatusLEDs()
    log.info("Status LEDs: %s", ", ".join(f"{name} on GPIO{pin}" for name, pin in pins.items()))
    return StatusLEDs(leds)


# The one set of LEDs the whole program uses; main.py replaces it at startup.
status = StatusLEDs()
