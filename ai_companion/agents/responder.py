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
import semantic
from agents.base import Agent
from db import parse_time
from grounding import keep_supported
from recall import is_recall, period, topic_words
from timeparse import spoken_time

log = logging.getLogger("responder")

# Questions like "what do you know about me?" get all recent facts, not just
# the ones that share a keyword.
ABOUT_ME = re.compile(r"\b(about me|do you (know|remember)|have i told you|what have i)\b")

# Questions whose answer can only come from a saved fact.
PERSONAL = re.compile(
    r"\b(what|where|when|which|who)('s| is| are| was| were) my\b"
    r"|\bwhere (did|do|have) i (park|put|leave|left|keep|store|save)\b"
    r"|\bdo you (know|remember) (my|where i|what my|when my|how old)\b"
    r"|\bhow old am i\b|\bwhat do i do for (a )?(living|work)\b"
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


# Replies not worth recalling: errors, "didn't catch that", "not saved".
_NOT_WORTH_RECALLING = re.compile(
    r"^(sorry|i didn't catch|i don't have that saved|i can't look that up|i couldn't find|"
    r"i don't remember|i'm not sure what you)", re.IGNORECASE)


def is_worth_recalling(turn):
    return not _NOT_WORTH_RECALLING.search(turn["reply"].strip())


def recall_line(turn, now):
    """'yesterday at 9:30 PM: the user said "..."; you replied "..."'."""
    when = spoken_time(parse_time(turn["created_at"]), now)
    reply = turn["reply"][:250]
    if not turn["user_text"]:
        return f'{when}: you announced "{reply}"'
    return f'{when}: the user said "{turn["user_text"]}"; you replied "{reply}"'


class Responder(Agent):
    name = "responder"

    def handle(self, text):
        if is_recall(text):
            return self.recall(text)
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

    def recall(self, text):
        """Answers a question about earlier conversations from the saved ones."""
        now = datetime.now()
        span = period(text, now)
        since, until = (span[0], span[1]) if span else (None, None)
        words = topic_words(text)
        # The last few turns are already in the conversation; don't repeat them.
        skip = self.db.recent_turn_ids(config.HISTORY_TURNS, config.HISTORY_MAX_AGE)
        turns = []
        if words:
            turns = self.db.search_conversations(words, config.RECALL_MATCHES, since, until, skip_ids=skip)
            # Plus turns about the same thing in other words, after the keyword matches.
            similar = [i for i, _ in semantic.index.search("turn", text, config.RECALL_MATCHES * 2)]
            seen = {(t["created_at"], t["user_text"]) for t in turns}
            for t in self.db.turns_by_ids(similar, since, until, skip_ids=skip):
                if len(turns) < config.RECALL_MATCHES and (t["created_at"], t["user_text"]) not in seen:
                    turns.append(t)
        if not turns and span:
            # "What did we talk about yesterday?": everything from then.
            turns = self.db.turns_between(since, until, config.RECALL_MATCHES * 2, skip_ids=skip)
        turns = [t for t in turns if is_worth_recalling(t)]  # best match first
        best = turns[0] if turns else None
        turns.sort(key=lambda t: t["created_at"])
        log.info("Recalled %d earlier turn(s)", len(turns))
        if not turns:
            when = f" from {span[2]}" if span else ""
            return f"I don't remember us talking about that{when}."

        lines = [recall_line(t, now) for t in turns]
        system = (self.system_prompt(text, facts=[]) + "\n\n"
                  "Earlier conversations that match the question, oldest first. Answer from these only; "
                  "if they don't answer it, say you don't remember talking about that:\n"
                  + "\n".join(f"- {line}" for line in lines))
        reply = self.llm.chat(system, text, temperature=config.RESPONDER_TEMPERATURE)
        if not reply:
            return "Sorry, my language model is not responding."
        if CLAIMS_SEARCH.search(reply):
            return CANT_SEARCH_REPLY
        # Numbers must be what was actually said back then.
        reply, dropped = keep_supported(reply, text, *lines)
        if dropped and not reply:
            asked = f"you asked: {best['user_text']}. " if best["user_text"] else ""
            return f"{spoken_time(parse_time(best['created_at']), now).capitalize()}, {asked}I said: {best['reply'][:300]}"
        return reply

    def facts_for(self, text):
        facts = self.db.search_memories(text, config.MEMORY_MATCHES)
        # Facts with the same meaning in other words ("where's my car?" ->
        # "The user parked on level 3"); keyword matches stay first.
        similar = [ref_id for ref_id, _ in semantic.index.search("memory", text, config.EMBED_MATCHES)]
        facts = list(dict.fromkeys(facts + self.db.memories_by_ids(similar)))
        if ABOUT_ME.search(text.lower()):
            facts = list(dict.fromkeys(facts + self.db.recent_memories(8)))
        if not facts and is_personal(text):
            # No keyword matched, but the answer may still be there in other
            # words. Show the model the saved facts; numbers are checked after.
            facts = self.db.recent_memories(config.MEMORY_FALLBACK)
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
