"""Conductor: decides which agent handles each sentence.

Obvious sentences ("remind me...", "remember...", "search...", "gold price")
are routed by keyword, without an LLM call. The rest go straight to the
Responder, or, with config.ROUTE_WITH_LLM = True, to the LLM, which picks one
label from a fixed list (one extra LLM call per sentence, but it also catches
requests phrased in unusual ways).
"""

import logging
import re

import config
import prompts

log = logging.getLogger("conductor")

LABELS = ("schedule", "search", "remember", "answer")
DEFAULT_LABEL = "answer"

# Checked in this order; the first match wins.
SHORTCUTS = [
    ("schedule", re.compile(
        r"\bremind me\b|\b(set|add|create|make) (a |an )?(reminder|alarm|timer)\b"
        r"|\b(my|any|the|all|upcoming) reminders?\b|\breminders? (for|do i)\b"
        r"|\b(cancel|delete|remove|clear) (the |my |all |that )*(reminders?|alarm|timer)\b"
        r"|\bwake me( up)?\b|\b(alarm|timer) for\b"
    )),
    ("remember", re.compile(
        r"^(please |can you |could you )?(remember|note|don't forget|do not forget|save|memorize)"
        r"(?! (what|when|where|who|how|if|whether)\b)\b"
    )),
    ("search", re.compile(
        r"^(please |can you |could you )?(search|google|look up|find out|browse)\b"
        # "check the gold price", but not "check my ..." or "check if ..."
        r"|^(please |can you |could you )?check\b(?! (my|if|whether|that|this)\b)"
        r"|\b(on the (internet|web)|online)\b|\b(news|headlines|weather|forecast|temperature)\b"
        r"|\b(prices?|cost of|rates? of|exchange rate|stock|share price|bitcoin|crypto|sensex|nifty)\b"
        r"|\b(scores?|who won|match result|standings)\b|\b(latest|most recent|breaking)\b"
    )),
]

SYSTEM_PROMPT = prompts.CONDUCTOR


def shortcut_label(text):
    """The label for an obvious sentence, or None."""
    lowered = text.lower().strip()
    for label, pattern in SHORTCUTS:
        if pattern.search(lowered):
            return label
    return None


class Conductor:
    def __init__(self, llm, db, agents, use_shortcuts=True, use_llm=None):
        """`agents` maps each label to the agent that handles it. `use_llm`
        defaults to config.ROUTE_WITH_LLM."""
        missing = set(LABELS) - set(agents)
        if missing:
            raise ValueError(f"No agent for {sorted(missing)}")
        self.llm = llm
        self.db = db
        self.agents = agents
        self.use_shortcuts = use_shortcuts
        self.use_llm = config.ROUTE_WITH_LLM if use_llm is None else use_llm
        self._last_agent = None

    def classify(self, text):
        """Returns (label, how) where how is "shortcut", "llm" or "default"."""
        if self.use_shortcuts:
            label = shortcut_label(text)
            if label:
                return label, "shortcut"
        if not self.use_llm:
            return DEFAULT_LABEL, "default"
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
