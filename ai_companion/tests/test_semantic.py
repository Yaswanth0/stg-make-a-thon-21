"""Meaning search: finds facts and past turns that share no keywords with the
question. A fake embedder stands in for Ollama: it maps words to a few
"meanings", which is what a real embedding model does far more finely."""

from datetime import datetime, timedelta

import pytest

import semantic
from agents.responder import Responder
from conftest import FakeLLM
from semantic import NoIndex, SemanticIndex, cosine_similarities, open_index, pack, unpack

MEANINGS = {
    "medical": {"doctor", "physician", "appointment", "apollo", "hospital", "dr", "rao", "clinic"},
    "food": {"recipe", "dish", "cook", "cooking", "chicken", "masala", "curry", "marinate", "simmer"},
    "locker": {"locker", "code", "4521"},
    "music": {"song", "music", "singer"},
}


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self):
        self.calls = []
        self.broken = False

    def embed(self, text):
        if self.broken:
            raise ConnectionError("Ollama is down")
        self.calls.append(text)
        words = {w.strip(".,?!:'\"").lower() for w in text.replace("'s", "").split()}
        vector = [float(len(words & vocab)) for vocab in MEANINGS.values()]
        return vector + [0.1]  # a little shared "everything else", like real embeddings


@pytest.fixture
def index(db, monkeypatch):
    idx = SemanticIndex(db, FakeEmbedder(), background=False)
    monkeypatch.setattr(semantic, "index", idx)
    return idx


def test_pack_roundtrip():
    assert unpack(pack([1.0, -0.5, 0.25])) == [1.0, -0.5, 0.25]


def test_cosine():
    a, b, c = [1.0, 0.0], [2.0, 0.0], [0.0, 1.0]
    sims = cosine_similarities(a, [b, c])
    assert sims[0] == pytest.approx(1.0) and sims[1] == pytest.approx(0.0)


def test_new_facts_are_embedded_as_they_are_saved(db, index):
    fact_id = db.add_memory("The user's doctor appointment is with Dr Rao at Apollo.")
    assert index.embedder.calls[-1].startswith("search_document: ")
    assert db.unembedded("memory", index.model) == []
    assert index.search("memory", "when do I see my physician?", 3)[0][0] == fact_id


def test_facts_saved_before_are_embedded_on_start(db):
    db.add_memory("The user's locker code is 4521.")  # no index yet
    idx = SemanticIndex(db, FakeEmbedder(), background=False)
    assert db.unembedded("memory", idx.model) == []
    assert idx.search("memory", "what is my locker code", 3)


def test_background_indexing(db):
    idx = SemanticIndex(db, FakeEmbedder(), background=True)
    db.add_memory("The user's doctor is Dr Rao.")
    idx.wait()
    assert db.unembedded("memory", idx.model) == []


def test_unrelated_question_finds_nothing(db, index):
    db.add_memory("The user's doctor appointment is with Dr Rao at Apollo.")
    assert index.search("memory", "play a song by my favourite singer", 3) == []


# ---------------------------------------------------------------- in the Responder
def test_responder_finds_a_fact_with_no_shared_words(db, index):
    db.add_memory("The user's doctor appointment is with Dr Rao at Apollo.")
    db.add_memory("The user's locker code is 4521.")
    assert db.search_memories("when do I see my physician?", 3) == []  # keywords alone miss it
    llm = FakeLLM(text_reply="You're seeing Dr Rao at Apollo.")
    Responder(llm, db).handle("When do I see my physician?")
    system = llm.calls[0]["system"]
    assert "Dr Rao at Apollo" in system and "locker code" not in system


def test_recall_finds_a_past_turn_in_other_words(db, index):
    db.add_turn("give me the recipe of chicken tikka masala",
                "Marinate chicken in yogurt and spices, grill it, then simmer it in a tomato sauce.", "responder")
    db.add_turn("what is the capital of Kerala", "Thiruvananthapuram is the capital of Kerala.", "responder")
    for _ in range(3):  # push both out of the "recent" window
        db.add_turn("ok", "Okay.", "responder")
    llm = FakeLLM(text_reply="You asked how to make chicken tikka masala: marinate, grill, then simmer.")
    reply = Responder(llm, db).handle("What was that dish you told me how to cook?")
    assert reply.startswith("You asked how to make chicken tikka masala")
    system = llm.calls[0]["system"]
    assert "tikka masala" in system and "Kerala" not in system


def test_embedding_failure_falls_back_to_keywords(db, index):
    db.add_memory("The user's locker code is 4521.")
    index.embedder.broken = True
    assert index.search("memory", "locker", 3) == []
    llm = FakeLLM(text_reply="Your locker code is 4521.")
    assert Responder(llm, db).handle("what's my locker code?") == "Your locker code is 4521."


def test_no_index_when_disabled_or_model_missing(db, monkeypatch):
    assert isinstance(open_index(db, enabled=False), NoIndex)

    class Missing:
        def __init__(self):
            raise RuntimeError('model "nomic-embed-text" not found, try pulling it first')

    monkeypatch.setattr(semantic, "OllamaEmbedder", Missing)
    assert isinstance(open_index(db), NoIndex)
