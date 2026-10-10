"""Todo: named to-do lists by voice. No LLM; lists are kept in the database.

    "create a todo list"            -> asks for a name, then for items
    "create a shopping list"        -> named "shopping", then asks for items
    "add milk to my shopping list"  -> adds (making the list if needed)
    "mark milk as done"             -> ticks it off
    "remove bread from shopping"    -> deletes an item
    "delete the shopping list"      -> asks "yes or no" first
    "what's on my shopping list?" / "what are my lists?"

While adding, every sentence is an item ("milk and eggs" is two) until the
user says "done" / "that's all". A conversation left alone for
TODO_FOLLOWUP_TIMEOUT seconds is dropped.
"""

import difflib
import re
import time

import config
from agents.base import Agent

LIST_WORD = r"(?:to-?do|to do|check)?\s*list"
CREATE = re.compile(r"\b(create|make|start|new)\b.*\blist\b")
NAMED = re.compile(r"\b(?:called|named)\s+(.+)$")
FOR_NAME = re.compile(r"\blist\s+for\s+(.+)$")
CREATE_NAME = re.compile(r"\b(?:create|make|start)\s+(?:a |an |my |the )?(?:new )?(.*?)\s*" + LIST_WORD + r"\b")
ADD_TO = re.compile(r"^(?:please )?(?:add|put)\s+(.+?)\s+(?:to|on|in|onto)\s+(?:my |the |our )?(.+?)$")
REMOVE_FROM = re.compile(r"^(?:please )?(?:remove|delete|take)\s+(.+?)\s+(?:off|from)\s+(?:my |the |our )?(.+?)$")
DELETE_LIST = re.compile(r"\b(delete|remove|erase|clear|get rid of|throw away)\b.*\blist\b")
MARK = [
    re.compile(r"^(?:please )?(?:mark|check off|tick off|tick|cross off|cross out|complete|finish)\s+(.+?)"
               r"(?:\s+as\s+(?:done|complete|completed|finished))?(?:\s+(?:on|in|from)\s+.*)?$"),
    re.compile(r"^(?:i'?m )?done with\s+(.+)$"),
    re.compile(r"^(.+?)\s+is\s+(?:done|complete|completed|finished)$"),
]
READ_ALL = re.compile(r"\b(what|which)\b.*\b(my|the|our)\s+(to-?do |to do |check)?lists\b|\blist (all )?my lists\b")
READ = re.compile(r"\b(what'?s|what is|what are|read|show|tell me|anything)\b.*\blist\b")
FINISH = re.compile(r"^(done|i'?m done|i'?m finished|finished|that'?s (all|it|everything)|nothing( else| more)?|"
                    r"no( more| thanks)?|stop|end|that'?ll be all|no that'?s it)\b")
YES = re.compile(r"^(yes|yeah|yep|sure|ok(ay)?|do it|delete it|go ahead)\b")
NO = re.compile(r"^(no|nope|nah|don'?t|cancel|keep it|never ?mind)\b")
FILLER = re.compile(r"^(?:please |and |also |plus |then |add |put |oh |um |uh )+")
TRAILER = re.compile(r"\s+(?:to (?:it|the list|my list)|too|as well|please)$")


def clean(text):
    return re.sub(r"\s+", " ", text.strip().strip(".!?,").strip()).lower()


def list_name(raw):
    """"the Groceries list." -> "groceries"."""
    name = clean(raw)
    name = re.sub(r"^(?:my |the |a |an |our |new )+", "", name)
    name = re.sub(r"\s*" + LIST_WORD + r"$", "", name).strip()
    return "" if name in ("", "todo", "to do", "to-do", "new", "check") else name


def items_from(text):
    """"Milk, eggs and bread." -> ["milk", "eggs", "bread"]."""
    text = TRAILER.sub("", FILLER.sub("", clean(text)))
    parts = re.split(r",\s*|\s+and\s+|\s+then\s+", text)
    return [p.strip() for p in parts if p.strip()]


def spoken_list(words):
    words = list(words)
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def creation_name(text):
    """The name in "create a shopping list" / "...a list called groceries", or ""."""
    for pattern in (NAMED, FOR_NAME):
        m = pattern.search(text)
        if m:
            return list_name(m.group(1))
    m = CREATE_NAME.search(text)
    return list_name(m.group(1)) if m else ""


