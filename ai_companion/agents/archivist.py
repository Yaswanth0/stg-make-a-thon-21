"""Archivist: saves facts the user tells it."""

import re

import display
import prompts
import rai
from agents.base import Agent, to_second_person

SYSTEM_PROMPT = prompts.ARCHIVIST

LEADING = re.compile(
    r"^(please |can you |could you )?(remember|note|save|memorize|don't forget|do not forget)"
    r"( down)?( that)?[\s,:]*",
    re.IGNORECASE,
)


def strip_command(text):
    """"Remember that my locker code is 4521." -> "my locker code is 4521"."""
    return LEADING.sub("", text.strip()).rstrip(".!?").strip()


class Archivist(Agent):
    name = "archivist"

    def handle(self, text):
        said = strip_command(text)
        if not said:
            return "What should I remember?"
        if rai.private:
            return "Private mode is on, so I won't save that. Say: private mode off, to let me remember again."

        data = self.llm.chat_json(SYSTEM_PROMPT, text) or {}
        fact = str(data.get("fact") or "").strip()
        if not fact:
            # The LLM failed; store the user's own words rather than lose them.
            fact = f"The user said: {said}."
        self.db.add_memory(fact)
        display.screen.flash("happy")  # ^ ^ and a heart on the OLED
        # Read back the user's own words, so a misheard number is caught now.
        return f"Got it. I'll remember that {to_second_person(said)}."
