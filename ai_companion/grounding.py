"""Checks that an LLM reply only states numbers its sources contain.

Small models fill gaps with plausible-looking figures: a sunrise time the
weather page never mentioned, a locker code that was never saved. Numbers
are the easiest made-up detail to catch mechanically, and the most harmful
one to say out loud, so every sentence with a number that isn't in the
sources is dropped before the reply is spoken.
"""

import logging
import re

log = logging.getLogger("grounding")

_NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def numbers(text):
    """{"14957", "6:45", "32.8"} from "Rs 14,957 at 6:45, 32.8 degrees"."""
    found = set()
    for token in _NUMBER.findall(text or ""):
        token = token.replace(",", "")
        if "." in token:
            token = token.rstrip("0").rstrip(".") or "0"  # "32.80" = "32.8", "5.0" = "5"
        found.add(token)
    return found


def unsupported_numbers(sentence, allowed):
    """Numbers in `sentence` that don't appear in the `allowed` set."""
    return numbers(sentence) - allowed


def keep_supported(reply, *sources):
    """Returns (reply without unsupported sentences, the dropped sentences).
    `sources` are the texts the reply was supposed to come from."""
    allowed = set()
    for source in sources:
        allowed |= numbers(source)
    kept, dropped = [], []
    for sentence in _SENTENCE_END.split(reply.strip()):
        if unsupported_numbers(sentence, allowed):
            dropped.append(sentence)
        elif sentence:
            kept.append(sentence)
    if dropped:
        log.warning("Dropped unsupported claim(s): %s", " | ".join(dropped))
    return " ".join(kept), dropped
