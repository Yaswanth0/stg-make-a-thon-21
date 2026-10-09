"""SQLite storage: conversation history, remembered facts and reminders.

One connection is shared by the main loop and the reminder thread, so every
query runs under a lock.
"""

import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta

log = logging.getLogger("db")

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"  # local time; sorts correctly as text

# Words that would match almost every stored fact, so they are left out of
# memory searches. "user" is here because facts read "The user's ...".
STOPWORDS = set("""
a about am an and any are as at be been but by can could did do does doing
for from had has have he her his how i if in into is it its just
know me my of on or our please remember say she so tell that the their them
then there these they this to told user users was we were what when
where which who whom why will with would you your yours
""".split())

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    user_text  TEXT NOT NULL,   -- empty for things the AI said on its own (reminders)
    agent      TEXT NOT NULL,
    reply      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    fact       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    due_at     TEXT NOT NULL,
    task       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending'  -- pending / done / cancelled
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders (status, due_at);
CREATE TABLE IF NOT EXISTS embeddings (
    kind    TEXT NOT NULL,      -- memory / turn
    ref_id  INTEGER NOT NULL,   -- memories.id or conversations.id
    model   TEXT NOT NULL,
    vector  BLOB NOT NULL,      -- float32s
    PRIMARY KEY (kind, ref_id, model)
);
CREATE TABLE IF NOT EXISTS games (
    id          INTEGER PRIMARY KEY,
    finished_at TEXT NOT NULL,
    winner      TEXT NOT NULL   -- user / rabbit / draw
);
"""


def now_text(moment=None):
    return (moment or datetime.now()).strftime(TIME_FORMAT)


def parse_time(text):
    return datetime.strptime(text, TIME_FORMAT)


def turn_text(user_text, reply):
    """One conversation turn as text, for embedding."""
    text = f"User: {user_text}\nRabbit: {reply}" if user_text else f"Rabbit: {reply}"
    return text[:1000]


def keywords(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in dict.fromkeys(words) if w not in STOPWORDS and len(w) > 1]


# Words people use for the same thing: "what's my age?" must find
# "The user is 24 years old.", which never says "age".
SYNONYM_GROUPS = [
    {"age", "old", "years", "born", "birthday"},
    {"birthday", "born", "birth"},
    {"name", "called", "named"},
    {"live", "lives", "living", "address", "stay", "home", "house", "city"},
    {"phone", "number", "mobile", "contact"},
    {"work", "works", "job", "office", "profession", "company", "employer"},
    {"study", "studies", "college", "school", "university", "course"},
    {"car", "bike", "vehicle", "parked", "parking"},
    {"password", "pin", "code", "passcode"},
    {"mail", "email"},
    {"wife", "husband", "spouse", "partner"},
    {"mom", "mother", "mum"},
    {"dad", "father"},
    {"favourite", "favorite", "like", "likes", "love", "loves"},
    {"doctor", "dr"},
]


def with_synonyms(words):
    """["age"] -> ["age", "old", "years", "born", "birthday"]."""
    expanded = list(words)
    for word in words:
        for group in SYNONYM_GROUPS:
            if word in group:
                expanded.extend(w for w in group if w not in expanded)
    return expanded


class Database:
    def __init__(self, path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self.has_fts = self._create_fts()
        # Called with (kind, id, text) after a fact or turn is saved; the
        # meaning-search index (semantic.py) sets it to embed them.
        self.on_saved = None

    def _saved(self, kind, ref_id, text):
        if self.on_saved is not None:
            try:
                self.on_saved(kind, ref_id, text)
            except Exception as e:
                log.warning("Indexing %s %s failed: %s", kind, ref_id, e)

    def _create_fts(self):
        """Full-text indexes over memories and conversations. Falls back to
        simple matching if this SQLite was built without FTS5."""
        try:
            self._conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(fact)")
            self._conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(user_text, reply)")
        except sqlite3.OperationalError:
            log.warning("SQLite has no FTS5; memory search uses simple matching")
            return False
        # Index conversations saved before this index existed (once).
        self._conn.execute(
            "INSERT INTO conversations_fts (rowid, user_text, reply) SELECT id, user_text, reply "
            "FROM conversations WHERE id > (SELECT IFNULL(MAX(rowid), 0) FROM conversations_fts)"
        )
        return True

    def _query(self, sql, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _write(self, sql, params=()):
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    def close(self):
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ conversations
    def add_turn(self, user_text, reply, agent):
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO conversations (created_at, user_text, agent, reply) VALUES (?, ?, ?, ?)",
                (now_text(), user_text, agent, reply),
            )
            if self.has_fts:
                self._conn.execute(
                    "INSERT INTO conversations_fts (rowid, user_text, reply) VALUES (?, ?, ?)",
                    (cur.lastrowid, user_text, reply),
                )
        self._saved("turn", cur.lastrowid, turn_text(user_text, reply))
        return cur.lastrowid

    def recent_turn_ids(self, limit, max_age_seconds):
        """Ids of the turns recent_turns() returns, so they aren't recalled twice."""
        since = now_text(datetime.now() - timedelta(seconds=max_age_seconds))
        rows = self._query("SELECT id FROM conversations WHERE created_at >= ? ORDER BY id DESC LIMIT ?",
                           (since, limit))
        return [r["id"] for r in rows]

    def search_conversations(self, words, limit, since=None, until=None, skip_ids=()):
        """Past turns mentioning any of `words` (synonyms included), best match
        first, optionally between `since` and `until`, leaving out `skip_ids`."""
        words = with_synonyms(words)
        if not words:
            return []
        skip = ",".join(str(int(i)) for i in skip_ids) or "0"
        where, params = [f"c.id NOT IN ({skip})"], []
        if since:
            where.append("c.created_at >= ?")
            params.append(now_text(since))
        if until:
            where.append("c.created_at < ?")
            params.append(now_text(until))
        if self.has_fts:
            match = " OR ".join('"%s"*' % w for w in words)
            rows = self._query(
                "SELECT c.created_at, c.user_text, c.agent, c.reply FROM conversations_fts f "
                "JOIN conversations c ON c.id = f.rowid WHERE conversations_fts MATCH ? AND "
                + " AND ".join(where) + " ORDER BY f.rank LIMIT ?",
                [match] + params + [limit],
            )
            return [dict(r) for r in rows]
        rows = self._query(
            "SELECT c.created_at, c.user_text, c.agent, c.reply FROM conversations c WHERE "
            + " AND ".join(where) + " ORDER BY c.id DESC",
            params,
        )
        scored = []
        for r in rows:
            text = f"{r['user_text']} {r['reply']}".lower()
            score = sum(1 for w in words if w in text)
            if score:
                scored.append((score, dict(r)))
        scored.sort(key=lambda s: -s[0])
        return [turn for _, turn in scored[:limit]]

    def turns_between(self, since, until, limit, skip_ids=()):
        """Turns between two datetimes, oldest first (for "what did we talk
        about yesterday?"), leaving out `skip_ids`."""
        skip = ",".join(str(int(i)) for i in skip_ids) or "0"
        rows = self._query(
            "SELECT created_at, user_text, agent, reply FROM conversations "
            f"WHERE created_at >= ? AND created_at < ? AND id NOT IN ({skip}) "
            "ORDER BY id DESC LIMIT ?",
            (now_text(since), now_text(until), limit),
        )
        return [dict(r) for r in reversed(rows)]

    def recent_turns(self, limit, max_age_seconds):
        """The last `limit` turns no older than `max_age_seconds`, oldest first."""
        since = now_text(datetime.now() - timedelta(seconds=max_age_seconds))
        rows = self._query(
            "SELECT user_text, agent, reply FROM conversations WHERE created_at >= ? "
            "ORDER BY id DESC LIMIT ?",
            (since, limit),
        )
        return [dict(r) for r in reversed(rows)]

    # ------------------------------------------------------------ memories
    def add_memory(self, fact):
        """Stores a fact, unless the exact same fact is already stored."""
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT id FROM memories WHERE lower(fact) = lower(?)", (fact,)
            ).fetchone()
            if existing:
                return existing["id"]
            cur = self._conn.execute(
                "INSERT INTO memories (created_at, fact) VALUES (?, ?)", (now_text(), fact)
            )
            if self.has_fts:
                self._conn.execute(
                    "INSERT INTO memories_fts (rowid, fact) VALUES (?, ?)", (cur.lastrowid, fact)
                )
        self._saved("memory", cur.lastrowid, fact)
        return cur.lastrowid

    def memories_by_ids(self, ids):
        """Facts for these ids, in the same order."""
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        found = {r["id"]: r["fact"] for r in self._query(f"SELECT id, fact FROM memories WHERE id IN ({marks})", ids)}
        return [found[i] for i in ids if i in found]

    def turns_by_ids(self, ids, since=None, until=None, skip_ids=()):
        """Turns for these ids, in the same order, optionally only between
        `since` and `until` and leaving out `skip_ids`."""
        ids = [i for i in ids if i not in set(skip_ids)]
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self._query(f"SELECT id, created_at, user_text, agent, reply FROM conversations WHERE id IN ({marks})",
                           ids)
        found = {}
        for r in rows:
            if since and r["created_at"] < now_text(since):
                continue
            if until and r["created_at"] >= now_text(until):
                continue
            found[r["id"]] = {k: r[k] for k in ("created_at", "user_text", "agent", "reply")}
        return [found[i] for i in ids if i in found]

    # ------------------------------------------------------------ embeddings
    def save_embedding(self, kind, ref_id, model, vector_bytes):
        self._write("INSERT OR REPLACE INTO embeddings (kind, ref_id, model, vector) VALUES (?, ?, ?, ?)",
                    (kind, ref_id, model, vector_bytes))

    def embeddings(self, kind, model):
        """[(ref_id, vector_bytes)] for every embedded fact or turn."""
        rows = self._query("SELECT ref_id, vector FROM embeddings WHERE kind = ? AND model = ? ORDER BY ref_id",
                           (kind, model))
        return [(r["ref_id"], r["vector"]) for r in rows]

    def unembedded(self, kind, model):
        """[(id, text)] of facts or turns not embedded with `model` yet."""
        if kind == "memory":
            rows = self._query("SELECT id, fact FROM memories WHERE id NOT IN "
                               "(SELECT ref_id FROM embeddings WHERE kind = 'memory' AND model = ?)", (model,))
            return [(r["id"], r["fact"]) for r in rows]
        rows = self._query("SELECT id, user_text, reply FROM conversations WHERE id NOT IN "
                           "(SELECT ref_id FROM embeddings WHERE kind = 'turn' AND model = ?)", (model,))
        return [(r["id"], turn_text(r["user_text"], r["reply"])) for r in rows]

    def search_memories(self, text, limit):
        """Facts sharing keywords (or their synonyms) with `text`, best match first."""
        words = with_synonyms(keywords(text))
        if not words:
            return []
        if self.has_fts:
            # "locker"* also matches "lockers"; OR means any one keyword is enough.
            match = " OR ".join('"%s"*' % w for w in words)
            rows = self._query(
                "SELECT fact FROM memories_fts WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            )
            return [r["fact"] for r in rows]
        rows = self._query("SELECT fact FROM memories ORDER BY id DESC")
        scored = []
        for r in rows:
            fact = r["fact"].lower()
            score = sum(1 for w in words if w in fact)
            if score:
                scored.append((score, r["fact"]))
        scored.sort(key=lambda s: -s[0])
        return [fact for _, fact in scored[:limit]]

    def recent_memories(self, limit):
        rows = self._query("SELECT fact FROM memories ORDER BY id DESC LIMIT ?", (limit,))
        return [r["fact"] for r in rows]

    # ------------------------------------------------------------ tic-tac-toe
    def add_game_result(self, winner):
        self._write("INSERT INTO games (finished_at, winner) VALUES (?, ?)", (now_text(), winner))

    def game_results(self, limit=1000):
        """[(finished_at, winner)], newest first."""
        rows = self._query("SELECT finished_at, winner FROM games ORDER BY id DESC LIMIT ?", (limit,))
        return [(parse_time(r["finished_at"]), r["winner"]) for r in rows]

    # ------------------------------------------------------------ reminders
    def add_reminder(self, task, due_at):
        cur = self._write(
            "INSERT INTO reminders (created_at, due_at, task) VALUES (?, ?, ?)",
            (now_text(), now_text(due_at), task),
        )
        return cur.lastrowid

    def pending_reminders(self, limit=50):
        rows = self._query(
            "SELECT id, due_at, task FROM reminders WHERE status = 'pending' ORDER BY due_at LIMIT ?",
            (limit,),
        )
        return [_reminder(r) for r in rows]

    def due_reminders(self, moment=None):
        rows = self._query(
            "SELECT id, due_at, task FROM reminders WHERE status = 'pending' AND due_at <= ? "
            "ORDER BY due_at",
            (now_text(moment),),
        )
        return [_reminder(r) for r in rows]

    def set_reminder_status(self, reminder_id, status):
        self._write("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))


def _reminder(row):
    return {"id": row["id"], "due_at": parse_time(row["due_at"]), "task": row["task"]}
