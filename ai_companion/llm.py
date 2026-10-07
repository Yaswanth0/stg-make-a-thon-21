"""Thin wrapper around Ollama. Every agent shares one LLM instance, so there
is only one model in RAM."""

import json
import logging
import time

import config

log = logging.getLogger("llm")


class LLM:
    def __init__(self, model=config.LLM_MODEL, keep_alive=config.LLM_KEEP_ALIVE):
        import ollama

        self._ollama = ollama
        self.model = model
        self.keep_alive = keep_alive

    def load(self, attempts=config.LLM_LOAD_ATTEMPTS):
        """Loads the LLM into RAM now and tells Ollama to keep it there, so the
        first question is not slow. Retries because at boot Ollama may still be
        starting. Returns True once the model is loaded."""
        for attempt in range(1, attempts + 1):
            try:
                # An empty prompt makes Ollama load the model without generating.
                self._ollama.generate(model=self.model, prompt="", keep_alive=self.keep_alive)
                return True
            except Exception as e:
                log.warning("LLM not ready (attempt %d/%d): %s", attempt, attempts, e)
                time.sleep(5)
        return False

    def chat(self, system, user, history=(), json_mode=False, temperature=None, max_tokens=None):
        """Returns the reply text, or None if Ollama failed.
        `history` is a list of {"role": ..., "content": ...} messages that go
        between the system prompt and the user message."""
        messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": user}]
        options = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        extra = {"format": "json"} if json_mode else {}
        try:
            started = time.monotonic()
            response = self._ollama.chat(
                model=self.model,
                messages=messages,
                options=options,
                keep_alive=self.keep_alive,
                **extra,
            )
            log.debug("LLM took %.1fs", time.monotonic() - started)
            return response["message"]["content"].strip()
        except Exception as e:
            log.error("LLM error: %s", e)
            return None

    def chat_json(self, system, user, max_tokens=150):
        """Asks for a JSON object. Returns a dict, or None if Ollama failed or
        the model wrote something that is not a JSON object."""
        text = self.chat(system, user, json_mode=True, temperature=0, max_tokens=max_tokens)
        return parse_json_object(text)


def parse_json_object(text):
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        # Small models sometimes wrap the JSON in prose; take the outermost braces.
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            log.warning("LLM returned no JSON: %r", text)
            return None
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            log.warning("LLM returned broken JSON: %r", text)
            return None
    return data if isinstance(data, dict) else None
