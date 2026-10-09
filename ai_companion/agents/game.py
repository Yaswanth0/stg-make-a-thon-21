"""Game: tic-tac-toe by voice. No LLM; moves are instant.

    1 | 2 | 3
    4 | 5 | 6        the user is X, Rabbit is O,
    7 | 8 | 9        who goes first is picked at random

While a game is on, every sentence comes here (a follow-up, like the
Scheduler's "when?"): a cell, "restart", "quit", or "where are we?". After a
game ends Rabbit asks whether to play again. A game left alone for
GAME_IDLE_TIMEOUT seconds is dropped.
"""

import random
import re
import time

import config
import display
from agents.base import Agent

LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
USER, RABBIT = "X", "O"

START = re.compile(r"\bti(c|ck)[\s-]*ta(c|ck)[\s-]*to(e|w)\b|\bnoughts and crosses\b"
                   r"|\b(play|start|have) (a |another )?game\b|\blet'?s play\b(?!.*\b(song|music)\b)")
RESTART = re.compile(r"\b(restart|start (again|over)|new game|reset|play again|another game|one more)\b")
QUIT = re.compile(r"\b(quit|exit|stop|end|cancel|enough|give up|i'?m done)\b")
YES = re.compile(r"^(yes|yeah|yep|sure|ok(ay)?|of course|let'?s|why not|go|alright)\b")
NO = re.compile(r"^(no|nope|nah|not now|no thanks|later)\b")
BOARD = re.compile(r"\b(board|where are we|what('s| is) (taken|free|left)|which (cells|numbers)|status)\b")

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
                "seventh": 7, "eighth": 8, "ninth": 9}
# What Whisper writes for a number said on its own.
SOUNDALIKES = {"won": 1, "to": 2, "too": 2, "tu": 2, "tree": 3, "for": 4, "fore": 4, "sex": 6, "ate": 8, "nein": 9}
POSITIONS = [
    (r"\btop[\s-]*left\b", 1), (r"\btop[\s-]*(middle|centre|center)\b", 2), (r"\btop[\s-]*right\b", 3),
    (r"\b(middle|centre|center)[\s-]*left\b", 4), (r"\b(middle|centre|center)[\s-]*right\b", 6),
    (r"\bbottom[\s-]*left\b", 7), (r"\bbottom[\s-]*(middle|centre|center)\b", 8), (r"\bbottom[\s-]*right\b", 9),
    (r"\b(centre|center|middle)\b", 5),
]


def parse_cell(text):
    """The cell (1-9) a sentence names, or None. "Five", "cell 5",
    "I'll take 7", "top left", and "for" (4) when said alone."""
    t = text.lower().strip().rstrip(".!?")
    digits = re.findall(r"\b([1-9])\b", t)
    if len(digits) == 1:
        return int(digits[0])
    words = re.findall(r"[a-z]+", t)
    numbers = [NUMBER_WORDS[w] for w in words if w in NUMBER_WORDS]
    if len(set(numbers)) == 1:
        return numbers[0]
    for pattern, cell in POSITIONS:
        if re.search(pattern, t):
            return cell
    if len(words) <= 2:
        alike = [SOUNDALIKES[w] for w in words if w in SOUNDALIKES]
        if len(alike) == 1:
            return alike[0]
    return None


# ---------------------------------------------------------------- the game itself
def winner(board):
    for a, b, c in LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None


def free_cells(board):
    return [i for i, mark in enumerate(board) if mark is None]


def rabbit_move(board, level=config.GAME_LEVEL, rng=random):
    """Index (0-8) of Rabbit's move. "easy": random; "medium": win, block,
    then centre / corners / sides with a little randomness; "hard": never loses."""
    free = free_cells(board)
    if level == "easy":
        return rng.choice(free)
    if level == "hard":
        return _best_move(board)
    for mark in (RABBIT, USER):  # win if possible, else block
        for i in free:
            board[i] = mark
            won = winner(board) == mark
            board[i] = None
            if won:
                return i
    if rng.random() < 0.2:  # sometimes just play somewhere, so it can be beaten
        return rng.choice(free)
    for group in ([4], [0, 2, 6, 8], [1, 3, 5, 7]):
        options = [i for i in group if i in free]
        if options:
            return rng.choice(options)
    return free[0]


_SCORES = {}  # (board, whose turn) -> best outcome for Rabbit; shared by every game


