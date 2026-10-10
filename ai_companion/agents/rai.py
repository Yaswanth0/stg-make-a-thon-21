"""Responsible AI, by voice: honesty about what Rabbit is, control over your
data, and accessibility. No LLM: these answers must be exact.

    "Are you a human?"               -> an honest answer: an AI that can make mistakes
    "What data do you store?"        -> what is saved, with real counts, and where
    "What do you know about me?"     -> reads the saved facts
    "Forget my locker code"          -> deletes that fact, after yes / no
    "Forget everything"              -> deletes facts and history, after yes / no
    "Private mode" / "...off"        -> while on, nothing is saved
    "Repeat that"                    -> says the last reply again
    "Speak slower" / "speak faster"  -> remembered across restarts
"""

import re
import time

import config
import rai
import semantic
from agents.base import Agent
from agents.responder import fact_to_speech
from db import keywords

WHO = re.compile(r"\b(are you (a |an )?(human|real|person|robot|ai|machine|alive|sentient|conscious)|"
                 r"am i talking to (a |an )?(real )?(human|person|machine|robot|ai)|what are you|"
                 r"who (made|built|created|programmed) you)\b")
DATA = re.compile(r"\b(what (data|information|info) do you (store|keep|save|collect|have)|"
                  r"what do you (store|record|save|collect)|are you (recording|listening|always listening|spying)|"
                  r"do you (send|share|upload) (my|anything|data)|where (is|do you keep) my data|"
                  r"is my (data|information|privacy) (safe|private|secure))\b")
HOW = re.compile(r"\b(how do you work|how do you (decide|make decisions|answer)|can i trust you|"
                 r"can you make mistakes|are you (always )?(right|accurate))\b")
KNOW_ME = re.compile(r"\b(what do you know about me|what have you (saved|stored|remembered)( about me)?|"
                     r"what do you remember about me|list my (saved )?(facts|memories))\b")
FORGET_ALL = re.compile(r"\b(forget|delete|erase|wipe|clear) (everything|all (my )?(data|history|memories|facts|"
                        r"conversations)|my (data|history|memories|conversation history)|what you know about me)\b")
FORGET_ONE = re.compile(r"^(?:please )?(?:forget|stop remembering|delete the fact) (?:about |that )?(?:my |the )?(.+)$")
PRIVATE_OFF = re.compile(r"\b(private mode off|turn off private mode|exit private mode|stop private mode|"
                         r"end private mode|leave private mode|you can (save|remember|record) again)\b")
PRIVATE_ON = re.compile(r"\b(private mode|incognito|go private|stop (saving|recording|remembering)|"
                        r"don'?t (save|remember|record) (this|anything|what i say))\b")
REPEAT = re.compile(r"^(?:please |sorry,? |pardon,? )?(repeat( that| it| yourself)?|say (that|it) again|come again|"
                    r"what did you (just )?say|pardon|i didn'?t (hear|catch) (that|you))\b")
SLOWER = re.compile(r"\b((speak|talk) (more )?(slower|slowly)|slow down)\b")
FASTER = re.compile(r"\b((speak|talk) (a bit |more )?(faster|quicker|more quickly)|speed up)\b")
YES = re.compile(r"^(yes|yeah|yep|sure|ok(ay)?|do it|go ahead|confirm|delete it|forget it)\b")
NO = re.compile(r"^(no|nope|nah|don'?t|cancel|keep it|never ?mind|stop)\b")

PATTERNS = (WHO, DATA, HOW, KNOW_ME, FORGET_ALL, PRIVATE_OFF, PRIVATE_ON, REPEAT, SLOWER, FASTER)

WHO_REPLY = (f"I'm {config.ASSISTANT_NAME}, an AI voice assistant, not a person. I run on this Raspberry Pi with "
             "a small local language model, and I can make mistakes, so please check anything important.")
HOW_REPLY = ("I turn your speech into text on this Pi, pick the right skill for it, and answer with a local "
             "language model or a fixed rule. I can be wrong, especially about facts and numbers, so I say "
             "when I'm not sure and only quote numbers I can back up. Please double-check anything important.")


def clean(text):
    return re.sub(r"\s+", " ", text.lower().strip().strip(".!?,"))


