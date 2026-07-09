"""Tests for the one-off Imgur transcription script (network + LLM mocked)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "data" / "eval" / "transcribe_imgur.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("transcribe_imgur", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_fetch_urls_falls_back_without_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IMGUR_CLIENT_ID", raising=False)
    mod = _load_module()
    urls = mod._fetch_image_urls()
    assert urls == mod.FALLBACK_IMAGE_URLS
    assert len(urls) >= 1


def test_transcribe_image_parses_llm_json() -> None:
    mod = _load_module()
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content='{"title": "Gain 5", "description": "Gain 5 points."}')
    result = mod.transcribe_image("https://i.imgur.com/x.jpg", llm=fake_llm)
    assert result["image_url"] == "https://i.imgur.com/x.jpg"
    assert result["title"] == "Gain 5"
    assert result["human_canonical"] is None


def test_transcribe_image_returns_none_on_bad_json() -> None:
    mod = _load_module()
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content="not json at all")
    assert mod.transcribe_image("https://i.imgur.com/x.jpg", llm=fake_llm) is None


def test_run_writes_json(tmp_path: Path) -> None:
    mod = _load_module()
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content='{"title": "T", "description": "D"}')
    out = tmp_path / "real_cards.json"
    n = mod.run(output_path=out, urls=["https://i.imgur.com/a.jpg", "https://i.imgur.com/b.jpg"], llm=fake_llm)
    assert n == 2
    data = json.loads(out.read_text())
    assert len(data) == 2
    assert data[0]["human_canonical"] is None
