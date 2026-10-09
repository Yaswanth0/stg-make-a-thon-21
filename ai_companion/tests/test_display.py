"""OLED: the rabbit's face follows what Rabbit is doing."""

import pytest

import display
from display import PRIORITY, RabbitDisplay, open_display, rabbit_image

pytest.importorskip("PIL")

MOODS = ["awake", "asleep", "hearing", "thinking", "searching", "happy", "alert", "confused"]


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
def screen(monkeypatch):
    s = RabbitDisplay(FakeOLED(), animate=False)
    monkeypatch.setattr(display, "screen", s)
    return s


# ---------------------------------------------------------------- drawings
def test_every_mood_is_a_distinct_drawing():
    images = {m: rabbit_image(m, t=0.33).tobytes() for m in MOODS}
    assert len(set(images.values())) == len(MOODS)
    for mood, data in images.items():
        lit = sum(bin(b).count("1") for b in data)
        assert 200 < lit < 3000, mood  # a face, not blank or solid


def test_mouth_moves_while_speaking():
    frames = {rabbit_image("awake", speaking=True, t=n * display.FRAME_SECONDS).tobytes() for n in range(4)}
    assert len(frames) == 2  # open, closed


def test_animations_change_over_time():
    for mood in ("thinking", "searching", "hearing", "asleep"):
        frames = {rabbit_image(mood, t=n * display.FRAME_SECONDS).tobytes() for n in range(20)}
        assert len(frames) > 1, mood


# ---------------------------------------------------------------- which face
def test_base_face_follows_state(screen):
    assert screen.current()[0] == "off"
    screen.show(True, awake=True)
    assert screen.current() == ("awake", False)
    screen.show(True, awake=False)
    assert screen.current()[0] == "asleep"


def test_moods_last_for_their_block_and_follow_priority(screen):
    screen.show(True)
    with screen.mood("thinking"):
        assert screen.current()[0] == "thinking"
        with screen.mood("searching"):
            assert screen.current()[0] == "searching"  # searching outranks thinking
        assert screen.current()[0] == "thinking"
    assert screen.current()[0] == "awake"
    assert PRIORITY.index("alert") == 0


def test_speaking_combines_with_the_face(screen):
    screen.show(True)
    with screen.mood("speaking"):
        assert screen.current() == ("awake", True)
        screen.flash("happy")
        assert screen.current() == ("happy", True)


def test_flash_wears_off(screen):
    screen.show(True)
    screen.flash("happy", seconds=2)
    now = display.time.monotonic()
    assert screen.current(now)[0] == "happy"
    assert screen.current(now + 3)[0] == "awake"


def test_asleep_ignores_noise_but_shows_reminders(screen):
    screen.show(True, awake=False)
    with screen.mood("hearing"):
        assert screen.current()[0] == "asleep"
    with screen.mood("alert"):
        assert screen.current()[0] == "alert"


def test_off_shows_nothing(screen):
    with screen.mood("thinking"):
        assert screen.current()[0] == "off"


# ---------------------------------------------------------------- drawing to the device
def test_device_updates(screen, monkeypatch):
    monkeypatch.setattr(display.time, "monotonic", lambda: 100.0)  # freeze animations
    device = screen._device
    screen.show(True)
    assert [e[0] for e in device.events] == ["face", "on"]
    screen.refresh()
    assert len(device.events) == 2  # same picture: not sent again
    with screen.mood("thinking"):
        pass
    assert device.events[-1][0] == "face"
    screen.show(False)
    assert device.events[-1] == ("off",)
    screen.close()
    assert device.events[-1] == ("cleanup",)


def test_without_a_screen_nothing_happens():
    s = open_display(enabled=False)
    s.show(True)
    with s.mood("thinking"):
        s.flash("happy")
    s.close()


# ---------------------------------------------------------------- triggered by Rabbit's parts
def test_thinking_while_answering(screen):
    from main import Companion, State

    seen = []
    companion = Companion(lambda t: seen.append(screen.current()[0]) or "four", lambda t: None,
                          state=State.RUNNING)
    companion.on_text("what is two plus two")
    assert seen == ["thinking"] and screen.current()[0] == "awake"


def test_searching_while_researching(screen, db):
    from agents.researcher import Researcher
    from conftest import FakeLLM

    screen.show(True)
    seen = []

    def search(query, n):
        seen.append(screen.current()[0])
        return [{"title": "T", "body": "B", "href": ""}]

    Researcher(FakeLLM(text_reply="ok"), db, search=search, news=lambda q, n: [], online=lambda: True,
               weather=None, fetch_page=lambda u, q: "").handle("search for x")
    assert seen == ["searching"]


def test_happy_when_a_fact_is_saved(screen, db):
    from agents.archivist import Archivist
    from conftest import FakeLLM

    screen.show(True)
    Archivist(FakeLLM([{"fact": "The user's age is 24."}]), db).handle("remember I am 24")
    assert screen.current()[0] == "happy"


def test_alert_while_a_reminder_is_spoken(screen, db):
    from datetime import datetime

    from agents.scheduler import ReminderWatcher

    screen.show(True, awake=False)  # reminders go off while asleep too
    seen = []
    db.add_reminder("call Mom", datetime.now())
    ReminderWatcher(db, lambda message: seen.append(screen.current()[0])).check()
    assert seen == ["alert"]
