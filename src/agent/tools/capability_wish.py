"""Persistent, data-only telemetry for card effects the engine cannot express."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

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
