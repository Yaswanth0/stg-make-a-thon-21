"""Meaning-based search over saved facts and past conversations.

Keyword search (SQLite FTS5) only finds shared words: "where did I leave my
car?" misses "The user parked on level 3". Here every fact and conversation
turn is turned into an embedding (a vector of numbers that captures its
meaning) by a small Ollama model, and a question is matched by cosine
similarity. Keyword matches still come first; meaning matches fill in.

    ollama pull nomic-embed-text      # ~270 MB, once

Saving is never slowed down: new facts and turns are embedded by a background
thread, and anything saved before (or while Ollama was down) is embedded on
the next start. If the model isn't available, search is keyword-only.
"""

import logging
import queue
import struct
import threading

import config

log = logging.getLogger("semantic")

# nomic-embed-text was trained with these prefixes; they improve matching.
QUERY_PREFIX = "search_query: "
DOCUMENT_PREFIX = "search_document: "


def pack(vector):
    return struct.pack(f"{len(vector)}f", *vector)


def unpack(data):
    return list(struct.unpack(f"{len(data) // 4}f", data))


class OllamaEmbedder:
    def __init__(self, model=config.EMBED_MODEL):
        import ollama

        self._ollama = ollama
        self.model = model

    def embed(self, text):
        try:
            response = self._ollama.embed(model=self.model, input=text, keep_alive=config.LLM_KEEP_ALIVE)
            return list(response["embeddings"][0])
        except AttributeError:  # older ollama package
            return list(self._ollama.embeddings(model=self.model, prompt=text)["embedding"])


class SemanticIndex:
    def __init__(self, db, embedder, background=True):
        self.db = db
        self.embedder = embedder
        self.model = embedder.model
        self._vectors = {"memory": {}, "turn": {}}   # kind -> {id: vector}
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._background = background
        for kind in self._vectors:
            for ref_id, data in db.embeddings(kind, self.model):
                self._vectors[kind][ref_id] = unpack(data)
        db.on_saved = self.add
        if background:
            threading.Thread(target=self._worker, name="embeddings", daemon=True).start()
        self.catch_up()

    # ------------------------------------------------ indexing
    def add(self, kind, ref_id, text):
        """Embeds a newly saved fact or turn (in the background)."""
        if self._background:
            self._queue.put((kind, ref_id, text))
        else:
            self._embed_and_store(kind, ref_id, text)

    def catch_up(self):
        """Queues everything saved before the index existed."""
        missing = [(kind, ref_id, text) for kind in self._vectors
                   for ref_id, text in self.db.unembedded(kind, self.model)]
        if missing:
            log.info("Embedding %d saved facts and turns in the background", len(missing))
        for item in missing:
            self.add(*item)

    def _worker(self):
        while True:
            kind, ref_id, text = self._queue.get()
            try:
                self._embed_and_store(kind, ref_id, text)
            except Exception as e:
                log.warning("Embedding failed (%s); will retry on the next start", e)
            finally:
                self._queue.task_done()

    def wait(self):
        """Blocks until everything queued is embedded (for tests and tools)."""
        self._queue.join()

    def _embed_and_store(self, kind, ref_id, text):
        vector = self.embedder.embed(DOCUMENT_PREFIX + text)
        self.db.save_embedding(kind, ref_id, self.model, pack(vector))
        with self._lock:
            self._vectors[kind][ref_id] = vector

    # ------------------------------------------------ searching
    def search(self, kind, question, limit, min_similarity=config.EMBED_MIN_SIMILARITY):
        """[(id, similarity)] of the facts or turns closest in meaning, best first."""
        with self._lock:
            items = list(self._vectors[kind].items())
        if not items:
            return []
        try:
            query = self.embedder.embed(QUERY_PREFIX + question)
        except Exception as e:
            log.warning("Embedding the question failed (%s); keyword search only", e)
            return []
        ids = [ref_id for ref_id, _ in items]
        scores = cosine_similarities(query, [vector for _, vector in items])
        ranked = sorted(zip(ids, scores), key=lambda pair: -pair[1])[:limit]
        # Logged so EMBED_MIN_SIMILARITY can be tuned: matches below it are dropped.
        log.info("Meaning search (%s): %s", kind, ", ".join(f"#{i} {s:.2f}" for i, s in ranked) or "nothing")
        return [(ref_id, score) for ref_id, score in ranked if score >= min_similarity]


def cosine_similarities(query, vectors):
    try:
        import numpy as np

        matrix = np.asarray(vectors, dtype=np.float32)
        q = np.asarray(query, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1) * (np.linalg.norm(q) or 1.0)
        return list((matrix @ q) / np.where(norms == 0, 1.0, norms))
    except Exception:  # no numpy: plain Python, fine for a few hundred items
        q_norm = sum(x * x for x in query) ** 0.5 or 1.0
        result = []
        for v in vectors:
            norm = (sum(x * x for x in v) ** 0.5) or 1.0
            result.append(sum(a * b for a, b in zip(query, v)) / (norm * q_norm))
        return result


class NoIndex:
    """Used when embeddings are off or unavailable: finds nothing."""

    def search(self, kind, question, limit, min_similarity=None):
        return []


def open_index(db, enabled=True):
    """The meaning-search index, or NoIndex if Ollama's embedding model isn't there."""
    if not enabled or not config.EMBED_MODEL:
        return NoIndex()
    try:
        embedder = OllamaEmbedder()
        embedder.embed("ready")  # fails now, not mid-conversation, if the model is missing
    except Exception as e:
        log.warning("No meaning search (%s). To enable it: ollama pull %s", e, config.EMBED_MODEL)
        return NoIndex()
    log.info("Meaning search on, with %s", config.EMBED_MODEL)
    return SemanticIndex(db, embedder)


# The one index the whole program uses; main.py replaces it at startup.
index = NoIndex()
