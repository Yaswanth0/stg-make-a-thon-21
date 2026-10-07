from datetime import datetime, timedelta

import pytest

from db import Database, keywords


def test_keywords_drop_filler_words():
    assert keywords("What's my locker code?") == ["locker", "code"]
    assert keywords("The user's WiFi password") == ["wifi", "password"]


def test_recent_turns_oldest_first_and_limited(db):
    for i in range(6):
        db.add_turn(f"q{i}", f"a{i}", "responder")
    turns = db.recent_turns(limit=3, max_age_seconds=60)
    assert [t["user_text"] for t in turns] == ["q3", "q4", "q5"]


@pytest.mark.parametrize("fts", [True, False])
def test_memory_search(db, fts):
    db.has_fts = db.has_fts and fts
    db.add_memory("The user's locker code is 4521.")
    db.add_memory("The user parked on level 3.")
    db.add_memory("The user's sister Priya has her birthday on May 4th.")

    assert db.search_memories("what's my locker code?", 3) == ["The user's locker code is 4521."]
    assert db.search_memories("where did I park", 3) == ["The user parked on level 3."]
    assert db.search_memories("when is Priya's birthday", 3)[0].startswith("The user's sister Priya")
    assert db.search_memories("what is the capital of France", 3) == []


def test_duplicate_memory_is_stored_once(db):
    db.add_memory("The user likes tea.")
    db.add_memory("the user likes tea.")
    assert db.recent_memories(10) == ["The user likes tea."]


def test_reminder_lifecycle(db):
    now = datetime.now().replace(microsecond=0)
    soon = db.add_reminder("call mom", now - timedelta(seconds=1))
    later = db.add_reminder("water plants", now + timedelta(hours=1))

    assert [r["id"] for r in db.pending_reminders()] == [soon, later]
    assert [r["id"] for r in db.due_reminders(now)] == [soon]

    db.set_reminder_status(soon, "done")
    db.set_reminder_status(later, "cancelled")
    assert db.pending_reminders() == []
    assert db.due_reminders(now + timedelta(days=1)) == []


def test_file_database_survives_reopen(tmp_path):
    path = str(tmp_path / "companion.db")
    first = Database(path)
    first.add_memory("The user likes tea.")
    first.close()
    second = Database(path)
    assert second.search_memories("tea", 3) == ["The user likes tea."]
    second.close()
