"""Researcher: looks things up on DuckDuckGo and summarises the top results.
The only agent that needs internet."""

import logging
import re
import socket

import config
from agents.base import Agent

log = logging.getLogger("researcher")

SYSTEM_PROMPT = """Answer the user's question from the web search results below, in at most two short sentences.
Use only facts found in the results. If they don't answer the question, say so.
Your reply is spoken aloud: no lists, no links, no markdown."""

LEADING = re.compile(
    r"^(please |can you |could you )?(search( the (web|internet))?( for)?|google|look up|find out|browse( for)?)\s*",
    re.IGNORECASE,
)
OFFLINE_REPLY = "I can't search the web right now because I'm not connected to the internet."


def search_query(text):
    """"Search for the Pi 5 price" -> "the Pi 5 price"."""
    query = LEADING.sub("", text.strip()).rstrip(".!?")
    query = re.sub(r"\s+(on(line| the (internet|web)))$", "", query, flags=re.IGNORECASE)
    return query.strip() or text.strip()


def is_online(timeout=2):
    """Quick check: can we open a connection to a public DNS server?"""
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            socket.create_connection((host, 53), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


def web_search(query, max_results):
    """Returns a list of {"title", "body", "href"} dicts."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS  # the package's older name
    return list(DDGS(timeout=config.SEARCH_TIMEOUT).text(query, max_results=max_results) or [])


class Researcher(Agent):
    name = "researcher"

    def __init__(self, llm, db, search=web_search, online=is_online):
        super().__init__(llm, db)
        self._search = search
        self._online = online

    def handle(self, text):
        query = search_query(text)
        if not self._online():
            return OFFLINE_REPLY
        try:
            results = self._search(query, config.SEARCH_RESULTS)
        except ImportError:
            log.error("Web search needs the 'ddgs' package: pip install ddgs")
            return "Web search isn't installed on me yet."
        except Exception as e:
            log.error("Search failed: %s", e)
            return "The web search failed, so I can't look that up right now."
        if not results:
            return f"I couldn't find anything about {query}."

        snippets = "\n".join(f"- {r.get('title', '')}: {r.get('body', '')}" for r in results)
        reply = self.llm.chat(SYSTEM_PROMPT, f"Question: {query}\n\nSearch results:\n{snippets}")
        return reply or "I found some results, but my language model is not responding."