class ResponsibleAI(Agent):
    name = "rai"

    def __init__(self, llm=None, db=None):
        super().__init__(llm, db)
        self.last_reply = ""        # set by the Conductor after each turn, for "repeat that"
        self._confirm = None        # ("one", memory id, fact) or ("all",) while asking yes / no
        self._asked_at = 0.0

    def awaiting_followup(self):
        if self._confirm is None:
            return False
        if time.monotonic() - self._asked_at > config.TODO_FOLLOWUP_TIMEOUT:
            self._confirm = None
            return False
        return True

    def claims(self, text):
        """True for questions and commands this agent answers (for the Conductor)."""
        t = clean(text)
        if any(p.search(t) for p in PATTERNS):
            return True
        m = FORGET_ONE.search(t)
        return bool(m) and self._find_fact(m.group(1)) is not None

    def handle(self, text):
        t = clean(text)
        if self._confirm is not None:
            return self._answer_confirmation(t)
        if REPEAT.search(t):
            return self.last_reply or "I haven't said anything yet."
        if SLOWER.search(t) or FASTER.search(t):
            new = rai.change_speed(rai.SPEED_STEP if FASTER.search(t) else -rai.SPEED_STEP)
            limit = new in (rai.MIN_SPEED, rai.MAX_SPEED)
            return ("That's as " + ("fast" if FASTER.search(t) else "slow") + " as I go." if limit
                    else "Okay, I'll speak " + ("faster." if FASTER.search(t) else "slower."))
        if PRIVATE_OFF.search(t):
            rai.set_private(False)
            return "Private mode is off. I'll remember our conversations again."
        if PRIVATE_ON.search(t):
            rai.set_private(True)
            return ("Private mode is on. I won't save anything we say until you say: private mode off. "
                    "Reminders and lists still work.")
        if FORGET_ALL.search(t):
            counts = self.db.data_inventory()
            self._ask(("all",))
            return (f"This deletes all {counts['facts']} saved facts and {counts['turns']} conversation turns. "
                    "Reminders, lists and game results stay. Say yes to delete, or no to keep them.")
        m = FORGET_ONE.search(t)
        if m:
            found = self._find_fact(m.group(1))
            if found is None:
                return "I don't have anything saved about that."
            self._ask(("one",) + found)
            return f"Should I forget this: {fact_to_speech(found[1])} Say yes or no."
        if KNOW_ME.search(t):
            return self._what_i_know()
        if DATA.search(t):
            return self._data_statement()
        if HOW.search(t):
            return HOW_REPLY
        return WHO_REPLY

    # ------------------------------------------------ data control
    def _ask(self, what):
        self._confirm, self._asked_at = what, time.monotonic()

    def _answer_confirmation(self, t):
        what, self._confirm = self._confirm, None
        if YES.search(t):
            if what[0] == "all":
                counts = self.db.delete_personal_data()
                semantic.index.forget()
                rai.audit("deleted", f"all personal data: {counts['facts']} facts, {counts['turns']} turns")
                return "Done. I've forgotten all saved facts and our conversation history."
            _, memory_id, fact = what
            self.db.delete_memory(memory_id)
            semantic.index.forget("memory", [memory_id])
            rai.audit("deleted", "one saved fact")
            return "Done, I've forgotten that."
        if NO.search(t):
            return "Okay, I kept it."
        self._confirm, self._asked_at = what, time.monotonic()
        return "Please say yes to delete, or no to keep it."

    def _find_fact(self, words):
        """(id, fact) best matching `words`, or None."""
        facts = self.db.memories()
        if not facts or not keywords(words):
            return None
        matches = self.db.search_memories(words, 1)
        if not matches:
            similar = semantic.index.search("memory", words, 1)
            ids = {f["id"]: f["fact"] for f in facts}
            return (similar[0][0], ids[similar[0][0]]) if similar and similar[0][0] in ids else None
        return next(((f["id"], f["fact"]) for f in facts if f["fact"] == matches[0]), None)

    # ------------------------------------------------ transparency
    def _what_i_know(self):
        facts = self.db.memories()
        if not facts:
            return "I haven't saved anything about you. Say: remember, and then a fact, to tell me something."
        spoken = " ".join(fact_to_speech(f["fact"]) for f in facts[-8:])
        more = f" And {len(facts) - 8} more." if len(facts) > 8 else ""
        return f"Here's what I've saved about you: {spoken}{more} Say: forget, and the fact, to delete one."

    def _data_statement(self):
        c = self.db.data_inventory()
        private = " Private mode is on right now, so nothing new is being saved." if rai.private else ""
        return (f"Everything stays on this Pi. I've saved {c['facts']} facts, {c['turns']} conversation turns, "
                f"{c['reminders']} reminders and {c['lists']} lists. While asleep I only listen for my name, and "
                "with the switch off the microphone is off. Only web searches go online, and only the question "
                f"is sent. History older than {config.HISTORY_RETENTION_DAYS} days is deleted automatically. "
                f"Say: forget everything, to delete it all.{private}")
