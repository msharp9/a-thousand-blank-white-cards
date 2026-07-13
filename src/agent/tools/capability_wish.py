"""Persistent, data-only telemetry for card effects the engine cannot express."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.tools import StructuredTool

from config import get_settings

_write_lock = threading.Lock()


def record_capability_wish(
    card_title: str,
    card_description: str,
    what_i_wanted: str,
    missing_capability: str,
    *,
    path: str | Path | None = None,
    max_bytes: int | None = None,
) -> dict[str, object]:
    """Best-effort append with a hard sink cap; telemetry never breaks play."""
    settings = get_settings()
    destination = Path(path or settings.capability_wish_path)
    limit = max_bytes if max_bytes is not None else settings.capability_wish_max_bytes
    record = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "card_title": card_title.strip()[:120],
        "card_description": card_description.strip()[:1000],
        "what_i_wanted": what_i_wanted.strip()[:1000],
        "missing_capability": missing_capability.strip()[:240],
    }
    line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock:
            current_size = destination.stat().st_size if destination.exists() else 0
            if current_size + len(line.encode("utf-8")) > limit:
                return {"recorded": False, "error": "capability wish store is full"}
            with destination.open("a", encoding="utf-8") as stream:
                stream.write(line)
    except OSError:
        return {"recorded": False, "error": "capability wish store is unavailable"}
    return {"recorded": True, **record}


def get_capability_wish_tool() -> StructuredTool:
    def wish(
        card_title: str,
        card_description: str,
        what_i_wanted: str,
        missing_capability: str,
    ) -> str:
        """Record a missing board capability only after ops, sandbox code, hooks, and interactions cannot express a card. This writes telemetry for human review; it never edits source code or files an issue at runtime."""
        record = record_capability_wish(
            card_title,
            card_description,
            what_i_wanted,
            missing_capability,
        )
        if not record["recorded"]:
            return json.dumps(record)
        return json.dumps({"recorded": True, "wish_id": record["id"]})

    return StructuredTool.from_function(
        func=wish,
        name="wish",
        description=(
            "Persist a capability gap for human review when no available op, SandboxGame method, hook, "
            "or interaction can implement the card. Never edits runtime source or invokes bd."
        ),
    )
