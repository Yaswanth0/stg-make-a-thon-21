"""Tic-tac-toe by voice."""

import random

import pytest

import config
import display
from agents.base import Agent
from agents.conductor import Conductor
from agents.game import (RABBIT, USER, Game, describe, free_cells, parse_cell, rabbit_move,
                         winner)
from conftest import FakeLLM


class FirstPlayer(random.Random):
    """A random source whose coin flip is fixed: who goes first."""

    def __init__(self, user_first):
        super().__init__(1)
        self.user_first = user_first

    def random(self):
        return 0.0 if self.user_first else 0.99


def game(user_first=True):
    return Game(rng=FirstPlayer(user_first))


# ---------------------------------------------------------------- understanding moves
@pytest.mark.parametrize("text,cell", [
    ("5", 5), ("Five.", 5), ("cell 7", 7), ("I'll take 3", 3), ("number nine", 9),
    ("top left", 1), ("bottom right", 9), ("the centre", 5), ("middle", 5),
    ("for", 4), ("to", 2), ("ate", 8),
    ("hello there", None), ("10", None), ("what about going to the market", None),
])
def test_parse_cell(text, cell):
    assert parse_cell(text) == cell


# ---------------------------------------------------------------- rules and moves
def test_winner_and_free_cells():
    board = ["X", "X", "X", None, "O", "O", None, None, None]
    assert winner(board) == "X" and free_cells(board) == [3, 6, 7, 8]


def test_rabbit_wins_when_it_can_and_blocks_otherwise():
    can_win = ["O", "O", None, "X", "X", None, None, None, None]
    assert rabbit_move(can_win, "medium") == 2
    must_block = ["X", "X", None, None, "O", None, None, None, None]
    assert rabbit_move(must_block, "medium") == 2


def test_hard_never_loses():
    rng = random.Random(7)
    for _ in range(30):  # the user plays at random
        board = [None] * 9
        turn = rng.choice([USER, RABBIT])
        while not winner(board) and free_cells(board):
            i = rng.choice(free_cells(board)) if turn == USER else rabbit_move(board, "hard")
            board[i] = turn
            turn = RABBIT if turn == USER else USER
        assert winner(board) != USER


def test_describe():
    board = ["X", None, "O", None, "X", None, None, None, None]
    assert describe(board) == "You have 1 and 5. I have 3. Free: 2, 4, 6, 7, 8 and 9."


# ---------------------------------------------------------------- a conversation
def test_user_goes_first():
    g = game(user_first=True)
    reply = g.handle("let's play tic tac toe")
    assert "cells are numbered 1 to 9" in reply and reply.endswith("You go first. Pick a cell.")
    assert g.awaiting_followup()


def test_rabbit_goes_first():
    reply = game(user_first=False).handle("tic tac toe")
    assert "I'll go first. I take" in reply and reply.endswith("Your turn.")


def test_a_move_and_a_reply():
    g = game()
    g.handle("tic tac toe")
    reply = g.handle("5")
    assert reply.startswith("I take ") and reply.endswith("Your turn.")
    assert g._board[4] == USER and g._board.count(RABBIT) == 1


def test_taken_and_unclear_moves():
    g = game()
    g.handle("tic tac toe")
    g.handle("5")
    assert g.handle("five").startswith("Cell 5 is already yours.")
    taken = g._board.index(RABBIT) + 1
    assert g.handle(str(taken)).startswith(f"Cell {taken} is already mine.")
    assert g.handle("what's for dinner") == "Say a number from 1 to 9 for your move, or say quit."


def test_where_are_we():
    g = game()
    g.handle("tic tac toe")
    g.handle("5")
    assert g.handle("where are we?").startswith("You have 5. I have ")


def test_win_then_play_again():
    g = game()
    g.handle("tic tac toe")
    g._board = ["X", "X", None, "O", "O", None, None, None, None]  # user to move and win
    assert g.handle("3") == "You win! Well played. Do you want to play again?"
    assert g.awaiting_followup()
    assert g.handle("maybe") == "Do you want to play again? Say yes or no."
    assert g.handle("yes").startswith("New game.")
    assert g._board.count(None) >= 8


def test_rabbit_wins_then_no():
    g = game()
    g.handle("tic tac toe")
    g._board = ["O", "O", None, "X", "X", None, "X", None, None]
    assert g.handle("9") == "I take 3. I win! Do you want to play again?"
    assert g.handle("no thanks") == "Okay. Thanks for playing!"
    assert not g.awaiting_followup()


def test_draw():
    g = game()
    g.handle("tic tac toe")
    g._board = ["X", "O", "X", "X", "O", "O", "O", "X", None]
    assert g.handle("9") == "It's a draw. Do you want to play again?"


