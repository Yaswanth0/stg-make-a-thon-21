"""Turning spoken time phrases into datetimes, and datetimes back into speech.

dateparser does the parsing. The helpers here tidy up what Whisper writes
("6 p.m.", "six o'clock", "tonight") into forms dateparser understands, and
fix times it places in the past ("at 6" said at 3 PM means 6 PM, not 6 AM).
"""

import re
from datetime import timedelta

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty five": 45, "forty": 40, "fifty": 50,
    "a couple of": 2, "an": 1, "a": 1,
}
PARTS_OF_DAY = {"morning": "9:00 am", "afternoon": "3:00 pm", "evening": "6:00 pm", "night": "8:00 pm"}
_CLOCK = re.compile(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b|\b\d{1,2}:\d{2}\b")
_AMPM = re.compile(r"\b(am|pm)\b")


def normalize(phrase):
    p = phrase.lower().strip().rstrip(".?!,")
    p = p.replace("p.m.", "pm").replace("a.m.", "am").replace("p.m", "pm").replace("a.m", "am")
    p = re.sub(r"\b(\d{1,2})\.(\d{2})\b", r"\1:\2", p)            # 6.30 -> 6:30
    for word, number in NUMBER_WORDS.items():
        # "in ten minutes", "at six pm", but not the "a" in "a.m." or "take a pill"
        unit = r"(?=\s+(minutes?|hours?|days?|weeks?|o'?\s?clock|am|pm|thirty|fifteen|forty|$))"
        p = re.sub(r"\b%s\b%s" % (word, unit), str(number), p)
    p = re.sub(r"\b(\d{1,2}) (15|30|45)\b(?!\s*(minute|hour|day|week))", r"\1:\2", p)  # 6 30 -> 6:30
    p = re.sub(r"\bhalf past (\d{1,2})\b", r"\1:30", p)
    p = re.sub(r"\bquarter past (\d{1,2})\b", r"\1:15", p)
    p = re.sub(r"\b(\d{1,2})\s*o'?\s?clock\b", r"\1:00", p)
    p = re.sub(r"\bnoon\b", "12:00 pm", p)
    p = re.sub(r"\bmidnight\b", "11:59 pm", p)
    p = re.sub(r"(?<![:\d])(\d{1,2})\s*(am|pm)\b", r"\1:00 \2", p)    # 6pm -> 6:00 pm
    p = re.sub(r"\bnext (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", r"\1", p)
    p = re.sub(r"\b(at|by) (\d{1,2})\b(?!:)", r"\1 \2:00", p)        # at 6 -> at 6:00
    p = re.sub(r"^(\d{1,2})$", r"\1:00", p)                          # 6 -> 6:00

    # tonight / this evening / tomorrow morning
    p = re.sub(r"\btonight\b", "today night", p)
    p = re.sub(r"\bthis (morning|afternoon|evening)\b", r"today \1", p)
    for part, default in PARTS_OF_DAY.items():
        if re.search(r"\b%s\b" % part, p):
            if not _CLOCK.search(p):
                p = re.sub(r"\b(in the )?%s\b" % part, default, p)
            else:
                if not _AMPM.search(p):
                    p = re.sub(r"(\d{1,2}:\d{2})", r"\1 " + default[-2:], p, count=1)
                p = re.sub(r"\b(in the )?%s\b" % part, "", p)
    return re.sub(r"\s+", " ", p).strip()


def _settings(now):
    return {"PREFER_DATES_FROM": "future", "RELATIVE_BASE": now, "RETURN_AS_TIMEZONE_AWARE": False}


_DATE_WORDS = re.compile(r"\b(today|tomorrow|day after|next|monday|tuesday|wednesday|thursday|friday|"
                         r"saturday|sunday|\d{1,2}(st|nd|rd|th)|january|february|march|april|may|june|"
                         r"july|august|september|october|november|december)\b")
_RELATIVE = re.compile(r"\b(in|after) \d+ (minute|hour|day|week)")


def _fix_time(when, now, phrase):
    """"at 6" said at 3 PM means 6 PM today, not 6 AM tomorrow (where
    dateparser puts it), and a time already passed today moves to tomorrow."""
    bare_clock = _CLOCK.search(phrase) and not _AMPM.search(phrase) and not _DATE_WORDS.search(phrase)
    if bare_clock and not _RELATIVE.search(phrase) and when.hour < 12:
        evening = now.replace(hour=when.hour + 12, minute=when.minute, second=0, microsecond=0)
        if now < evening < when:
            return evening
    if when > now:
        return when
    if now - when > timedelta(days=1):
        return None  # a real date in the past
    return when + timedelta(days=1)


def _has_time_info(phrase):
    return bool(_CLOCK.search(phrase) or _RELATIVE.search(phrase)
                or (_DATE_WORDS.search(phrase) and not re.fullmatch(r"(at |by )?today", phrase)))


def parse_when(phrase, now):
    """Datetime for a time phrase such as "tomorrow at 6 pm", or None."""
    import dateparser

    if not phrase:
        return None
    p = normalize(phrase)
    if not _has_time_info(p):
        return None
    when = dateparser.parse(p, languages=["en"], settings=_settings(now))
    if when is None:
        return None
    if not _CLOCK.search(p) and not _RELATIVE.search(p):
        when = when.replace(hour=9, minute=0, second=0)  # "on Monday" -> Monday 9 AM
    return _fix_time(when.replace(microsecond=0), now, p)


def find_when(text, now):
    """Searches a whole sentence for a time. Returns (datetime, matched_text)
    or (None, None)."""
    from dateparser.search import search_dates

    p = normalize(text)
    if not _has_time_info(p):
        return None, None
    try:
        found = search_dates(p, languages=["en"], settings=_settings(now)) or []
    except Exception:
        return None, None
    for matched, _ in found:
        when = parse_when(matched, now)
        if when:
            return when, matched
    return None, None


def spoken_time(when, now, clock_only=False):
    """"6 PM", "today at 6:30 PM", "tomorrow at 9 AM", "on Friday at noon"."""
    hour = when.hour % 12 or 12
    suffix = "AM" if when.hour < 12 else "PM"
    clock = f"{hour} {suffix}" if when.minute == 0 else f"{hour}:{when.minute:02d} {suffix}"
    if clock_only:
        return clock

    delta = when - now
    if timedelta(0) <= delta < timedelta(hours=1):
        minutes = round(delta.total_seconds() / 60)
        return "in a minute" if minutes <= 1 else f"in {minutes} minutes"
    days = (when.date() - now.date()).days
    if days == 0:
        return f"today at {clock}"
    if days == 1:
        return f"tomorrow at {clock}"
    if days == -1:
        return f"yesterday at {clock}"
    if 1 < days < 7:
        return f"on {when:%A} at {clock}"
    return f"on {when:%B} {when.day} at {clock}"
