"""To-do lists by voice."""

import pytest

import config
from agents.base import Agent
from agents.conductor import Conductor
from agents.todo import Todo, creation_name, items_from, list_name
from conftest import FakeLLM


@pytest.fixture
def todo(db):
    return Todo(db=db)


def items(db, name):
    lst = next(l for l in db.todo_lists() if l["name"] == name)
    return [(i["text"], i["done"]) for i in db.todo_items(lst["id"])]


# ---------------------------------------------------------------- understanding
@pytest.mark.parametrize("text,name", [
    ("create a todo list", ""),
    ("make a new to-do list", ""),
    ("create a shopping list", "shopping"),
    ("create a grocery to do list", "grocery"),
    ("create a new list called Weekend Chores", "weekend chores"),
    ("start a list for the party", "party"),
])
def test_creation_name(text, name):
    assert creation_name(text.lower()) == name


def test_items_and_names():
    assert items_from("Milk, eggs and bread.") == ["milk", "eggs", "bread"]
    assert items_from("Also add paneer to it") == ["paneer"]
    assert list_name("The Groceries list.") == "groceries"


# ---------------------------------------------------------------- a whole conversation
def test_create_name_add_and_finish(todo, db):
    assert todo.handle("Create a todo list") == "Sure. What should I call the new list?"
    assert todo.awaiting_followup()
    assert todo.handle("Groceries.") == (
        "Created your groceries list. What should I add? Say done when you're finished.")
    assert todo.handle("Milk.") == "Added milk. Anything else?"
    assert todo.handle("Eggs and bread") == "Added eggs and bread. Anything else?"
    assert todo.handle("milk") == "Milk is already on it. Anything else?"
    assert todo.handle("That's all.") == "Okay. Your groceries list has 3 items."
    assert not todo.awaiting_followup()
    assert items(db, "groceries") == [("milk", False), ("eggs", False), ("bread", False)]


def test_name_given_up_front(todo):
    assert todo.handle("Create a shopping list").startswith("Created your shopping list.")


def test_same_name_twice(todo):
    todo.handle("create a shopping list")
    todo.handle("done")
    assert todo.handle("create a shopping list").startswith("You already have a shopping list.")


def test_add_to_a_list_in_one_go(todo, db):
    assert todo.handle("Add milk to my groceries list") == "I made a new groceries list. Added milk."
    assert todo.handle("Add eggs to groceries") == "Added eggs."
    assert items(db, "groceries") == [("milk", False), ("eggs", False)]


def test_mark_done(todo, db):
    todo.handle("add milk and eggs to my groceries list")
    assert todo.handle("Mark milk as done") == "Marked milk as done on your groceries list. 1 item left."
    assert todo.handle("Eggs is done.") == "Marked eggs as done on your groceries list. That's everything on it!"
    assert todo.handle("check off milk") == "Milk is already done."
    assert todo.handle("mark butter as done") == "I couldn't find butter on your lists."


def test_remove_item(todo, db):
    todo.handle("add milk and bread to my groceries list")
    assert todo.handle("Remove bread from the groceries list") == "Removed bread from your groceries list."
    assert items(db, "groceries") == [("milk", False)]


def test_delete_list_asks_first(todo, db):
    todo.handle("add milk to my groceries list")
    assert todo.handle("Delete the groceries list") == "Delete your groceries list with 1 item? Say yes or no."
    assert todo.handle("hmm") == "Should I delete the groceries list? Say yes or no."
    assert todo.handle("No") == "Okay, I kept your groceries list."
    todo.handle("delete my grocery list")       # "grocery" finds "groceries"
    assert todo.handle("Yes") == "Deleted the groceries list."
    assert db.todo_lists() == []


def test_reading(todo):
    assert todo.handle("what are my lists?") == "You don't have any lists yet. Say: create a todo list."
    todo.handle("add milk, eggs and bread to my groceries list")
    todo.handle("mark eggs as done")
    assert todo.handle("What's on my groceries list?") == (
        "Your groceries list has 2 items to do: milk and bread. Done: eggs.")
    todo.handle("add call the bank to my work list")
    assert todo.handle("What are my lists?") == (
        "You have 2 lists: groceries with 2 items to do and work with 1 item to do.")


def test_commands_still_work_while_adding(todo, db):
    todo.handle("create a shopping list")
    todo.handle("rice")
    assert todo.handle("finish the report") == "Added finish the report. Anything else?"  # an item, not a tick
    assert todo.handle("mark rice as done").startswith("Marked rice as done")
    assert todo.handle("what's on the list?").startswith("Your shopping list has 1 item to do")


def test_dropped_after_a_while(todo):
    todo.handle("create a todo list")
    todo._last_seen -= config.TODO_FOLLOWUP_TIMEOUT + 1
    assert not todo.awaiting_followup()


def test_lists_survive_a_restart(db):
    Todo(db=db).handle("add milk to my groceries list")
    assert Todo(db=db).handle("what's on my groceries list?") == "Your groceries list has 1 item to do: milk."


# ---------------------------------------------------------------- routing
class Echo(Agent):
    def __init__(self, name):
        super().__init__(None, None)
        self.name = name

    def handle(self, text):
        return self.name


def conductor(db, todo):
    agents = {n: Echo(n) for n in ("schedule", "search", "remember", "answer", "music", "game")}
    agents["todo"] = todo
    return Conductor(FakeLLM(), db, agents, use_llm=False)


def test_a_whole_conversation_through_the_conductor(db, todo):
    c = conductor(db, todo)
    assert c.handle("Create a todo list") == "Sure. What should I call the new list?"
    assert c.handle("Groceries").startswith("Created your groceries list")
    assert c.handle("Yes") == "Added yes. Anything else?"  # while adding, words are items...
    c.handle("done")
    assert c.handle("what is the capital of France") == "answer"  # ...and afterwards, back to normal


@pytest.mark.parametrize("text,route", [
    ("create a todo list", "todo"),
    ("check off milk", "todo"),                      # not a web "check"
    ("what's on my shopping list", "todo"),
    ("remind me to buy milk at 6", "schedule"),      # reminders still win
    ("I have work tomorrow", "answer"),              # not about the "work" list
    ("my homework is done", "answer"),               # not an item on any list
    ("check the gold price", "search"),
])
def test_routing(db, todo, text, route):
    todo.handle("add milk to my shopping list")
    todo.handle("add call the bank to my work list")
    assert conductor(db, todo).classify(text)[0] == route