def test_restart_and_quit_mid_game():
    g = game()
    g.handle("tic tac toe")
    g.handle("1")
    assert g.handle("restart").startswith("Okay, starting over. New game.")
    assert g._board == [None] * 9
    assert g.handle("quit") == "Okay, game over. Thanks for playing!"
    assert not g.awaiting_followup()


def test_abandoned_game_lets_other_requests_through(monkeypatch):
    g = game()
    g.handle("tic tac toe")
    g._last_seen -= config.GAME_IDLE_TIMEOUT + 1
    assert not g.awaiting_followup()


# ---------------------------------------------------------------- the screen
def test_board_on_the_oled(monkeypatch):
    pytest.importorskip("PIL")
    screen = display.RabbitDisplay(device=None)
    monkeypatch.setattr(display, "screen", screen)
    screen.show(True)
    g = game()
    g.handle("tic tac toe")
    assert screen.current()[0] == "game"
    g.handle("quit")
    assert screen.current()[0] == "awake"


# ---------------------------------------------------------------- with the Conductor
class Echo(Agent):
    def __init__(self, name):
        super().__init__(None, None)
        self.name = name

    def handle(self, text):
        return self.name


@pytest.mark.parametrize("text", ["Let's play tic-tac-toe", "play tic tac toe", "can we play a game"])
def test_routing_starts_a_game(db, text):
    agents = {n: Echo(n) for n in ("schedule", "search", "remember", "answer", "music")}
    agents["game"] = game()
    c = Conductor(FakeLLM(), db, agents, use_llm=False)
    assert "tic-tac-toe" in c.handle(text)
    # Every move goes to the game while it's on, even "5" or "stop".
    assert c.handle("5").startswith("I take ")
    assert c.handle("stop") == "Okay, game over. Thanks for playing!"
    assert c.handle("what is the capital of France") == "answer"


def test_reminder_about_a_game_is_still_a_reminder(db):
    agents = {n: Echo(n) for n in ("schedule", "search", "remember", "answer", "music")}
    agents["game"] = game()
    assert Conductor(FakeLLM(), db, agents, use_llm=False).handle("remind me to play a game at 6") == "schedule"


# ---------------------------------------------------------------- "who won the last game?"
def play_and_win(g):
    g.handle("tic tac toe")
    g._board = ["X", "X", None, "O", "O", None, None, None, None]
    g.handle("3")


def test_log_replay_who_won_the_last_game(db):
    """From the Pi: after winning and quitting, this went to a web search."""
    agents = {n: Echo(n) for n in ("schedule", "search", "remember", "answer", "music")}
    agents["game"] = Game(db=db, rng=FirstPlayer(True))
    c = Conductor(FakeLLM(), db, agents, use_llm=False)
    c.handle("let's play tic tac toe")
    agents["game"]._board = ["X", "X", None, "O", "O", None, None, None, None]
    assert c.handle("3").startswith("You win!")
    c.handle("Yes, play again.")
    c.handle("You can quit now.")
    assert c.handle("Who won the last game?").startswith("You won the last game, today at ")


def test_results_survive_a_restart(db):
    play_and_win(Game(db=db, rng=FirstPlayer(True)))
    fresh = Game(db=db)  # Rabbit restarted
    assert fresh.results_reply().startswith("You won the last game")


def test_overall_score(db):
    g = Game(db=db, rng=FirstPlayer(True))
    play_and_win(g)
    g.handle("yes")
    g._board = ["O", "O", None, "X", "X", None, "X", None, None]
    g.handle("9")  # Rabbit wins
    g.handle("yes")
    g._board = ["X", "O", "X", "X", "O", "O", "O", "X", None]
    g.handle("9")  # draw
    g.handle("no")
    assert g.handle("what's the score so far?").endswith(
        "Overall, you've won 1 and I've won 1, with 1 draw.")


def test_score_mid_game_keeps_the_game_going(db):
    g = Game(db=db, rng=FirstPlayer(True))
    play_and_win(g)
    g.handle("yes")
    assert g.handle("who is winning?").endswith("Your turn.")
    assert g.awaiting_followup()


def test_no_games_yet(db):
    assert Game(db=db).results_reply() == "We haven't played any games yet. Say: let's play tic-tac-toe."


@pytest.mark.parametrize("text,ours", [
    ("Who won the last game?", True),
    ("how many games did I win?", True),
    ("what's the tic tac toe score", True),
    ("who won the last IPL game?", False),
    ("who won the cricket match yesterday", False),
    ("who won the election", False),
])
def test_results_questions(text, ours):
    from agents.game import is_results_question

    assert is_results_question(text) is ours


def test_who_won_goes_to_search_if_we_never_played(db):
    agents = {n: Echo(n) for n in ("schedule", "search", "remember", "answer", "music")}
    agents["game"] = Game(db=db)
    assert Conductor(FakeLLM(), db, agents, use_llm=False).handle("who won the last game?") == "search"
