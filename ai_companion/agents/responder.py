"""Responder: answers questions using recent conversation, saved facts and
upcoming reminders as context.

Questions about the user ("what's my locker code?") are answered only from
saved facts: with no matching fact the reply says so without asking the LLM,
and an answer with a number the facts don't contain is replaced by the facts
themselves.
"""

import logging
import re
from datetime import datetime

import config
import prompts
from agents.base import Agent
from grounding import keep_supported
from timeparse import spoken_time

log = logging.getLogger("responder")

# Questions like "what do you know about me?" get all recent facts, not just
# the ones that share a keyword.
ABOUT_ME = re.compile(r"\b(about me|do you (know|remember)|have i told you|what have i)\b")

# Questions whose answer can only come from a saved fact.
PERSONAL = re.compile(
    r"\b(what|where|when|which|who)('s| is| are| was| were) my\b"
    r"|\bwhere (did|do|have) i (park|put|leave|left|keep|store|save)\b"
    r"|\bdo you (know|remember) (my|where i|what my|when my)\b"
)
# The Responder has no internet, so a reply saying it searched is invented
# ("I found that Virat Kohli has scored 43 centuries").
CLAIMS_SEARCH = re.compile(
    r"\bI(?: have|'ve)? (?:just )?(?:searched|looked (?:it |that )?up|found (?:that|out)|checked online)\b"
    r"|\b(?:according to|based on) (?:my|the) (?:search|results)\b|\bsearch results\b",
    re.IGNORECASE)
CANT_SEARCH_REPLY = "I can't look that up by myself. Say: search it, and I'll check online."

# ...unless they are about plans, which the reminders answer.
ABOUT_PLANS = re.compile(r"\b(remind\w*|schedule|plans?|appointments?|agenda|meetings?|today|tomorrow)\b")


def is_personal(text):
    t = text.lower()
    return bool(PERSONAL.search(t)) and not ABOUT_PLANS.search(t)


def fact_to_speech(fact):
    """"The user's locker code is 4521." -> "Your locker code is 4521."."""
    for old, new in (("The user's", "Your"), ("the user's", "your"), ("The user is", "You are"),
                     ("The user has", "You have"), ("The user was", "You were"), ("The user", "You"),
                     ("the user", "you")):
        fact = fact.replace(old, new)
    return fact


class Responder(Agent):
    name = "responder"

    def handle(self, text):
        personal = is_personal(text)
        facts = self.facts_for(text)
        if personal and not facts:
            return (f"I don't have that saved, so I don't know. You can tell me by saying: "
                    f"{config.ASSISTANT_NAME}, remember, and then the fact.")

        reply = self.llm.chat(self.system_prompt(text, facts), text, history=self.history(),
                              temperature=config.RESPONDER_TEMPERATURE)
        if not reply:
            return "Sorry, my language model is not responding."
        if CLAIMS_SEARCH.search(reply):
            # It can't search; anything "found" was made up.
            log.warning("Responder claimed to search: %s", reply)
            return CANT_SEARCH_REPLY
        if personal:
            reply, dropped = keep_supported(reply, text, *facts)
            if dropped and not reply:
                return "Here's what I have saved: " + " ".join(fact_to_speech(f) for f in facts)
        return reply

    def facts_for(self, text):
        facts = self.db.search_memories(text, config.MEMORY_MATCHES)
        if ABOUT_ME.search(text.lower()):
            facts = list(dict.fromkeys(facts + self.db.recent_memories(8)))
        return facts

    def history(self):
        messages = []
        for turn in self.db.recent_turns(config.HISTORY_TURNS, config.HISTORY_MAX_AGE):
            if turn["user_text"]:
                messages.append({"role": "user", "content": turn["user_text"]})
            messages.append({"role": "assistant", "content": turn["reply"]})
        return messages

    def system_prompt(self, text, facts=None):
        now = datetime.now()
        if facts is None:
            facts = self.facts_for(text)
        parts = [
            prompts.RESPONDER,
            f"It is now {now:%A}, {now:%B} {now.day}, {now:%Y}, {spoken_time(now, now, clock_only=True)}.",
        ]
        if facts:
            parts.append(
                "Facts the user asked you to remember. These are the ONLY things you know about the user; "
                "copy names and numbers exactly:\n" + "\n".join(f"- {f}" for f in facts)
            )
        else:
            parts.append("You have no saved facts about the user that match this question.")

        reminders = self.db.pending_reminders(limit=3)
        if reminders:
            parts.append(
                "The user's upcoming reminders:\n"
                + "\n".join(f"- {r['task']}, {spoken_time(r['due_at'], now)}" for r in reminders)
            )
        return "\n\n".join(parts)
