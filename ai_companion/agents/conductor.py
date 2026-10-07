"""Conductor: decides which agent handles each sentence.

Obvious sentences ("remind me...", "remember...", "search...") are routed by
keyword, which skips one LLM call. Everything else goes to the LLM, which
picks one label from a fixed list.
"""

import logging
import re

log = logging.getLogger("conductor")

LABELS = ("schedule", "search", "remember", "answer")
DEFAULT_LABEL = "answer"

# Checked in this order; the first match wins.
SHORTCUTS = [
    ("schedule", re.compile(
        r"\bremind me\b|\b(set|add|create|make) (a |an )?(reminder|alarm|timer)\b"
        r"|\b(my|any|the|all|upcoming) reminders?\b|\breminders? (for|do i)\b"
        r"|\b(cancel|delete|remove|clear) (the |my |all |that )*(reminders?|alarm)\b"
    )),
    ("remember", re.compile(
        r"^(please |can you |could you )?(remember|note|don't forget|do not forget|save|memorize)"
        r"(?! (what|when|where|who|how|if|whether)\b)\b"
    )),
    ("search", re.compile(
        r"^(please |can you |could you )?(search|google|look up|find out|browse)\b"
        r"|\b(on the (internet|web)|online)\b|\b(latest |today's )?(news|headlines|weather)\b"
    )),
]

SYSTEM_PROMPT = """You route a voice assistant's requests. Pick exactly one label:
- "schedule": create, list or cancel reminders, alarms and timers
- "search": needs current information from the internet (news, weather, prices, sports scores, recent events)
- "remember": the user tells you a fact about themselves to save for later
- "answer": anything else: questions, chat, asking about saved facts or general knowledge

Examples:
"wake me up at seven" -> schedule
"what's on my list for tomorrow" -> schedule
"who won the football match yesterday" -> search
"how much is bitcoin right now" -> search
"my car is parked on level 3" -> remember
"my sister's birthday is on May 4th" -> remember
"where did I park my car" -> answer
"what is the capital of France" -> answer
"tell me a joke" -> answer

Reply with JSON only: {"label": "<one label>"}"""


def shortcut_label(text):
    """The label for an obvious sentence, or None."""
    lowered = text.lower().strip()
    for label, pattern in SHORTCUTS:
        if pattern.search(lowered):
            return label
    return None


class Conductor:
    def __init__(self, llm, db, agents, use_shortcuts=True):
        """`agents` maps each label to the agent that handles it."""
        missing = set(LABELS) - set(agents)
        if missing:
            raise ValueError(f"No agent for {sorted(missing)}")
        self.llm = llm
        self.db = db
        self.agents = agents
        self.use_shortcuts = use_shortcuts
        self._last_agent = None

    def classify(self, text):
        """Returns (label, how) where how is "shortcut", "llm" or "default"."""
        if self.use_shortcuts:
            label = shortcut_label(text)
            if label:
                return label, "shortcut"
        data = self.llm.chat_json(SYSTEM_PROMPT, text, max_tokens=20)
        label = str((data or {}).get("label", "")).strip().lower()
        if label in LABELS:
            return label, "llm"
        log.warning("Conductor got %r, using %s", data, DEFAULT_LABEL)
        return DEFAULT_LABEL, "default"

    def handle(self, text):
        """Routes `text` to an agent, stores the turn and returns the reply."""
        if self._last_agent is not None and self._last_agent.awaiting_followup():
            agent = self._last_agent
            log.info("Route: %s (follow-up)", agent.name)
        else:
            label, how = self.classify(text)
            agent = self.agents[label]
            log.info("Route: %s (%s)", agent.name, how)

        try:
            reply = agent.handle(text)
        except Exception:
            log.exception("%s failed on %r", agent.name, text)
            reply = "Sorry, something went wrong while handling that."

        self._last_agent = agent
        self.db.add_turn(text, reply, agent.name)
        return reply
