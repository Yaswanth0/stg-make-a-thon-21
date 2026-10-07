"""Responder: answers questions using recent conversation, saved facts and
upcoming reminders as context."""

import re
from datetime import datetime

import config
from agents.base import Agent
from timeparse import spoken_time

# Questions like "what do you know about me?" get all recent facts, not just
# the ones that share a keyword.
ABOUT_ME = re.compile(r"\b(about me|do you (know|remember)|have i told you|what have i)\b")


class Responder(Agent):
    name = "responder"

    def handle(self, text):
        reply = self.llm.chat(self.system_prompt(text), text, history=self.history())
        return reply or "Sorry, my language model is not responding."

    def history(self):
        messages = []
        for turn in self.db.recent_turns(config.HISTORY_TURNS, config.HISTORY_MAX_AGE):
            if turn["user_text"]:
                messages.append({"role": "user", "content": turn["user_text"]})
            messages.append({"role": "assistant", "content": turn["reply"]})
        return messages

    def system_prompt(self, text):
        now = datetime.now()
        parts = [
            config.SYSTEM_PROMPT,
            "Your replies are spoken aloud: plain sentences, no lists, no markdown, no emoji.",
            f"It is now {now:%A}, {now:%B} {now.day}, {now:%Y}, {spoken_time(now, now, clock_only=True)}.",
        ]

        facts = self.db.search_memories(text, config.MEMORY_MATCHES)
        if ABOUT_ME.search(text.lower()):
            facts = list(dict.fromkeys(facts + self.db.recent_memories(8)))
        if facts:
            parts.append(
                "Facts the user asked you to remember (use them if relevant, never invent others):\n"
                + "\n".join(f"- {f}" for f in facts)
            )

        reminders = self.db.pending_reminders(limit=3)
        if reminders:
            parts.append(
                "The user's upcoming reminders:\n"
                + "\n".join(f"- {r['task']}, {spoken_time(r['due_at'], now)}" for r in reminders)
            )
        return "\n\n".join(parts)
