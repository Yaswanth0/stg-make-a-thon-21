"""Questions about earlier conversations: "what did we talk about yesterday?",
"what was the recipe you told me?", "remind me what you said about gold".

Every conversation is kept in the database; for these questions the
Responder looks up the matching past turns instead of only the last two.
"""

import re
from datetime import datetime, timedelta

from db import keywords

# Phrases that point back at an earlier conversation.
RECALL = re.compile(
    r"\byou (told|said|gave|suggested|recommended|mentioned|explained|answered)\b"
    r"|\b(did|have) you (tell|say|give|suggest|recommend|mention|explain)\b"
    r"|\bdid (i|we) (ask|say|tell|talk|discuss|speak)\b"
    r"|\b(we|i) (talked|discussed|spoke|chatted|asked you)\b"
    r"|\bwhat (did|have) (we|i) (talk|discuss|ask|say)\w*\b"
    r"|\bremind me what (you|i|we)\b"
    r"|\blast time (we|i|you)\b"
    r"|\b(our|the) (last|previous|earlier) (conversation|chat)\b"
)

# Words that say "the past" but not what about; left out of the search.
_RECALL_WORDS = set("""
you told said gave suggested recommended mentioned explained answered did ask asked
say tell talk talked discuss discussed speak spoke chatted remind conversation chat
last time previous earlier before yesterday today morning evening night week ago
again remember recall about
""".split())


def is_recall(text):
    return bool(RECALL.search(text.lower()))


def topic_words(text):
    """What the past turn was about: "the recipe you told me" -> ["recipe"]."""
    return [w for w in keywords(text) if w not in _RECALL_WORDS]


def period(text, now=None):
    """(since, until, label) for a time the question names, or None."""
    now = now or datetime.now()
    t = text.lower()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "day before yesterday" in t:
        return today - timedelta(days=2), today - timedelta(days=1), "the day before yesterday"
    if "last night" in t:
        return today - timedelta(hours=6), today + timedelta(hours=4), "last night"
    if "yesterday" in t:
        return today - timedelta(days=1), today, "yesterday"
    if re.search(r"\b(this morning|today|earlier today)\b", t):
        return today, now, "today"
    if re.search(r"\b(this|last|past) week\b", t):
        return now - timedelta(days=7), now, "this past week"
    return None
