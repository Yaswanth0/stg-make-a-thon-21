"""Archivist: saves facts the user tells it."""

import re

from agents.base import Agent, to_second_person

SYSTEM_PROMPT = """You store facts for a voice assistant. Rewrite what the user wants remembered as one short,
standalone fact about "the user". Keep every name, number and code exactly as given.
Reply with JSON only: {"fact": "..."}

Examples:
"remember my locker code is 4521" -> {"fact": "The user's locker code is 4521."}
"note that I parked on level 3, spot B12" -> {"fact": "The user parked on level 3, spot B12."}
"my sister Priya's birthday is on May 4th" -> {"fact": "The user's sister Priya has her birthday on May 4th."}"""

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

        data = self.llm.chat_json(SYSTEM_PROMPT, text) or {}
        fact = str(data.get("fact") or "").strip()
        if not fact:
            # The LLM failed; store the user's own words rather than lose them.
            fact = f"The user said: {said}."
        self.db.add_memory(fact)
        # Read back the user's own words, so a misheard number is caught now.
        return f"Got it. I'll remember that {to_second_person(said)}."
