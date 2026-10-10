"""Guardrails around every request, whichever agent handles it.

Before (check_input): what the user said
    emergencies      -> "call 112 now", at once, no LLM
    self-harm        -> a caring reply with a helpline
    dangerous asks   -> declined (weapons, explosives, poison, hacking)
    rule changes     -> declined ("ignore your instructions", "reveal your prompt")
    secret numbers   -> not stored (card numbers, OTP, CVV, bank PIN, Aadhaar)

After (check_output): what Rabbit is about to say
    leaked instructions, dangerous content -> replaced
    emoji / markdown -> removed (they're spoken badly)
    too long         -> cut at a sentence boundary
    empty            -> a short fallback

The Responder adds its own checks on top (grounding.py, no pretend searching).
Everything here is plain pattern matching: instant, and the same every time.
"""

import logging
import re

import config

log = logging.getLogger("guardrails")

# ---------------------------------------------------------------- before
EMERGENCY = re.compile(
    r"\b(having a heart attack|had a heart attack|chest pain|can'?t breathe|cannot breathe|not breathing|"
    r"stopped breathing|(having|had) a stroke|(is|went|fell) unconscious|is choking|"
    r"(he|she|someone|somebody|my \w+|dad|mom|mum|mother|father) (has |just )?collapsed|"
    r"bleeding (badly|heavily|a lot)|overdosed?|"
    r"(house|kitchen|room|building) is on fire|there'?s a fire|fire in the|"
    r"call (an |the )?(ambulance|police|fire brigade)|i'?m being attacked|someone is (attacking|following) me|"
    r"(break-?in|robbery|burglar) (at|in) my)\b")
SELF_HARM = re.compile(
    r"\b(kill(ing)? myself|end(ing)? my life|take my (own )?life|suicid(e|al)|want to die|"
    r"(hurt|harm|cut)(ting)? myself|self[- ]harm|no reason to live|better off dead)\b")
DANGEROUS = re.compile(
    r"\b(how (do i|to|can i|would i|should i) (make|build|create|cook|get|buy) (a |an |some )?"
    r"(bomb|explosives?|grenade|gun|weapon|poison|meth|cocaine|heroin|napalm)|"
    r"(make|build) (a |an )?(bomb|explosive)|poison (someone|a person|my \w+)|"
    r"hack (into|someone'?s?)|(steal|crack) (a |an |someone'?s? )?(password|account|identity|wifi)|"
    r"hurt (someone|a person|him|her|them) (badly|without))\b")
INJECTION = re.compile(
    r"\b(ignore (all |any |your |the )?(previous |prior |above |earlier )?(instructions|rules|prompts?)|"
    r"forget (your|all) (instructions|rules)|you are now (a|an|in)|developer mode|jailbreak|"
    r"(reveal|show|tell me|repeat|print) (me )?(your |the )?(system |hidden |secret )?(prompt|instructions)|"
    r"what('?s| is| are) your (system )?(prompt|instructions))\b")

# Numbers that should never be written to the database.
# Not "credit card" alone: "pay my credit card bill of 25000" is a fine reminder.
SECRET_WORDS = re.compile(r"\b(otp|one[- ]time (password|code)|cvv|cvc|upi pin|atm pin|bank pin|card pin|"
                          r"card number|net ?banking password|aadhaa?r)\b")
CARD_NUMBER = re.compile(r"\b(?:\d[ -]?){13,19}\b")
AADHAAR = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")
DIGITS = re.compile(r"\d{4,}|\d(?:[ -]\d){3,}")  # a code: 4+ digits, maybe spoken "4 5 2 1"

EMERGENCY_REPLY = ("This sounds like an emergency. Call 112 right now, or ask someone near you to call. "
                   "Stay on the line and follow what they say.")
SELF_HARM_REPLY = ("I'm really sorry you're feeling this way, and I'm glad you said it. Please talk to someone now: "
                   "call Tele-MANAS on 14416, free and any time, or 112 if you are in danger. "
                   "You don't have to go through this alone.")
DANGEROUS_REPLY = "I can't help with that, because it could hurt someone. I'm happy to help with something else."
INJECTION_REPLY = "I can't change or share how I'm set up, but I'm happy to help with something else."
SECRET_REPLY = ("For your safety I don't store or repeat card numbers, OTPs, CVVs, bank PINs or Aadhaar numbers. "
                "Please keep those private.")


def has_secret(text):
    t = text.lower()
    if CARD_NUMBER.search(t):
        return True
    return bool(SECRET_WORDS.search(t) and DIGITS.search(t)) or bool("aadha" in t and AADHAAR.search(t))


