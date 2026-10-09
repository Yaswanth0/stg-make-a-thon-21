"""Status LEDs: red = on, white = thinking, yellow = Researcher on the internet."""

import pytest

import leds
from agents.researcher import Researcher
from conftest import FakeLLM
from leds import StatusLEDs, open_leds
from main import Companion, State


class FakeLED:
    def __init__(self, name, events):
        self.name, self.events, self.lit = name, events, False

    def on(self):
        self.lit = True
        self.events.append((self.name, "on"))

    def off(self):
        self.lit = False
        self.events.append((self.name, "off"))

    def close(self):
        pass


@pytest.fixture
def fake_leds(monkeypatch):
    events = []
    board = {name: FakeLED(name, events) for name in ("power", "thinking", "internet")}
    monkeypatch.setattr(leds, "status", StatusLEDs(board))
    return board, events


def test_red_follows_on_and_off(fake_leds):
    board, _ = fake_leds
    companion = Companion(lambda text: "hi", lambda text: None, state=State.OFF)
    assert not board["power"].lit
    companion.switch_on()
    assert board["power"].lit
    companion.on_text("go to sleep")
    assert board["power"].lit  # asleep still counts as on
    companion.switch_off()
    assert not board["power"].lit
    companion.switch_on()
    companion.on_text("mayday")
    assert not board["power"].lit


def test_white_only_while_thinking(fake_leds):
    board, events = fake_leds
    lit_while_answering, lit_while_speaking = [], []

    def answer(text):
        lit_while_answering.append(board["thinking"].lit)
        return "four"

    def say(text):
        lit_while_speaking.append(board["thinking"].lit)

    companion = Companion(answer, say, state=State.RUNNING)
    companion.on_text("what is two plus two")
    assert lit_while_answering == [True] and lit_while_speaking == [False]


def test_white_goes_off_even_if_interrupted(fake_leds):
    board, _ = fake_leds

    def answer(text):
        raise KeyboardInterrupt  # the switch turned OFF mid-thought

    with pytest.raises(KeyboardInterrupt):
        Companion(answer, lambda t: None, state=State.RUNNING).on_text("hello")
    assert not board["thinking"].lit


def test_yellow_while_researching(fake_leds, db):
    board, _ = fake_leds
    seen = []

    def search(query, n):
        seen.append(board["internet"].lit)
        return [{"title": "T", "body": "B", "href": ""}]

    researcher = Researcher(FakeLLM(text_reply="ok"), db, search=search, online=lambda: True,
                            fetch_page=lambda u, q: "", weather=None)
    researcher.handle("search for something")
    assert seen == [True] and not board["internet"].lit


def test_yellow_off_when_offline(fake_leds, db):
    board, events = fake_leds
    Researcher(FakeLLM(), db, online=lambda: False).handle("search for news")
    assert ("internet", "on") in events and not board["internet"].lit


def test_overlapping_uses_keep_the_led_on():
    events = []
    status = StatusLEDs({"thinking": FakeLED("thinking", events)})
    with status.thinking():
        with status.thinking():
            pass
        assert events[-1] == ("thinking", "on")  # still held by the outer block
    assert events[-1] == ("thinking", "off")


def test_without_gpio_leds_do_nothing():
    status = open_leds(enabled=False)
    status.power(True)
    with status.thinking(), status.internet():
        pass
    status.close()
