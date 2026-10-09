"""Plays songs from the music folder with mpv, in the background.

mpv runs as its own process, so Rabbit keeps listening while a song plays,
and is controlled through its IPC socket: pause, resume, and turning the
volume down ("ducking") while Rabbit speaks. Needs: sudo apt install mpv.
Without mpv, nothing plays and Rabbit says so.
"""

import json
import logging
import os
import random
import re
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import config
import display

log = logging.getLogger("music")

SOCKET = "/tmp/rabbit-mpv.sock"


def list_songs(folder=config.MUSIC_DIR):
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in config.MUSIC_EXTENSIONS)


def song_title(path):
    """Speakable title from a file name:
    "Bekhayali (Arijit Singh Version) - Kabir Singh (128 kbps).mp3"
    -> "Bekhayali, Arijit Singh Version, from Kabir Singh"."""
    name = Path(path).stem
    name = re.sub(r"[\(\[][^)\]]*\b(kbps|320|128|mp3|lyrics?|official|audio|video|hd)\b[^)\]]*[\)\]]", "",
                  name, flags=re.IGNORECASE)
    name = re.sub(r"\s*[\(\[]\s*([^)\]]+?)\s*[\)\]]", r", \1,", name)   # (Version) -> , Version,
    name = re.sub(r"\s+-\s+", " from ", name)
    name = name.replace("_", " ")
    return re.sub(r"\s*,\s*(,|$)", r"\1", re.sub(r"\s{2,}", " ", name)).strip(" ,")


class MusicPlayer:
    def __init__(self, folder=config.MUSIC_DIR, spawn=None, send=None):
        """`spawn(args)` starts mpv and `send(command)` talks to it; both can be
        replaced in tests."""
        self.folder = folder
        self._spawn = spawn or (lambda args: subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                                              stderr=subprocess.DEVNULL))
        self._send = send or _send_to_mpv
        self._lock = threading.Lock()
        self._process = None
        self._current = None        # Path of the song playing (or paused)
        self._pending = None        # chosen song, started once Rabbit has finished speaking
        self._paused = False
        self._ducks = 0             # how many things want the volume down right now
        self._duck_until = 0.0

    # ------------------------------------------------ choosing and starting
    def choose_random(self, avoid_current=False):
        """Picks a random song to start after Rabbit's reply. Returns its
        title, or None if the folder has no songs."""
        songs = list_songs(self.folder)
        if avoid_current and self._current in songs and len(songs) > 1:
            songs.remove(self._current)
        if not songs:
            return None
        self._pending = random.choice(songs)
        return song_title(self._pending)

    def start_pending(self):
        """Starts the chosen song (called after Rabbit says "Playing ...")."""
        with self._lock:
            song, self._pending = self._pending, None
            if song is None:
                return
            self._stop_locked()
            try:
                os.remove(SOCKET)
            except OSError:
                pass
            args = ["mpv", "--no-video", "--really-quiet", f"--input-ipc-server={SOCKET}",
                    f"--volume={config.MUSIC_VOLUME}", str(song)]
            try:
                self._process = self._spawn(args)
            except FileNotFoundError:
                log.error("mpv is not installed: sudo apt install mpv")
                return
            self._current, self._paused = song, False
            log.info("Playing %s", song.name)
            display.screen.hold("music", True)
            threading.Thread(target=self._wait_for_end, args=(self._process,), name="music",
                             daemon=True).start()

    def _wait_for_end(self, process):
        process.wait()
        with self._lock:
            if process is self._process:  # not replaced by another song
                self._process, self._current = None, None
                display.screen.hold("music", False)
                log.info("Song finished")

    # ------------------------------------------------ controls
    def is_active(self):
        """A song is playing or paused."""
        return self._process is not None and self._process.poll() is None

    def is_playing(self):
        return self.is_active() and not self._paused

    def now_playing(self):
        return song_title(self._current) if self.is_active() else None

    def pause(self):
        if self.is_playing():
            self._command("set_property", "pause", True)
            self._paused = True
            display.screen.hold("music", False)

    def resume(self):
        if self.is_active() and self._paused:
            self._command("set_property", "pause", False)
            self._paused = False
            display.screen.hold("music", True)

    def stop(self):
        with self._lock:
            self._pending = None
            self._stop_locked()

    def _stop_locked(self):
        process, self._process, self._current = self._process, None, None
        if process is not None and process.poll() is None:
            process.terminate()
            log.info("Music stopped")
        display.screen.hold("music", False)

    # ------------------------------------------------ ducking
    @contextmanager
    def ducked(self):
        """Volume down for the duration of a `with` block (while Rabbit speaks)."""
        self._duck(+1)
        try:
            yield
        finally:
            self._duck(-1)

    def duck_for(self, seconds):
        """Volume down for a while: after "Rabbit", so the command can be heard."""
        self._duck_until = time.monotonic() + seconds
        self._apply_volume()
        threading.Timer(seconds + 0.1, self._apply_volume).start()

    def is_listening(self):
        """True shortly after "Rabbit" was heard: the next sentence is a request."""
        return time.monotonic() < self._duck_until

    def _duck(self, change):
        self._ducks += change
        self._apply_volume()

    def _apply_volume(self):
        if not self.is_active():
            return
        low = self._ducks > 0 or time.monotonic() < self._duck_until
        self._command("set_property", "volume", config.MUSIC_DUCK_VOLUME if low else config.MUSIC_VOLUME)

    def _command(self, *args):
        try:
            self._send(list(args))
        except Exception as e:
            log.debug("mpv command %s failed: %s", args, e)

    def close(self):
        self.stop()


def _send_to_mpv(command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        s.connect(SOCKET)
        s.sendall((json.dumps({"command": command}) + "\n").encode())


# The one player the whole program uses.
player = MusicPlayer()