class Todo(Agent):
    name = "todo"

    def __init__(self, llm=None, db=None):
        super().__init__(llm, db)
        self._state = "idle"   # idle / await_name / adding / confirm_delete
        self._list_id = None   # the list being added to, or asked about
        self._current = None   # the list used last, for "mark milk as done"
        self._last_seen = 0.0

    # ------------------------------------------------ conversation state
    def awaiting_followup(self):
        if self._state == "idle":
            return False
        if time.monotonic() - self._last_seen > config.TODO_FOLLOWUP_TIMEOUT:
            self._state = "idle"
            return False
        return True

    def claims(self, text):
        """True if `text` is clearly about to-do lists (for the Conductor).
        Careful not to grab ordinary sentences: "I have work tomorrow" isn't
        about a "work" list, and "my homework is done" isn't a tick-off unless
        "homework" is actually on a list."""
        t = clean(text)
        if re.search(r"\b(to-?do|to do|checklist)\b", t):
            return True
        if re.search(r"\blists?\b", t) and (CREATE.search(t) or ADD_TO.search(t) or REMOVE_FROM.search(t)
                                            or DELETE_LIST.search(t) or READ.search(t) or READ_ALL.search(t)):
            return True
        if not self.db.todo_lists():
            return False
        for pattern in (ADD_TO, REMOVE_FROM):  # "add milk to groceries" (no "list")
            m = pattern.search(t)
            if m and self._find_list(m.group(2)):
                return True
        for pattern in MARK:
            m = pattern.search(t)
            if m and self._find_item(m.group(1)):
                return True
        return False

    # ------------------------------------------------ handling
    def handle(self, text):
        self._last_seen = time.monotonic()
        t = clean(text)
        if self._state == "await_name":
            return self._named(t)
        if self._state == "confirm_delete":
            return self._confirm_delete(t)
        if self._state == "adding":
            if FINISH.search(t):
                return self._finish_adding()
            # Only clear commands; anything else is an item ("finish the report").
            command = self._command(t, while_adding=True)
            return command if command is not None else self._add_items(self._list_id, items_from(t))
        reply = self._command(t)
        return reply if reply is not None else self._read_all()

    def _command(self, t, while_adding=False):
        """A list command, or None if `t` isn't one."""
        if CREATE.search(t) and not ADD_TO.search(t):
            return self._create(creation_name(t))
        m = REMOVE_FROM.search(t)
        if m and not re.fullmatch(r"(?:the |my )?(?:\w+ )?list", m.group(1)):
            return self._remove_item(m.group(1), m.group(2))
        if DELETE_LIST.search(t):
            return self._ask_delete(t)
        m = ADD_TO.search(t)
        if m and (re.search(r"\blist\b", m.group(2)) or self._find_list(m.group(2))):
            lst = self._find_list(m.group(2))
            if lst is None:
                name = list_name(m.group(2)) or "todo"
                list_id, _ = self.db.create_todo_list(name)
                self._current = list_id
                return f"I made a new {name} list. " + self._add_items(list_id, items_from(m.group(1)))
            self._current = lst["id"]
            return self._add_items(lst["id"], items_from(m.group(1)))
        for pattern in (MARK[:1] if while_adding else MARK):  # while adding: explicit "mark ..." only
            m = pattern.search(t)
            if m and (not while_adding or re.match(r"(?:please )?(?:mark|check off|tick|cross)", t)):
                return self._mark_done(m.group(1))
        if READ_ALL.search(t):
            return self._read_all()
        if READ.search(t):
            lst = self._find_list(t)
            return self._read(lst) if lst else self._read_all()
        return None

    # ------------------------------------------------ create and add
    def _create(self, name):
        if not name:
            self._state = "await_name"
            return "Sure. What should I call the new list?"
        list_id, created = self.db.create_todo_list(name)
        self._current = self._list_id = list_id
        self._state = "adding"
        if created:
            return f"Created your {name} list. What should I add? Say done when you're finished."
        return f"You already have a {name} list. What should I add to it? Say done when you're finished."

    def _named(self, t):
        if NO.search(t) or FINISH.search(t):
            self._state = "idle"
            return "Okay, no new list."
        name = list_name(t)
        if not name:
            return "What should the list be called?"
        return self._create(name)

    def _add_items(self, list_id, items):
        if not items:
            return "What should I add?"
        existing = {i["text"].lower() for i in self.db.todo_items(list_id)}
        added, already = [], []
        for item in items:
            if item.lower() in existing:
                already.append(item)
            else:
                self.db.add_todo_item(list_id, item)
                existing.add(item.lower())
                added.append(item)
        reply = f"Added {spoken_list(added)}." if added else ""
        if already:
            reply += f" {spoken_list(already).capitalize()} {'is' if len(already) == 1 else 'are'} already on it."
        if self._state == "adding":
            reply += " Anything else?"
        return reply.strip()

    def _finish_adding(self):
        self._state = "idle"
        lst = self._list(self._list_id)
        if lst is None:
            return "Okay."
        return f"Okay. Your {lst['name']} list has {self._count(lst['items'])}."

    # ------------------------------------------------ done, remove, delete
    def _mark_done(self, words):
        found = self._find_item(words)
        if found is None:
            return f"I couldn't find {clean(words)} on your lists."
        lst, item = found
        if item["done"]:
            return f"{item['text'].capitalize()} is already done."
        self.db.set_todo_done(item["id"])
        self._current = lst["id"]
        reply = f"Marked {item['text']} as done on your {lst['name']} list."
        left = [i for i in self.db.todo_items(lst["id"]) if not i["done"]]
        return reply + (" That's everything on it!" if not left else f" {self._count(len(left))} left.")

    def _remove_item(self, words, list_words):
        lst = self._find_list(list_words)
        found = self._find_item(words, only=lst)
        if found is None:
            return f"I couldn't find {clean(words)} on {'your ' + lst['name'] + ' list' if lst else 'your lists'}."
        lst, item = found
        self.db.delete_todo_item(item["id"])
        return f"Removed {item['text']} from your {lst['name']} list."

    def _ask_delete(self, t):
        lst = self._find_list(t) or (self._list(self._current) if len(self.db.todo_lists()) == 1 else None)
        if lst is None:
            lists = self.db.todo_lists()
            if not lists:
                return "You don't have any lists to delete."
            return f"Which list should I delete? You have {spoken_list(l['name'] for l in lists)}."
        self._list_id = lst["id"]
        self._state = "confirm_delete"
        return f"Delete your {lst['name']} list with {self._count(lst['items'])}? Say yes or no."

    def _confirm_delete(self, t):
        lst = self._list(self._list_id)
        self._state = "idle"
        if lst is None:
            return "That list is already gone."
        if YES.search(t):
            self.db.delete_todo_list(lst["id"])
            if self._current == lst["id"]:
                self._current = None
            return f"Deleted the {lst['name']} list."
        if NO.search(t):
            return f"Okay, I kept your {lst['name']} list."
        self._state = "confirm_delete"
        return f"Should I delete the {lst['name']} list? Say yes or no."

    # ------------------------------------------------ reading
    def _read(self, lst):
        self._current = lst["id"]
        items = self.db.todo_items(lst["id"])
        if not items:
            return f"Your {lst['name']} list is empty."
        todo = [i["text"] for i in items if not i["done"]]
        done = [i["text"] for i in items if i["done"]]
        if not todo:
            return f"Everything on your {lst['name']} list is done: {spoken_list(done)}."
        reply = f"Your {lst['name']} list has {self._count(len(todo))} to do: {spoken_list(todo)}."
        return reply + (f" Done: {spoken_list(done)}." if done else "")

    def _read_all(self):
        lists = self.db.todo_lists()
        if not lists:
            return "You don't have any lists yet. Say: create a todo list."
        if len(lists) == 1:
            return self._read(lists[0])
        parts = [f"{l['name']} with {self._count(l['items'] - l['done'])} to do" for l in lists]
        return f"You have {len(lists)} lists: {spoken_list(parts)}."

    # ------------------------------------------------ finding lists and items
    def _list(self, list_id):
        return next((l for l in self.db.todo_lists() if l["id"] == list_id), None)

    @staticmethod
    def _name_in(name, text):
        """Is the list `name` mentioned in `text`? "grocery" finds "groceries"."""
        stem = re.sub(r"(ies|es|s)$", "", name.lower())
        return bool(re.search(r"\b" + re.escape(stem), text))

    def _find_list(self, text):
        lists = self.db.todo_lists()
        t = clean(text)
        named = [l for l in lists if self._name_in(l["name"], t)]
        if named:
            return max(named, key=lambda l: len(l["name"]))
        wanted = list_name(t)
        if wanted:
            close = difflib.get_close_matches(wanted, [l["name"] for l in lists], n=1, cutoff=0.75)
            if close:
                return next(l for l in lists if l["name"] == close[0])
        return None

    def _find_item(self, words, only=None):
        """(list, item) best matching `words`; the current list is searched first."""
        wanted = re.sub(r"^(?:the |my |a |an )", "", clean(words))
        wanted = re.sub(r"\s+(?:on|in|from) .*$", "", wanted)
        lists = [only] if only else sorted(self.db.todo_lists(), key=lambda l: l["id"] != self._current)
        best, best_score = None, 0.0
        for lst in lists:
            for item in self.db.todo_items(lst["id"]):
                text = item["text"].lower()
                if text == wanted:
                    score = 2.0
                elif wanted in text or text in wanted:
                    score = 1.5
                else:
                    score = difflib.SequenceMatcher(None, wanted, text).ratio()
                if not item["done"]:
                    score += 0.01  # prefer the one still to do
                if score > best_score:
                    best, best_score = (lst, item), score
        return best if best_score >= 0.7 else None

    @staticmethod
    def _count(n):
        return f"{n} item" if n == 1 else f"{n} items"
