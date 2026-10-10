"""Music: a random song from the music folder, plus pause / resume / next /
stop, ducking while Rabbit speaks, and ignoring lyrics."""

import threading

import pytest

import config
import music
from agents.base import Agent
from agents.conductor import Conductor, music_label
from agents.music import NO_SONGS_REPLY, Music
from conftest import FakeLLM
from main import Companion, State
from music import MusicPlayer, song_title


class FakeProcess:
    def __init__(self, args):
        self.args = args
        self._done = threading.Event()

    def poll(self):
        return 0 if self._done.is_set() else None

    def wait(self):
        self._done.wait()

    def terminate(self):
        self._done.set()

    finish = terminate  # the song ends by itself


@pytest.fixture
def songs(tmp_path):
    for name in ("Bekhayali (Arijit Singh Version) - Kabir Singh (128 kbps).mp3", "Kesariya.mp3", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    return tmp_path


@pytest.fixture
def player(songs, monkeypatch):
    sent, started = [], []

    def spawn(args):
        started.append(FakeProcess(args))
        return started[-1]

    p = MusicPlayer(folder=songs, spawn=spawn, send=sent.append)
    p.sent, p.started = sent, started
    monkeypatch.setattr(music, "player", p)
    yield p
    p.stop()


def test_song_titles():
    assert song_title("Bekhayali (Arijit Singh Version) - Kabir Singh (128 kbps).mp3") == (
        "Bekhayali, Arijit Singh Version, from Kabir Singh")
    assert song_title("Kesariya.mp3") == "Kesariya"
    assert song_title("tum_hi_ho [Official Audio].mp3") == "tum hi ho"


def test_random_song_starts_after_the_reply(player):
    title = player.choose_random()
    assert title in ("Bekhayali, Arijit Singh Version, from Kabir Singh", "Kesariya")
    assert player.started == []          # not while Rabbit is still saying "Playing ..."
    player.start_pending()
    args = player.started[0].args
    assert args[0] == "mpv" and "--no-video" in args and args[-1].endswith(".mp3")
    assert player.is_playing() and player.now_playing() == title


def test_next_song_is_a_different_one(player):
    player.choose_random()
    player.start_pending()
    first = player.now_playing()
    for _ in range(10):
        assert player.choose_random(avoid_current=True) != first


def test_pause_resume_stop(player):
    player.choose_random()
    player.start_pending()
    player.pause()
    assert player.sent[-1] == ["set_property", "pause", True] and not player.is_playing()
    player.resume()
    assert player.sent[-1] == ["set_property", "pause", False] and player.is_playing()
    player.stop()
    assert not player.is_active()


def test_song_ending_by_itself(player):
    player.choose_random()
    player.start_pending()
    player.started[0].finish()
    for _ in range(100):
        if not player.is_active():
            break
        threading.Event().wait(0.01)
    assert not player.is_active() and player.now_playing() is None


def test_volume_ducks_while_rabbit_speaks(player):
    player.choose_random()
    player.start_pending()
    with player.ducked():
        assert player.sent[-1] == ["set_property", "volume", config.MUSIC_DUCK_VOLUME]
    assert player.sent[-1] == ["set_property", "volume", config.MUSIC_VOLUME]


def test_empty_folder(tmp_path):
    assert MusicPlayer(folder=tmp_path, spawn=None, send=None).choose_random() is None
    assert MusicPlayer(folder=tmp_path / "missing").choose_random() is None


# ---------------------------------------------------------------- the agent
def test_music_agent(player):
    agent = Music()
    assert agent.handle("play a song").startswith("Playing ")
    player.start_pending()
    assert agent.handle("what song is this?") == f"This is {player.now_playing()}."
    assert agent.handle("pause the music") == "Paused."
    assert agent.handle("resume") == "Resuming."
    assert agent.handle("next song").startswith("Playing ")
    assert agent.handle("stop the music") == "Okay, music stopped."
    assert agent.handle("stop the music") == "No music is playing."


def test_music_agent_without_songs(tmp_path, monkeypatch):
    monkeypatch.setattr(music, "player", MusicPlayer(folder=tmp_path))
    assert Music().handle("play a song") == NO_SONGS_REPLY


# ---------------------------------------------------------------- routing
class Echo(Agent):
    def __init__(self, name):
        super().__init__(None, None)
        self.name = name

    def handle(self, text):
        return self.name


@pytest.mark.parametrize("text,label", [
    ("play a song", "music"),
    ("Play some music", "music"),
    ("can you play something", "music"),
    ("next song please", "music"),
    ("stop the music", "music"),
    ("remind me to play cricket at 6", "schedule"),
    ("what is the capital of France", "answer"),
])
def test_routing(db, player, text, label):
    agents = {n: Echo(n) for n in ("schedule", "search", "remember", "answer", "music")}
    assert Conductor(FakeLLM(), db, agents, use_llm=False).handle(text) == label


def test_bare_stop_is_for_the_music_only_while_it_plays():
    assert music_label("stop", playing=True) == "music"
    assert music_label("stop", playing=False) is None
    assert music_label("stop worrying about the exam tomorrow", playing=True) is None


# ---------------------------------------------------------------- with the state machine
def companion(answer):
    said = []
    return Companion(answer, said.append, state=State.RUNNING), said


def test_lyrics_are_ignored_while_a_song_plays(player):
    player.choose_random()
    player.start_pending()
    asked = []
    c, _ = companion(lambda t: asked.append(t) or "ok")
    c.on_text("Bekhayali mein bhi tera hi khayal aaye")
    assert asked == []
    c.on_text("stop the music")
    c.on_text("Rabbit, what time is it?")
    assert asked == ["stop the music", "what time is it"]


def test_rabbit_lowers_the_music_to_listen(player):
    player.choose_random()
    player.start_pending()
    asked = []
    c, said = companion(lambda t: asked.append(t) or "ok")
    c.on_text("Rabbit")
    assert said == [config.WAKE_REPLY] and player.is_listening()
    assert ["set_property", "volume", config.MUSIC_DUCK_VOLUME] in player.sent
    c.on_text("what time is it")          # no "Rabbit" needed right after
    assert asked == ["what time is it"]


def test_song_starts_once_the_reply_is_said(player):
    order = []
    c = Companion(lambda t: player.choose_random() and "Playing a song.",
                  lambda reply: order.append(("said", player.is_active())), state=State.RUNNING)
    c.on_text("play a song")
    assert order == [("said", False)] and player.is_playing()


def test_no_sleep_timeout_during_music(player):
    player.choose_random()
    player.start_pending()
    c, said = companion(lambda t: "ok")
    c.last_activity -= config.SILENCE_TIMEOUT + 1
    c.check_timeout()
    assert c.state is State.RUNNING and said == []
