"""One-off GPT-vision transcription of real 1000 Blank White Cards photos.

This is a STANDALONE utility (not imported by the app, not run in CI). It reads
card photos from a public Imgur album, asks an OpenAI vision model to transcribe
each card verbatim, and writes the results to ``data/eval/real_cards.json`` for
later human annotation (see ``ANNOTATION_GUIDE.md``).

Run it directly::

    OPENAI_API_KEY=... IMGUR_CLIENT_ID=... uv run python data/eval/transcribe_imgur.py

The core functions accept injected dependencies so the module is importable and
unit-testable without any network or API access.

Design notes
------------
* :func:`fetch_image_urls` pulls the *entire* album, following Imgur's paged
  ``/album/{id}/images`` responses rather than assuming a fixed card count.
* Every URL is validated with :func:`is_valid_imgur_url` -- only genuine
  ``https://i.imgur.com/<hash>.<ext>`` direct links are accepted. Known-bad
  placeholders (e.g. the ``fallback_NNN.jpg`` links previously committed to
  ``real_cards.json``) are rejected so the script never writes junk URLs.
* Failures are LOUD: a missing client id or an album that yields no valid URLs
  raises rather than silently emitting placeholders.
"""

from __future__ import annotations

import json
import logging
import os
import re
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

# Imgur serves at most this many images per album page.
_IMGUR_PAGE_SIZE = 50

# A genuine Imgur direct image link, e.g. https://i.imgur.com/abc123.jpg.
# The image hash is alphanumeric; extension is a common raster/animated format.
_IMGUR_DIRECT_URL_RE = re.compile(
    r"^https://i\.imgur\.com/[A-Za-z0-9]+\.(?:jpg|jpeg|png|gif|webp)$",
    re.IGNORECASE,
)

# Substrings that mark a URL as a known placeholder rather than a real photo.
# These were written by an earlier offline run and must never be accepted.
_PLACEHOLDER_MARKERS: tuple[str, ...] = ("fallback_", "placeholder")

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


class TranscriptionError(RuntimeError):
    """Raised when the album cannot be fetched or yields no usable image URLs."""


def is_valid_imgur_url(url: object) -> bool:
    """Return ``True`` only for a real Imgur direct image link.

    A valid URL matches ``https://i.imgur.com/<hash>.<ext>`` and contains none
    of the known placeholder markers (``fallback_``, ``placeholder``). This is
    the single guard that keeps stand-in URLs out of the corpus.
    """
    if not isinstance(url, str):
        return False
    lowered = url.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return False
    return _IMGUR_DIRECT_URL_RE.match(url) is not None


def _fetch_album_page(client_id: str, page: int) -> list[dict]:
    """Fetch a single page of album image records from the Imgur API."""
    response = httpx.get(
        f"https://api.imgur.com/3/album/{IMGUR_ALBUM_ID}/images",
        headers={"Authorization": f"Client-ID {client_id}"},
        params={"page": page},
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data")
    if not isinstance(data, list):
        raise TranscriptionError(f"Unexpected Imgur payload on page {page}: 'data' is not a list.")
    return data


def fetch_image_urls(client_id: str | None = None) -> list[str]:
    """Return every validated image URL for the album, following pagination.

    Args:
        client_id: Imgur API client id. Defaults to ``$IMGUR_CLIENT_ID``.

    Returns:
        A de-duplicated list of validated ``https://i.imgur.com/<hash>.<ext>``
        direct links, in album order.

    Raises:
        TranscriptionError: If no client id is available, the API errors, or the
            album yields zero valid image URLs. The script fails loudly here
            rather than falling back to placeholder URLs.
    """
    client_id = client_id or os.environ.get("IMGUR_CLIENT_ID")
    if not client_id:
        raise TranscriptionError(
            "IMGUR_CLIENT_ID is not set. A real Imgur client id is required to "
            "fetch the album; refusing to emit placeholder URLs."
        )

    urls: list[str] = []
    seen: set[str] = set()
    page = 0
    while True:
        try:
            records = _fetch_album_page(client_id, page)
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"Failed to fetch Imgur album page {page}: {exc}") from exc

        if not records:
            break

        for item in records:
            link = item.get("link")
            if not link:
                continue
            if not is_valid_imgur_url(link):
                logger.warning("Rejecting non-direct/placeholder Imgur URL: %s", link)
                continue
            if link in seen:
                continue
            seen.add(link)
            urls.append(link)

        # A short page means we've reached the end of the album.
        if len(records) < _IMGUR_PAGE_SIZE:
            break
        page += 1

    if not urls:
        raise TranscriptionError("Imgur returned no valid direct image URLs for the album; nothing to transcribe.")

    logger.info("Fetched %d valid image URL(s) from album %s.", len(urls), IMGUR_ALBUM_ID)
    return urls


def _build_llm():
    """Lazily construct a real vision ChatOpenAI client (requires OPENAI_API_KEY)."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=VISION_MODEL, temperature=0, openai_api_key=os.environ["OPENAI_API_KEY"])


def transcribe_image(url: str, llm=None) -> dict | None:
    """Transcribe a single card image into a corpus record, or ``None`` on failure.

    Args:
        url: Public image URL of the card photo. Must be a valid Imgur direct
            link (see :func:`is_valid_imgur_url`).
        llm: Optional injected chat model with an ``.invoke`` method. When omitted,
            a real :class:`~langchain_openai.ChatOpenAI` vision client is built.

    Returns:
        ``{"image_url", "title", "description", "human_canonical"}`` on success,
        where ``human_canonical`` is always ``None`` (filled in later by a human).
        Returns ``None`` if the URL is invalid or the LLM call / JSON parsing fails.
    """
    if not is_valid_imgur_url(url):
        logger.warning("Refusing to transcribe invalid/placeholder URL: %s", url)
        return None

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
        urls: Image URLs to transcribe. Defaults to :func:`fetch_image_urls`
            (the full album). Any URL failing :func:`is_valid_imgur_url` is
            rejected before writing.
        llm: Optional injected chat model (see :func:`transcribe_image`).

    Returns:
        The number of records successfully transcribed and written.

    Raises:
        TranscriptionError: If no valid URLs are available to transcribe, or if
            transcription produced zero records (so we never overwrite the
            corpus with an empty array).
    """
    output_path = output_path or DEFAULT_OUTPUT_PATH
    urls = urls if urls is not None else fetch_image_urls()

    valid_urls = [u for u in urls if is_valid_imgur_url(u)]
    if not valid_urls:
        raise TranscriptionError("No valid Imgur direct URLs to transcribe.")

    records = []
    for url in valid_urls:
        record = transcribe_image(url, llm=llm)
        if record is not None:
            records.append(record)
        else:
            logger.warning("Skipping image (transcription failed): %s", url)

    if not records:
        raise TranscriptionError(
            f"Transcription produced zero records; refusing to overwrite {output_path} with an empty corpus."
        )

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
