"""Tests for the one-off Imgur transcription script (network + LLM mocked)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "data_prep" / "transcribe_imgur.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("transcribe_imgur", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# --- URL validation ---------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://i.imgur.com/abc123.jpg",
        "https://i.imgur.com/AbC123.png",
        "https://i.imgur.com/xY9.jpeg",
        "https://i.imgur.com/foo.gif",
        "https://i.imgur.com/bar.webp",
    ],
)
def test_is_valid_imgur_url_accepts_direct_links(url: str) -> None:
    mod = _load_module()
    assert mod.is_valid_imgur_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://i.imgur.com/fallback_001.jpg",  # the committed placeholder bug
        "https://i.imgur.com/placeholder1.jpg",
        "https://imgur.com/abc123",  # gallery page, not a direct link
        "http://i.imgur.com/abc123.jpg",  # not https
        "https://i.imgur.com/abc123.bmp",  # unsupported extension
        "https://i.imgur.com/abc123",  # missing extension
        "https://evil.example.com/abc123.jpg",  # wrong host
        "",
        None,
        123,
    ],
)
def test_is_valid_imgur_url_rejects_bad_links(url: object) -> None:
    mod = _load_module()
    assert not mod.is_valid_imgur_url(url)


# --- album fetching ---------------------------------------------------------


def test_fetch_image_urls_raises_without_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IMGUR_CLIENT_ID", raising=False)
    mod = _load_module()
    with pytest.raises(mod.TranscriptionError, match="IMGUR_CLIENT_ID"):
        mod.fetch_image_urls()


def test_fetch_image_urls_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module()

    page_size = mod._IMGUR_PAGE_SIZE
    first_page = [{"link": f"https://i.imgur.com/pa{i:03d}.jpg"} for i in range(page_size)]
    second_page = [{"link": "https://i.imgur.com/pb001.jpg"}]

    def fake_page(client_id: str, page: int):
        assert client_id == "cid"
        return first_page if page == 0 else second_page

    monkeypatch.setattr(mod, "_fetch_album_page", fake_page)
    urls = mod.fetch_image_urls(client_id="cid")
    assert len(urls) == page_size + 1
    assert urls[-1] == "https://i.imgur.com/pb001.jpg"


def test_fetch_image_urls_rejects_placeholders_and_dedupes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_module()
    page = [
        {"link": "https://i.imgur.com/good1.jpg"},
        {"link": "https://i.imgur.com/fallback_001.jpg"},  # rejected
        {"link": "https://i.imgur.com/good1.jpg"},  # duplicate
        {"link": None},  # skipped
        {},  # skipped
        {"link": "https://i.imgur.com/good2.png"},
    ]
    monkeypatch.setattr(mod, "_fetch_album_page", lambda cid, p: page if p == 0 else [])
    urls = mod.fetch_image_urls(client_id="cid")
    assert urls == ["https://i.imgur.com/good1.jpg", "https://i.imgur.com/good2.png"]


def test_fetch_image_urls_raises_when_no_valid_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_module()
    page = [{"link": "https://i.imgur.com/fallback_001.jpg"}]
    monkeypatch.setattr(mod, "_fetch_album_page", lambda cid, p: page if p == 0 else [])
    with pytest.raises(mod.TranscriptionError, match="no valid direct image URLs"):
        mod.fetch_image_urls(client_id="cid")


def test_fetch_image_urls_wraps_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module()

    def boom(client_id: str, page: int):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(mod, "_fetch_album_page", boom)
    with pytest.raises(mod.TranscriptionError, match="Failed to fetch Imgur album"):
        mod.fetch_image_urls(client_id="cid")


def test_fetch_album_page_rejects_non_list_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module()

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": "not-a-list"}

    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: FakeResponse())
    with pytest.raises(mod.TranscriptionError, match="not a list"):
        mod._fetch_album_page("cid", 0)


# --- transcription ----------------------------------------------------------


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


def test_transcribe_image_rejects_invalid_url() -> None:
    mod = _load_module()
    fake_llm = MagicMock()
    assert mod.transcribe_image("https://i.imgur.com/fallback_001.jpg", llm=fake_llm) is None
    fake_llm.invoke.assert_not_called()


def test_run_writes_json(tmp_path: Path) -> None:
    mod = _load_module()
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content='{"title": "T", "description": "D"}')
    out = tmp_path / "real_cards.json"
    n = mod.run(
        output_path=out,
        urls=["https://i.imgur.com/a.jpg", "https://i.imgur.com/b.jpg"],
        llm=fake_llm,
    )
    assert n == 2
    data = json.loads(out.read_text())
    assert len(data) == 2
    assert data[0]["human_canonical"] is None


def test_run_raises_when_all_urls_invalid(tmp_path: Path) -> None:
    mod = _load_module()
    out = tmp_path / "real_cards.json"
    with pytest.raises(mod.TranscriptionError, match="No valid Imgur direct URLs"):
        mod.run(output_path=out, urls=["https://i.imgur.com/fallback_001.jpg"], llm=MagicMock())
    assert not out.exists()


def test_run_raises_when_no_records_transcribed(tmp_path: Path) -> None:
    mod = _load_module()
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content="garbage")  # every transcribe fails
    out = tmp_path / "real_cards.json"
    with pytest.raises(mod.TranscriptionError, match="zero records"):
        mod.run(output_path=out, urls=["https://i.imgur.com/a.jpg"], llm=fake_llm)
    assert not out.exists()
