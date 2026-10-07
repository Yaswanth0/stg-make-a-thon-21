"""Scheduler: creates, lists and cancels reminders. The ReminderWatcher thread
announces them when they are due."""

import logging
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta

import config
from agents.base import Agent, to_second_person
from db import keywords
from timeparse import find_when, parse_when, spoken_time

log = logging.getLogger("scheduler")

SYSTEM_PROMPT = """You manage reminders for a voice assistant. Read the user's sentence and reply with JSON only:
{"action": "create" or "list" or "cancel", "task": "...", "when": "..."}

create: task = what to do, short, starting with a verb, without "remind me to".
        when = the time words exactly as the user said them, or "" if they gave no time.
list:   the user asks which reminders they have. task and when are "".
cancel: task = which reminder to cancel, or "all". when is "".

Examples:
"remind me to call mom tomorrow at 6 pm" -> {"action": "create", "task": "call mom", "when": "tomorrow at 6 pm"}
"in 10 minutes remind me to check the oven" -> {"action": "create", "task": "check the oven", "when": "in 10 minutes"}
"set a reminder to take my medicine" -> {"action": "create", "task": "take my medicine", "when": ""}
"what are my reminders" -> {"action": "list", "task": "", "when": ""}
"cancel the reminder about the dentist" -> {"action": "cancel", "task": "dentist", "when": ""}
"delete all my reminders" -> {"action": "cancel", "task": "all", "when": ""}"""

ACTIONS = ("create", "list", "cancel")
NEVER_MIND = re.compile(r"\b(never ?mind|forget it|cancel|no|stop|nothing)\b")
# Words that say nothing about which reminder to cancel.
NOT_A_TARGET = {"reminder", "reminders", "cancel", "delete", "remove", "clear", "alarm", "set", "one"}
SPOKEN_LIMIT = 5


def guess_action(text):
    """Used when the LLM gives no usable action."""
    t = text.lower()
    if re.search(r"\b(cancel|delete|remove|clear)\b", t):
        return "cancel"
    if re.search(r"\b(what|which|list|any|do i have|tell me)\b.*\breminders?\b", t):
        return "list"
    return "create"


def task_from_text(text):
    """Fallback task extraction: "remind me to call mom at 6" -> "call mom"."""
    t = text.lower().strip().rstrip(".?!")
    m = re.search(r"\b(remind me|reminder)\s+(to|about|that|of)?\s*(.+)", t)
    task = m.group(3) if m else t
    task = re.split(r"\s+\b(at|in|on|by|tomorrow|tonight|today|this|next|every)\b", task)[0]
    return task.strip()


def clock_synced():
    """False only if timedatectl says the clock is not synchronised.
    Without network the Pi cannot set its clock (unless it has an RTC battery)."""
    try:
        out = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        return out != "no"
    except Exception:
        return True


class Scheduler(Agent):
    name = "scheduler"

    def __init__(self, llm, db):
        super().__init__(llm, db)
        self._pending_task = None
        self._pending_since = 0.0

    def awaiting_followup(self):
        return (self._pending_task is not None
                and time.monotonic() - self._pending_since < config.FOLLOWUP_TIMEOUT)

    def handle(self, text):
        now = datetime.now()
        if self.awaiting_followup():
            return self._finish_pending(text, now)
        self._pending_task = None

        data = self.llm.chat_json(SYSTEM_PROMPT, text) or {}
        action = str(data.get("action", "")).lower()
        if action not in ACTIONS:
            action = guess_action(text)
        task = str(data.get("task") or "").strip().rstrip(".")

        if action == "list":
            return self.list_reply(now)
        if action == "cancel":
            return self.cancel(task or text)

        task = task or task_from_text(text)
        if not task:
            return "What should I remind you about?"
        task = to_second_person(task)
        when = parse_when(str(data.get("when") or ""), now) or find_when(text, now)[0]
        if when is None:
            self._pending_task = task
            self._pending_since = time.monotonic()
            return f"When should I remind you to {task}?"
        return self.create(task, when, now)

    def _finish_pending(self, text, now):
        task, self._pending_task = self._pending_task, None
        when = parse_when(text, now) or find_when(text, now)[0]
        if when:
            return self.create(task, when, now)
        if NEVER_MIND.search(text.lower()):
            return "Okay, no reminder."
        return "Sorry, I didn't catch a time, so I didn't set that reminder."

    def create(self, task, when, now):
        self.db.add_reminder(task, when)
        log.info("Reminder set for %s: %s", when, task)
        reply = f"Okay, I'll remind you to {task} {spoken_time(when, now)}."
        if not clock_synced():
            reply += " My clock isn't synced right now, so the time could be off."
        return reply

    def list_reply(self, now):
        reminders = self.db.pending_reminders()
        if not reminders:
            return "You have no reminders."
        items = [f"{r['task']}, {spoken_time(r['due_at'], now)}" for r in reminders[:SPOKEN_LIMIT]]
        if len(reminders) == 1:
            return f"You have one reminder: {items[0]}."
        reply = f"You have {len(reminders)} reminders: " + "; ".join(items) + "."
        if len(reminders) > SPOKEN_LIMIT:
            reply += f" And {len(reminders) - SPOKEN_LIMIT} more."
        return reply

    def cancel(self, target):
        reminders = self.db.pending_reminders()
        if not reminders:
            return "You have no reminders to cancel."

        target = target.lower()
        if target.strip() in ("all", "everything") or re.search(r"\b(all|every|everything)\b", target):
            for r in reminders:
                self.db.set_reminder_status(r["id"], "cancelled")
            return f"Cancelled all {len(reminders)} reminders." if len(reminders) > 1 else "Cancelled your reminder."

        wanted = [w for w in keywords(target) if w not in NOT_A_TARGET]
        best, best_score = None, 0
        for r in reminders:
            task_words = keywords(r["task"])
            score = sum(1 for w in wanted if any(t.startswith(w) or w.startswith(t) for t in task_words))
            if score > best_score:
                best, best_score = r, score
        if best is None and not wanted and len(reminders) == 1:
            best = reminders[0]

        if best is None:
            names = ", ".join(r["task"] for r in reminders[:SPOKEN_LIMIT])
            return f"I couldn't tell which reminder you mean. You have: {names}."
        self.db.set_reminder_status(best["id"], "cancelled")
        return f"Cancelled the reminder to {best['task']}."


class ReminderWatcher(threading.Thread):
    """Background thread that speaks reminders when they are due, in any
    state, including SLEEP."""

    def __init__(self, db, say, interval=config.REMINDER_CHECK_INTERVAL):
        super().__init__(name="reminders", daemon=True)
        self.db = db
        self.say = say
        self.interval = interval
        self._stop_event = threading.Event()

    def run(self):
        while True:
            try:
                self.check()
            except Exception:
                log.exception("Reminder check failed")
            if self._stop_event.wait(self.interval):
                return

    def stop(self):
        """Stops the thread and waits for an announcement in progress to end."""
        self._stop_event.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=30)

    def check(self, now=None):
        now = now or datetime.now()
        for r in self.db.due_reminders(now):
            # Mark it first, so a slow or failed announcement is never repeated.
            self.db.set_reminder_status(r["id"], "done")
            if now - r["due_at"] <= timedelta(seconds=config.MISSED_REMINDER_GRACE):
                message = f"Reminder: {r['task']}."
            else:
                message = f"Missed reminder from {spoken_time(r['due_at'], now)}: {r['task']}."
            log.info("Announcing reminder %d", r["id"])
            self.db.add_turn("", message, "scheduler")
            self.say(message)
