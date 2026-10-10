"""Responsible-AI state shared across Rabbit (see RESPONSIBLE_AI.md).

    private mode   while on, nothing is saved: no history, no new facts
    speech speed   "speak slower / faster", remembered across restarts
    audit log      guardrail triggers, deletions and privacy changes, in the
                   rai_events table (python rai_report.py summarises it)
    retention      conversations older than HISTORY_RETENTION_DAYS are deleted
"""

import logging
from datetime import datetime, timedelta

import config
from guardrails import mask_secrets

log = logging.getLogger("rai")

MIN_SPEED, MAX_SPEED, SPEED_STEP = 0.7, 1.5, 0.15

private = False   # private mode; always off after a restart, so it is never forgotten "on"
speed = 1.0       # 1.0 = normal; read by tts.py for every sentence
_db = None


def attach(db):
    """Called at startup: audit to `db` and restore the saved speech speed."""
    global _db, speed
    _db = db
    try:
        speed = min(MAX_SPEED, max(MIN_SPEED, float(db.get_setting("speech_speed", "1.0"))))
    except ValueError:
        speed = 1.0


def audit(kind, detail=""):
    """Records a responsible-AI event. Secrets are masked before saving."""
    log.info("RAI event %s: %s", kind, detail)
    if _db is not None:
        try:
            _db.add_rai_event(kind, mask_secrets(str(detail)))
        except Exception as e:
            log.warning("Could not record RAI event: %s", e)


def set_private(on):
    global private
    private = bool(on)
    audit("private_mode", "on" if private else "off")


def change_speed(step):
    """Makes speech faster (step > 0) or slower; returns the new speed."""
    global speed
    speed = round(min(MAX_SPEED, max(MIN_SPEED, speed + step)), 2)
    if _db is not None:
        _db.set_setting("speech_speed", speed)
    audit("speech_speed", speed)
    return speed


def apply_retention(db, days=None):
    """Deletes conversation history older than `days` (HISTORY_RETENTION_DAYS)."""
    days = config.HISTORY_RETENTION_DAYS if days is None else days
    if not days:
        return 0
    removed = db.delete_conversations_before(datetime.now() - timedelta(days=days))
    if removed:
        audit("retention", f"deleted {removed} conversation turns older than {days} days")
    return removed