def mask_secrets(text):
    """Text safe to keep in the history: secret-looking numbers become ####."""
    if not has_secret(text):
        return text
    return re.sub(r"\d", "#", text)


def check_input(text, audit=None):
    """A reply to give INSTEAD of handling `text`, or None if it's fine.
    The order matters: an emergency beats everything. `audit(kind, detail)`,
    if given, records each trigger (rai.audit)."""
    if not config.GUARDRAILS_ENABLED:
        return None
    t = text.lower()
    for name, pattern, reply in (("emergency", EMERGENCY, EMERGENCY_REPLY),
                                 ("self-harm", SELF_HARM, SELF_HARM_REPLY),
                                 ("dangerous request", DANGEROUS, DANGEROUS_REPLY),
                                 ("rule change", INJECTION, INJECTION_REPLY)):
        if pattern.search(t):
            log.warning("Guardrail before reply (%s): %s", name, mask_secrets(text))
            if audit:
                audit("guardrail_in", name)
            return reply
    if has_secret(text):
        log.warning("Guardrail before reply (secret number): %s", mask_secrets(text))
        if audit:
            audit("guardrail_in", "secret number")
        return SECRET_REPLY
    return None


def trim_input(text):
    return text[:config.MAX_INPUT_CHARS]


# ---------------------------------------------------------------- after
# Lines from Rabbit's own instructions (prompts.py) that should never be spoken.
LEAK_MARKERS = (
    "you are rabbit, a personal voice assistant", "facts the user asked you to remember",
    "these are the only things you know about the user", "stay truthful:", "how to answer:",
    "reply with json only", "your reply is spoken aloud by a text-to-speech", "earlier conversations that match",
)
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
MARKDOWN = re.compile(r"(\*\*|__|`+|^#{1,6}\s+|^\s*[-*]\s+)", re.MULTILINE)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Sweeping claims about a group of people ("all women are ...", "Muslims are
# always ..."): replaced, whatever the rest of the reply says.
GROUPS = (r"(women|men|girls|boys|wives|husbands|hindus|muslims|christians|sikhs|jains|buddhists|jews|"
          r"atheists|dalits|brahmins|north indians|south indians|biharis|tamils|bengalis|punjabis|"
          r"africans|americans|chinese|pakistanis|indians|foreigners|migrants|immigrants|old people|"
          r"elderly people|disabled people|gay people|poor people|rich people)")
STEREOTYPE = re.compile(r"\b(all|most|every) " + GROUPS + r" (are|can'?t|cannot|never|always)\b"
                        r"|\b" + GROUPS + r" are (all |always |naturally |inherently )?"
                        r"(stupid|lazy|inferior|dangerous|criminals?|dirty|untrustworthy|bad at|worse|less|slow|"
                        r"weak|greedy|violent|useless|incapable|too emotional)")
FAIRNESS_REPLY = ("I don't make generalisations about groups of people; everyone is an individual. "
                  "I'm happy to help with something more specific.")
FALLBACK_REPLY = "Sorry, I don't have an answer for that."
LEAK_REPLY = "I can't share how I'm set up, but I'm happy to help with something else."


def check_output(reply, audit=None):
    """The reply made safe to speak and store. `audit(kind, detail)`, if
    given, records each replacement."""
    if not config.GUARDRAILS_ENABLED:
        return reply
    text = (reply or "").strip()
    if not text:
        return FALLBACK_REPLY
    lowered = text.lower()
    if any(marker in lowered for marker in LEAK_MARKERS):
        log.warning("Guardrail after reply (leaked instructions): %s", text[:200])
        if audit:
            audit("guardrail_out", "leaked instructions")
        return LEAK_REPLY
    if DANGEROUS.search(lowered):
        log.warning("Guardrail after reply (dangerous content): %s", text[:200])
        if audit:
            audit("guardrail_out", "dangerous content")
        return DANGEROUS_REPLY
    if STEREOTYPE.search(lowered):
        log.warning("Guardrail after reply (stereotype): %s", text[:200])
        if audit:
            audit("guardrail_out", "stereotype")
        return FAIRNESS_REPLY
    text = MARKDOWN.sub("", EMOJI.sub("", text))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > config.MAX_REPLY_CHARS:
        kept = ""
        for sentence in SENTENCE_END.split(text):
            if kept and len(kept) + len(sentence) + 1 > config.MAX_REPLY_CHARS:
                break
            kept = f"{kept} {sentence}".strip()
        text = kept[:config.MAX_REPLY_CHARS]
        log.info("Guardrail after reply: shortened to %d characters", len(text))
    return text or FALLBACK_REPLY