def _score(board, mark):
    """+1 Rabbit wins, 0 draw, -1 Rabbit loses, with both playing perfectly."""
    key = (tuple(board), mark)
    if key not in _SCORES:
        w = winner(board)
        if w:
            result = 1 if w == RABBIT else -1
        elif None not in board:
            result = 0
        else:
            results = []
            for i in free_cells(board):
                board[i] = mark
                results.append(_score(board, USER if mark == RABBIT else RABBIT))
                board[i] = None
            result = max(results) if mark == RABBIT else min(results)
        _SCORES[key] = result
    return _SCORES[key]


def _best_move(board):
    best, best_score = None, -2
    for i in free_cells(board):
        board[i] = RABBIT
        s = _score(board, USER)
        board[i] = None
        if s > best_score:
            best, best_score = i, s
    return best


def describe(board):
    """"You have 1 and 5. I have 3. Free: 2, 4, 6, 7, 8 and 9."."""
    def cells(mark):
        found = [str(i + 1) for i, m in enumerate(board) if m == mark]
        return " and ".join([", ".join(found[:-1]), found[-1]]) if len(found) > 1 else (found[0] if found else "")

    parts = []
    if USER in board:
        parts.append(f"You have {cells(USER)}.")
    if RABBIT in board:
        parts.append(f"I have {cells(RABBIT)}.")
    parts.append(f"Free: {cells(None)}." if None in board else "The board is full.")
    return " ".join(parts)


# ---------------------------------------------------------------- the agent
class Game(Agent):
    name = "game"

    def __init__(self, llm=None, db=None, rng=None):
        super().__init__(llm, db)
        self._rng = rng or random.Random()
        self._board = None
        self._state = "idle"     # "idle", "playing", or "ask_again" (after a game ends)
        self._last_seen = 0.0

    def awaiting_followup(self):
        if self._state == "idle":
            return False
        if time.monotonic() - self._last_seen > config.GAME_IDLE_TIMEOUT:
            self._end()  # walked away; let other requests through
            return False
        return True

    def handle(self, text):
        self._last_seen = time.monotonic()
        t = text.lower().strip()
        if self._state == "idle":
            return self._new_game(intro=True)
        if self._state == "ask_again":
            if YES.search(t) or RESTART.search(t):
                return self._new_game()
            if NO.search(t) or QUIT.search(t):
                return self._end("Okay. Thanks for playing!")
            return "Do you want to play again? Say yes or no."

        # playing
        if RESTART.search(t):
            return "Okay, starting over. " + self._new_game()
        if QUIT.search(t):
            return self._end("Okay, game over. Thanks for playing!")
        if BOARD.search(t):
            return describe(self._board) + " Your turn."
        cell = parse_cell(t)
        if cell is None:
            return "Say a number from 1 to 9 for your move, or say quit."
        if self._board[cell - 1] is not None:
            whose = "yours" if self._board[cell - 1] == USER else "mine"
            return f"Cell {cell} is already {whose}. {describe(self._board)}"
        self._board[cell - 1] = USER
        self._show()
        if winner(self._board) == USER:
            return self._finish("You win! Well played.")
        if not free_cells(self._board):
            return self._finish("It's a draw.")
        return self._rabbit_turn()

    # ------------------------------------------------ turns
    def _new_game(self, intro=False):
        self._board = [None] * 9
        self._state = "playing"
        self._show()
        start = ("Let's play tic-tac-toe! The cells are numbered 1 to 9, left to right, top to bottom. "
                 "You're X and I'm O. " if intro else "New game. ")
        if self._rng.random() < 0.5:
            return start + "You go first. Pick a cell."
        return start + "I'll go first. " + self._rabbit_turn()

    def _rabbit_turn(self):
        i = rabbit_move(self._board, rng=self._rng)
        self._board[i] = RABBIT
        self._show()
        move = f"I take {i + 1}."
        if winner(self._board) == RABBIT:
            return self._finish(f"{move} I win!")
        if not free_cells(self._board):
            return self._finish(f"{move} It's a draw.")
        return f"{move} Your turn."

    def _finish(self, result):
        self._state = "ask_again"
        return f"{result} Do you want to play again?"

    def _end(self, reply=""):
        self._state, self._board = "idle", None
        display.screen.board(None)
        return reply

    def _show(self):
        display.screen.board(self._board)
