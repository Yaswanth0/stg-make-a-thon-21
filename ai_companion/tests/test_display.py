"""OLED: rabbit face while on (eyes closed when asleep), blank when off."""

import pytest

import display
from display import RabbitDisplay, open_display, rabbit_image
from main import Companion, State

pytest.importorskip("PIL")


class FakeOLED:
    mode = "1"

    def __init__(self):
        self.events = []

    def display(self, image):
        self.events.append(("face", image.tobytes()))

    def show(self):
        self.events.append(("on",))

    def hide(self):
        self.events.append(("off",))

    def cleanup(self):
        self.events.append(("cleanup",))


@pytest.fixture
def oled(monkeypatch):
    device = FakeOLED()
    monkeypatch.setattr(display, "screen", RabbitDisplay(device))
    return device


def faces(device):
    return [e[1] for e in device.events if e[0] == "face"]


def test_face_has_pixels_and_sleeping_differs():
    awake, asleep = rabbit_image(True), rabbit_image(False)
    assert awake.size == (128, 64)
    assert 300 < sum(1 for p in awake.getdata() if p) < 3000  # a drawing, not blank or solid
    assert awake.tobytes() != asleep.tobytes()


def test_screen_follows_the_states(oled):
    awake, asleep = rabbit_image(True).tobytes(), rabbit_image(False).tobytes()
    companion = Companion(lambda t: "hi", lambda t: None, state=State.OFF)
    assert oled.events == [("off",)]

    companion.switch_on()
    assert faces(oled) == [awake] and oled.events[-1] == ("on",)

    companion.on_text("go to sleep")
    assert faces(oled)[-1] == asleep

    companion.on_text("rabbit")
    assert faces(oled)[-1] == awake

    companion.switch_off()
    assert oled.events[-1] == ("off",)


def test_unchanged_state_doesnt_redraw(oled):
    companion = Companion(lambda t: "four", lambda t: None, state=State.RUNNING)
    drawn = len(oled.events)
    companion.on_text("what is two plus two")
    assert len(oled.events) == drawn


def test_screen_cleared_on_close():
    device = FakeOLED()
    screen = RabbitDisplay(device)
    screen.show(True)
    screen.close()
    assert device.events[-1] == ("cleanup",)
    screen.show(True)  # after close: ignored, no error


def test_without_a_screen_nothing_happens():
    screen = open_display(enabled=False)
    screen.show(True)
    screen.close()
