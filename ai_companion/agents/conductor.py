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
import guardrails
import music
import prompts
import rai
from agents.game import START as GAME_START
from agents.game import is_results_question
from recall import is_recall

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
        # sports and public figures change too often for the model's own memory
        r"|\b(centuries|wickets|goals|medals|cricket|ipl|world cup|tournament|elections?)\b"
        r"|\b(prime minister|president|chief minister|ceo) of\b"
    )),
]

# "Search it", "search again", "searching internet": search the previous question.
SEARCH_THAT = re.compile(
    r"^(please |ok(ay)?,? |yes,? |then )?(search|look|check|google|find)"
    r"( it| that| this| again| for it| for that| up| online| internet| the (internet|web)| on the (internet|web))*[.!?]*$"
    r"|^searching( on)?( the)? (internet|web|online)[.!?]*$"
    # "... can you search again?", "... please look it up online."
    r"|\b(search|check|look) (it |that |for it |for that )?again\b"
    r"|\b(search|look|check) (it|that)( up)?( online| on the (internet|web))?[.!?]*$"
)
# "Yes, go ahead" after Rabbit offered to search.
YES = re.compile(r"^(yes|yeah|yep|sure|ok(ay)?|please( do)?|go ahead|do it|let's go)\b")

SYSTEM_PROMPT = prompts.CONDUCTOR


# Music (an optional agent, used when one is given to the Conductor).
MUSIC_REQUEST = re.compile(
    r"^(please |can you |could you |will you )?(play|put on)\b"
    r"|\b(play|put on) (a |some |any |me (a |some )?)?(songs?|music|tracks?)\b"
    r"|\b(stop|pause|resume|continue|unpause|turn off)( the| this| that)? (music|songs?|track)\b"
    r"|\b(next|another|different|skip( this| the)?|change( the| this)?) (songs?|tracks?)\b"
    r"|\bwhat('s| is) this song\b|\bwhich song\b"
)
# While a song plays, short commands are clearly about it: "stop", "next one".
MUSIC_CONTROL = re.compile(r"^(stop|pause|resume|continue|next|skip|play|another)\b")


def shortcut_label(text):
    """The label for an obvious sentence, or None."""
    lowered = text.lower().strip()
    for label, pattern in SHORTCUTS:
        if pattern.search(lowered):
            return label
    return None


def music_label(text, playing):
    """"music" for a music request, or a short command while a song plays."""
    lowered = text.lower().strip()
    if MUSIC_REQUEST.search(lowered):
        return "music"
    if playing and MUSIC_CONTROL.search(lowered) and len(lowered.split()) <= 4:
        return "music"
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
        self._last_question = None  # the last real question, for "search it"
        self._last_reply = ""

    def search_followup(self, text):
        """The previous question, if `text` asks to search for it ("search it",
        "search again", or "yes" after Rabbit offered to search); else None."""
        if not self._last_question:
            return None
        lowered = text.lower().strip()
        if SEARCH_THAT.search(lowered):
            return self._last_question
        offered = "search" in self._last_reply.lower()
        if offered and YES.search(lowered) and len(lowered.split()) <= 7:
            return self._last_question
        return None

    def classify(self, text):
        """Returns (label, how) where how is "shortcut", "llm", "recall" or "default"."""
        responsible = self.agents.get("rai")
        if responsible is not None and responsible.claims(text):
            # "Are you human?", "forget my locker code", "private mode", "repeat that"
            return "rai", "shortcut"
        if is_recall(text):
            # "What was the gold price you told me?" is about the past, not a new search.
            return DEFAULT_LABEL, "recall"
        if self.use_shortcuts:
            # "Remind me to play cricket" is a reminder, so reminders go first.
            label = shortcut_label(text)
            game = self.agents.get("game")
            if game is not None and is_results_question(text) and game.has_results():
                return "game", "shortcut"  # "who won the last game?" isn't a web search
            if label != "schedule" and game is not None and GAME_START.search(text.lower()):
                return "game", "shortcut"
            todo = self.agents.get("todo")
            if label != "schedule" and todo is not None and todo.claims(text):
                return "todo", "shortcut"  # "check off milk" isn't a web search
            if label != "schedule" and "music" in self.agents and music_label(text, music.player.is_active()):
                return "music", "shortcut"
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
        """Routes `text` to an agent, stores the turn and returns the reply.
        Guardrails run before (on what was said) and after (on the reply)."""
        text = guardrails.trim_input(text)
        stopped = guardrails.check_input(text, audit=rai.audit)
        if stopped is not None:
            # Emergencies, self-harm, dangerous asks, secret numbers: no agent runs.
            self._remember(text, stopped, "guardrail")
            return stopped

        request = text
        previous = self.search_followup(text)
        if self._last_agent is not None and self._last_agent.awaiting_followup():
            agent = self._last_agent
            log.info("Route: %s (follow-up)", agent.name)
        elif previous:
            agent, request = self.agents["search"], previous
            log.info("Route: %s (search the previous question: %s)", agent.name, previous)
        else:
            label, how = self.classify(text)
            agent = self.agents[label]
            log.info("Route: %s (%s)", agent.name, how)
            if label in ("search", "answer"):
                self._last_question = text

        try:
            reply = agent.handle(request)
        except Exception:
            log.exception("%s failed on %r", agent.name, request)
            reply = "Sorry, something went wrong while handling that."

        reply = guardrails.check_output(reply, audit=rai.audit)
        self._last_agent = agent
        self._remember(text, reply, agent.name)
        return reply

    def _remember(self, text, reply, agent_name):
        """Keeps the turn: for "repeat that", and in the history unless private mode is on."""
        self._last_reply = reply
        responsible = self.agents.get("rai")
        if responsible is not None:
            responsible.last_reply = reply
        if not rai.private:
            self.db.add_turn(guardrails.mask_secrets(text), reply, agent_name)
