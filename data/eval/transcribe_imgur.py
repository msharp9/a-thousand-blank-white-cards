"""One-off GPT-vision transcription of real 1000 Blank White Cards photos.

This is a STANDALONE utility (not imported by the app, not run in CI). It reads
card photos from a public Imgur album, asks an OpenAI vision model to transcribe
each card verbatim, and writes the results to ``data/eval/real_cards.json`` for
later human annotation (see ``ANNOTATION_GUIDE.md``).

Run it directly::

    OPENAI_API_KEY=... IMGUR_CLIENT_ID=... uv run python data/eval/transcribe_imgur.py

The core functions accept injected dependencies so the module is importable and
unit-testable without any network or API access.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

# Imgur album id for the real-card photo set.
IMGUR_ALBUM_ID = "rWS9A"

# Vision-capable OpenAI model used for transcription.
VISION_MODEL = "gpt-5.4-mini"

# Default output location for the transcribed corpus.
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent / "real_cards.json"

# Placeholder image URLs used when the Imgur API is unavailable or no client id
# is configured. These are NOT guaranteed to resolve to real images -- they are
# stand-ins so the script (and its tests) can run offline. Replace them, or set
# IMGUR_CLIENT_ID, to transcribe the actual album.
FALLBACK_IMAGE_URLS: list[str] = [
    "https://i.imgur.com/placeholder1.jpg",
    "https://i.imgur.com/placeholder2.jpg",
    "https://i.imgur.com/placeholder3.jpg",
    "https://i.imgur.com/placeholder4.jpg",
]

# System instruction steering the model to transcribe faithfully and emit JSON.
_TRANSCRIBE_SYSTEM = SystemMessage(
    content=(
        "You are transcribing a photograph of a hand-made playing card from the "
        "party game '1000 Blank White Cards'. Read the card verbatim -- do not "
        "invent, correct, or interpret its meaning. Respond with a single JSON "
        'object and nothing else, using exactly these keys: {"title": <the card '
        'title / name text>, "description": <the remaining body / rules text, '
        "verbatim>}. If the card has no separate title, use an empty string for "
        '"title".'
    )
)


def _fetch_image_urls() -> list[str]:
    """Return image URLs for the album, falling back to placeholders on any failure.

    When ``IMGUR_CLIENT_ID`` is set, query the Imgur API for the album images and
    parse the ``data[].link`` fields. Any missing client id, network error, or
    unexpected payload results in :data:`FALLBACK_IMAGE_URLS`.
    """
    client_id = os.environ.get("IMGUR_CLIENT_ID")
    if not client_id:
        logger.info("IMGUR_CLIENT_ID not set; using fallback image URLs.")
        return FALLBACK_IMAGE_URLS

    try:
        response = httpx.get(
            f"https://api.imgur.com/3/album/{IMGUR_ALBUM_ID}/images",
            headers={"Authorization": f"Client-ID {client_id}"},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        urls = [item["link"] for item in payload["data"] if item.get("link")]
        if not urls:
            logger.warning("Imgur returned no image links; using fallback URLs.")
            return FALLBACK_IMAGE_URLS
        return urls
    except Exception:
        logger.exception("Failed to fetch Imgur album; using fallback URLs.")
        return FALLBACK_IMAGE_URLS


def _build_llm():
    """Lazily construct a real vision ChatOpenAI client (requires OPENAI_API_KEY)."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=VISION_MODEL, temperature=0, openai_api_key=os.environ["OPENAI_API_KEY"])


def transcribe_image(url: str, llm=None) -> dict | None:
    """Transcribe a single card image into a corpus record, or ``None`` on failure.

    Args:
        url: Public image URL of the card photo.
        llm: Optional injected chat model with an ``.invoke`` method. When omitted,
            a real :class:`~langchain_openai.ChatOpenAI` vision client is built.

    Returns:
        ``{"image_url", "title", "description", "human_canonical"}`` on success,
        where ``human_canonical`` is always ``None`` (filled in later by a human).
        Returns ``None`` if the LLM call or JSON parsing fails.
    """
    if llm is None:
        llm = _build_llm()

    message = HumanMessage(
        content=[
            {"type": "text", "text": "Transcribe this card."},
            {"type": "image_url", "image_url": {"url": url}},
        ]
    )

    try:
        response = llm.invoke([_TRANSCRIBE_SYSTEM, message])
        parsed = json.loads(response.content)
        return {
            "image_url": url,
            "title": parsed["title"],
            "description": parsed["description"],
            "human_canonical": None,
        }
    except Exception:
        logger.exception("Failed to transcribe image: %s", url)
        return None


def run(output_path: Path | None = None, urls: list[str] | None = None, llm=None) -> int:
    """Transcribe every card image and write the corpus JSON array.

    Args:
        output_path: Destination file. Defaults to :data:`DEFAULT_OUTPUT_PATH`.
        urls: Image URLs to transcribe. Defaults to :func:`_fetch_image_urls`.
        llm: Optional injected chat model (see :func:`transcribe_image`).

    Returns:
        The number of records successfully transcribed and written.
    """
    output_path = output_path or DEFAULT_OUTPUT_PATH
    urls = urls if urls is not None else _fetch_image_urls()

    records = []
    for url in urls:
        record = transcribe_image(url, llm=llm)
        if record is not None:
            records.append(record)
        else:
            logger.warning("Skipping image (transcription failed): %s", url)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    return len(records)


def main() -> None:
    """Entry point: transcribe the album and print how many cards were written."""
    logging.basicConfig(level=logging.INFO)
    count = run()
    print(f"Transcribed {count} card(s) to {DEFAULT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
