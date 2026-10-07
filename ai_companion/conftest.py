"""Shared test fixtures. Lives at the project root so pytest puts this
folder on sys.path and `import db`, `import agents` work in tests."""

import pytest

from db import Database


class FakeLLM:
    """Stands in for Ollama. Returns queued JSON replies, and a fixed text reply."""

    def __init__(self, json_replies=(), text_reply="OK."):
        self.json_replies = list(json_replies)
        self.text_reply = text_reply
        self.calls = []

    def chat(self, system, user, history=(), **options):
        self.calls.append({"system": system, "user": user, "history": list(history)})
        return self.text_reply

    def chat_json(self, system, user, max_tokens=150):
        self.calls.append({"system": system, "user": user, "json": True})
        return self.json_replies.pop(0) if self.json_replies else None


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def fake_llm():
    return FakeLLM()
