"""Music: plays a random song from the music folder, and pauses, resumes,
skips or stops it. No LLM needed."""

import re

import music
from agents.base import Agent

STOP = re.compile(r"\b(stop|turn off|switch off|enough)\b")
PAUSE = re.compile(r"\b(pause|hold on|wait)\b")
RESUME = re.compile(r"\b(resume|continue|unpause|carry on|keep playing)\b")
NEXT = re.compile(r"\b(next|another|skip|change|different)\b")
WHATS_PLAYING = re.compile(r"\b(what('s| is) (this|playing)|which song|what song|name of (this|the) song)\b")

NO_SONGS_REPLY = "I don't have any songs yet. Add MP3 files to my music folder."


class Music(Agent):
    name = "music"

    def __init__(self, llm=None, db=None, player=None):
        super().__init__(llm, db)
        self._player = player

    @property
    def player(self):
        return self._player or music.player

    def handle(self, text):
        t = text.lower()
        player = self.player
        if WHATS_PLAYING.search(t):
            title = player.now_playing()
            return f"This is {title}." if title else "Nothing is playing right now."
        if STOP.search(t):
            if not player.is_active():
                return "No music is playing."
            player.stop()
            return "Okay, music stopped."
        if PAUSE.search(t):
            player.pause()
            return "Paused."
        if RESUME.search(t) and player.is_active():
            player.resume()
            return "Resuming."
        title = player.choose_random(avoid_current=bool(NEXT.search(t)))
        if title is None:
            return NO_SONGS_REPLY
        return f"Playing {title}."  # the song starts once this has been said
