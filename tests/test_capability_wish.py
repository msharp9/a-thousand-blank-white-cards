from __future__ import annotations

import json
from unittest.mock import patch

from agent.tools import get_default_tools
from agent.tools.capability_wish import record_capability_wish


def test_wish_records_bounded_exportable_jsonl_without_issue_tracker(tmp_path) -> None:
    path = tmp_path / "wishes.jsonl"
    record = record_capability_wish(
        "Auction",
        "Highest bidder wins.",
        "Ask every player for a sealed number.",
        "sealed multiplayer numeric input",
        path=path,
    )

    stored = json.loads(path.read_text())
    assert record["recorded"] is True
    assert stored == {key: value for key, value in record.items() if key != "recorded"}
    assert stored["missing_capability"] == "sealed multiplayer numeric input"
    assert "id" in stored and "created_at" in stored


def test_default_tools_include_wish_telemetry() -> None:
    assert "wish" in {tool.name for tool in get_default_tools()}


def test_wish_store_cap_is_non_throwing(tmp_path) -> None:
    path = tmp_path / "wishes.jsonl"
    path.write_text("x" * 100)

    result = record_capability_wish("Card", "Rule", "Intent", "Gap", path=path, max_bytes=100)

    assert result == {"recorded": False, "error": "capability wish store is full"}
    assert path.read_text() == "x" * 100


def test_unwritable_wish_store_is_non_throwing(tmp_path) -> None:
    with patch.object(type(tmp_path), "open", side_effect=OSError("read only")):
        result = record_capability_wish("Card", "Rule", "Intent", "Gap", path=tmp_path / "wishes.jsonl")

    assert result == {"recorded": False, "error": "capability wish store is unavailable"}
