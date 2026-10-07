from datetime import datetime, timedelta

import pytest

from agents import scheduler as scheduler_module
from agents.scheduler import ReminderWatcher, Scheduler, task_from_text
from conftest import FakeLLM
from timeparse import normalize, parse_when, spoken_time

NOW = datetime(2026, 10, 8, 15, 0, 0)  # a Thursday, 3 PM


@pytest.fixture(autouse=True)
def clock_is_synced(monkeypatch):
    monkeypatch.setattr(scheduler_module, "clock_synced", lambda: True)


@pytest.mark.parametrize("phrase,expected", [
    ("in 10 minutes", NOW + timedelta(minutes=10)),
    ("in ten minutes", NOW + timedelta(minutes=10)),
    ("in an hour", NOW + timedelta(hours=1)),
    ("tomorrow at 6 pm", datetime(2026, 10, 9, 18, 0)),
    ("tomorrow at 6 p.m.", datetime(2026, 10, 9, 18, 0)),
    ("tomorrow at 6", datetime(2026, 10, 9, 6, 0)),
    ("at 6", datetime(2026, 10, 8, 18, 0)),           # 6 AM has passed -> 6 PM
    ("at 6 o'clock", datetime(2026, 10, 8, 18, 0)),
    ("at 9 am", datetime(2026, 10, 9, 9, 0)),          # 9 AM has passed -> tomorrow
    ("at 6.30 pm", datetime(2026, 10, 8, 18, 30)),
    ("at 2", datetime(2026, 10, 9, 2, 0)),             # 2 PM passed -> next 2 AM
    ("tonight", datetime(2026, 10, 8, 20, 0)),
    ("tomorrow morning", datetime(2026, 10, 9, 9, 0)),
    ("tomorrow evening at 7", datetime(2026, 10, 9, 19, 0)),
    ("on friday at 10 am", datetime(2026, 10, 9, 10, 0)),
    ("at noon tomorrow", datetime(2026, 10, 9, 12, 0)),
    ("next monday at 9", datetime(2026, 10, 12, 9, 0)),
    ("on monday", datetime(2026, 10, 12, 9, 0)),
    ("in 1 week", NOW + timedelta(weeks=1)),
    ("six thirty p.m.", datetime(2026, 10, 8, 18, 30)),
])
def test_parse_when(phrase, expected):
    assert parse_when(phrase, NOW) == expected


@pytest.mark.parametrize("phrase", ["", "later", "soon", "today"])
def test_vague_times_are_rejected(phrase):
    assert parse_when(phrase, NOW) is None


def test_normalize():
    assert normalize("Six thirty P.M.") == "6:30 pm"


def test_spoken_time():
    assert spoken_time(NOW + timedelta(minutes=10), NOW) == "in 10 minutes"
    assert spoken_time(datetime(2026, 10, 8, 18, 30), NOW) == "today at 6:30 PM"
    assert spoken_time(datetime(2026, 10, 9, 9, 0), NOW) == "tomorrow at 9 AM"
    assert spoken_time(datetime(2026, 10, 12, 12, 0), NOW) == "on Monday at 12 PM"
    assert spoken_time(datetime(2026, 11, 20, 8, 5), NOW) == "on November 20 at 8:05 AM"


def test_task_from_text():
    assert task_from_text("Remind me to call mom tomorrow at 6") == "call mom"


def test_create_reminder(db):
    llm = FakeLLM([{"action": "create", "task": "call my mom", "when": "in 20 minutes"}])
    reply = Scheduler(llm, db).handle("remind me to call my mom in 20 minutes")
    assert reply == "Okay, I'll remind you to call your mom in 20 minutes."
    [reminder] = db.pending_reminders()
    assert reminder["task"] == "call your mom"
    assert timedelta(minutes=19) < reminder["due_at"] - datetime.now() <= timedelta(minutes=20)


def test_create_reminder_when_llm_fails(db):
    reply = Scheduler(FakeLLM([None]), db).handle("remind me to stretch in 5 minutes")
    assert reply == "Okay, I'll remind you to stretch in 5 minutes."


def test_asks_for_missing_time_then_uses_answer(db):
    scheduler = Scheduler(FakeLLM([{"action": "create", "task": "take medicine", "when": ""}]), db)
    assert scheduler.handle("set a reminder to take medicine") == "When should I remind you to take medicine?"
    assert scheduler.awaiting_followup()
    assert scheduler.handle("in 2 hours").startswith("Okay, I'll remind you to take medicine")
    assert not scheduler.awaiting_followup()
    assert len(db.pending_reminders()) == 1


def test_followup_never_mind(db):
    scheduler = Scheduler(FakeLLM([{"action": "create", "task": "x", "when": ""}]), db)
    scheduler.handle("remind me to x")
    assert scheduler.handle("never mind") == "Okay, no reminder."
    assert db.pending_reminders() == []


def test_list_reminders(db):
    db.add_reminder("call mom", datetime.now() + timedelta(days=1))
    db.add_reminder("water plants", datetime.now() + timedelta(days=2))
    reply = Scheduler(FakeLLM([{"action": "list"}]), db).handle("what are my reminders")
    assert reply.startswith("You have 2 reminders: call mom, tomorrow at")
    assert "water plants" in reply


def test_list_when_empty(db):
    assert Scheduler(FakeLLM([None]), db).handle("what are my reminders") == "You have no reminders."


def test_cancel_by_name(db):
    db.add_reminder("call mom", datetime.now() + timedelta(days=1))
    db.add_reminder("go to the dentist", datetime.now() + timedelta(days=2))
    reply = Scheduler(FakeLLM([{"action": "cancel", "task": "dentist"}]), db).handle("cancel the dentist reminder")
    assert reply == "Cancelled the reminder to go to the dentist."
    assert [r["task"] for r in db.pending_reminders()] == ["call mom"]


def test_cancel_all(db):
    for task in ("a", "b"):
        db.add_reminder(task, datetime.now() + timedelta(days=1))
    reply = Scheduler(FakeLLM([None]), db).handle("delete all my reminders")
    assert reply == "Cancelled all 2 reminders."
    assert db.pending_reminders() == []


def test_cancel_unknown(db):
    db.add_reminder("call mom", datetime.now() + timedelta(days=1))
    db.add_reminder("feed cat", datetime.now() + timedelta(days=1))
    reply = Scheduler(FakeLLM([{"action": "cancel", "task": "gym"}]), db).handle("cancel the gym reminder")
    assert reply.startswith("I couldn't tell which reminder")
    assert len(db.pending_reminders()) == 2


def test_watcher_announces_due_and_missed_once(db):
    db.add_reminder("call mom", NOW - timedelta(seconds=3))
    db.add_reminder("old thing", NOW - timedelta(hours=2))
    db.add_reminder("future", NOW + timedelta(hours=1))
    said = []
    watcher = ReminderWatcher(db, said.append)

    watcher.check(NOW)
    watcher.check(NOW)
    assert said == ["Missed reminder from today at 1 PM: old thing.", "Reminder: call mom."]
    assert [r["task"] for r in db.pending_reminders()] == ["future"]
    assert db.recent_turns(5, 10 ** 9)[-1]["reply"] == "Reminder: call mom."
