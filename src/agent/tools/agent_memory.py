"""agent.tools.agent_memory — persist and recall the agent's own rulings.

This tool lets the interpretation agent PERSIST and RECALL its OWN prior
card-interpretation decisions, so its rulings stay consistent across cards and
games. It is distinct from card_rag: card_rag retrieves the interpreted-card
*corpus* (exemplar cards to imitate); agent_memory stores the *agent's* concrete
decisions plus the reasoning behind them.

Persistence decision — WHY sqlite (not Qdrant)
----------------------------------------------
The backing store is stdlib ``sqlite3``, chosen deliberately over reusing the
Qdrant instance that already backs card_rag:

* No embeddings key required. Qdrant recall needs an OpenAI/embeddings key to
  vectorize queries, and that key is frequently absent in dev / CI. sqlite needs
  none — keyword (LIKE) + recency recall is sufficient for the MVP.
* Survives process restarts. Rooms are in-memory / single-worker, so anything
  kept only in RAM is lost on restart; a sqlite file persists the agent's
  decisions across restarts.
* Zero infra. ``sqlite3`` ships with Python — no external service to run, unlike
  Qdrant which is a separate process/container.

A future upgrade could add vector recall (e.g. an embeddings column or a Qdrant
mirror) for semantic similarity; today keyword/recency recall is enough and does
NOT require embeddings.

The DB path is configurable via ``Settings.agent_memory_db`` (env
``AGENT_MEMORY_DB``), defaulting to a repo-relative ``agent_memory.db`` that
``.gitignore`` already ignores (``*.db``). Tests pass an explicit path or
``":memory:"``.

Graceful degradation: every sqlite error is swallowed and the LLM-facing tools
return a short string ("memory unavailable" / "nothing recalled") — they NEVER
raise, so a flaky/read-only DB can never break the agent.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool

from config import get_settings

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    card_title TEXT NOT NULL,
    card_description TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL DEFAULT '',
    persona_action TEXT NOT NULL DEFAULT '',
    program_json TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
)
"""


class AgentMemoryStore:
    """Small sqlite persistence layer over the agent's interpretation decisions.

    Lazily creates the ``decisions`` table on first use. Pass an explicit
    ``db_path`` (a file path, or ``":memory:"``) to isolate tests; when omitted
    the configured ``Settings.agent_memory_db`` is used.
    """

    def __init__(self, db_path: str | None = None) -> None:
        # Resolve lazily-configured path but capture it now so a single store
        # instance is stable even if Settings later changes.
        self._db_path = db_path if db_path is not None else get_settings().agent_memory_db

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(_CREATE_TABLE)
        return conn

    def remember(
        self,
        card_title: str,
        card_description: str,
        verdict: str,
        persona_action: str = "",
        program_json: str = "",
        note: str = "",
    ) -> None:
        """Persist one interpretation decision. Raises on sqlite error.

        The LLM-facing tool wrappers catch errors; this method surfaces them so
        callers/tests can distinguish success from failure.
        """
        created_at = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO decisions "
                "(created_at, card_title, card_description, verdict, persona_action, program_json, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (created_at, card_title, card_description, verdict, persona_action, program_json, note),
            )
            conn.commit()
        finally:
            conn.close()

    def recall(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return recent decisions matching ``query`` (LIKE on title/description/note).

        Most recent first (by id, which is monotonic with insertion order).
        Raises on sqlite error; the tool wrapper degrades gracefully.
        """
        like = f"%{query}%"
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, created_at, card_title, card_description, verdict, "
                "persona_action, program_json, note FROM decisions "
                "WHERE card_title LIKE ? OR card_description LIKE ? OR note LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]


def _summarize(decisions: list[dict[str, Any]]) -> str:
    """Render recalled decisions as a concise, LLM-readable text block."""
    lines: list[str] = []
    for d in decisions:
        parts = [f'- "{d["card_title"]}" -> verdict: {d["verdict"] or "n/a"}']
        if d.get("persona_action"):
            parts.append(f"persona_action: {d['persona_action']}")
        if d.get("note"):
            parts.append(f"note: {d['note']}")
        lines.append("; ".join(parts))
    return "\n".join(lines)


@tool
def remember_decision(card_title: str, card_description: str, verdict: str, note: str = "") -> str:
    """Record how you interpreted a card so future rulings stay consistent."""
    try:
        AgentMemoryStore().remember(
            card_title=card_title,
            card_description=card_description,
            verdict=verdict,
            note=note,
        )
    except sqlite3.Error:
        logger.warning("agent_memory: remember failed", exc_info=True)
        return "memory unavailable"
    return f"Recorded decision for card '{card_title}'."


@tool
def recall_decisions(query: str) -> str:
    """Recall your previous card interpretations similar to the current one, to rule consistently."""
    try:
        decisions = AgentMemoryStore().recall(query)
    except sqlite3.Error:
        logger.warning("agent_memory: recall failed", exc_info=True)
        return "memory unavailable"
    if not decisions:
        return "nothing recalled"
    return "Prior decisions:\n" + _summarize(decisions)


def get_agent_memory_tools() -> list:
    """Return the agent-memory tool objects (remember + recall)."""
    return [remember_decision, recall_decisions]
