"""Validates the real_cards.json eval dataset shape and coverage."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent.parent / "data" / "eval" / "real_cards.json"

_VALID_TIMING = {"immediate", "modifier"}
_VALID_TARGET = {"self", "player", "all", "center"}
_VALID_PLACEMENT = {"self", "player", "center"}
_VALID_SIGN = {"positive", "negative", "neutral"}

# A genuine Imgur direct image link, e.g. https://i.imgur.com/abc123.jpg.
_IMGUR_DIRECT_URL_RE = re.compile(
    r"^https://i\.imgur\.com/[A-Za-z0-9]+\.(?:jpg|jpeg|png|gif|webp)$",
    re.IGNORECASE,
)
# Substrings marking a URL as a known offline placeholder rather than a real photo.
_PLACEHOLDER_MARKERS = ("fallback_", "placeholder")


def _load() -> list[dict]:
    return json.loads(DATA.read_text(encoding="utf-8"))


def _is_placeholder(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def test_count_in_range() -> None:
    cards = _load()
    assert 30 <= len(cards) <= 50


def test_every_card_has_required_fields() -> None:
    for c in _load():
        assert c["title"]
        assert c["description"]
        assert "image_url" in c
        hc = c["human_canonical"]
        assert hc is not None
        assert hc["timing"] in _VALID_TIMING
        assert hc["target"] in _VALID_TARGET
        assert hc["placement"] in _VALID_PLACEMENT
        assert hc["magnitude_sign"] in _VALID_SIGN
        assert ("ops" in hc) or ("snippet" in hc)


def test_diversity() -> None:
    cards = _load()
    hcs = [c["human_canonical"] for c in cards]
    assert sum(1 for h in hcs if "snippet" in h and h.get("snippet")) >= 5
    assert sum(1 for h in hcs if h["timing"] == "modifier") >= 6
    assert sum(1 for h in hcs if h["target"] == "all") >= 3
    assert any(h["magnitude_sign"] == "negative" for h in hcs)
    assert any(h["magnitude_sign"] == "neutral" for h in hcs)


def test_titles_unique() -> None:
    titles = [c["title"] for c in _load()]
    assert len(titles) == len(set(titles))


def test_image_urls_are_placeholders_documents_defect() -> None:
    """Documents the current defect: every image_url is a broken placeholder.

    This test PASSES today because ``real_cards.json`` still ships offline
    ``fallback_NNN.jpg`` stand-ins (see bead 75j). When the corpus is
    regenerated with a real ``IMGUR_CLIENT_ID``, this test will start failing
    and should be deleted in favour of the strict ``xfail`` check below.
    """
    urls = [c["image_url"] for c in _load()]
    assert urls, "expected at least one card"
    assert all(_is_placeholder(u) for u in urls)


@pytest.mark.xfail(
    reason=("real_cards.json still has placeholder image URLs — needs IMGUR_CLIENT_ID to regenerate; see bead 75j"),
    strict=True,
)
def test_image_urls_are_real_imgur_direct_links() -> None:
    """Strict target: every image_url is a real, non-placeholder Imgur direct link.

    Marked ``xfail(strict=True)`` because the committed corpus still holds
    placeholders. Once regenerated from the real album this will XPASS, at which
    point the ``xfail`` marker should be removed to enforce the invariant.
    """
    for c in _load():
        url = c["image_url"]
        assert not _is_placeholder(url), f"placeholder URL: {url}"
        assert _IMGUR_DIRECT_URL_RE.match(url), f"not an Imgur direct link: {url}"
