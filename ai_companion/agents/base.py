"""What every agent has in common."""

import re


class Agent:
    name = ""

    def __init__(self, llm, db):
        self.llm = llm
        self.db = db

    def handle(self, text):
        """Returns the reply to speak."""
        raise NotImplementedError

    def awaiting_followup(self):
        """True if this agent asked a question and wants the next sentence
        too (the Scheduler asking "When should I remind you?")."""
        return False


# "my" -> "your" so a fact can be read back to the user.
_PRONOUNS = {
    "i": "you", "me": "you", "my": "your", "mine": "yours", "myself": "yourself",
    "i'm": "you're", "im": "you're", "am": "are", "i've": "you've", "i'll": "you'll",
    "i'd": "you'd", "we": "you", "our": "your", "us": "you",
}


def to_second_person(text):
    def swap(match):
        word = match.group(0)
        return _PRONOUNS.get(word.lower(), word)

    return re.sub(r"[A-Za-z']+", swap, text)
