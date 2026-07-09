"""Download the real 1000 Blank White Cards photo dataset from Imgur.

This is a STANDALONE utility (not imported by the app, not run in CI). It
downloads every card photo from the public Imgur album into a local, gitignored
folder so the corpus is available offline for eval / annotation work.

Run it directly::

    uv run python data/eval/download_images.py

No API key is required: the album's full media manifest is embedded in the
public album HTML (``window.postDataJSON``), so we parse the direct
``https://i.imgur.com/<hash>.<ext>`` links out of the page and fetch each one.
Downloads are idempotent -- files that already exist locally are skipped -- so
the script can be re-run to resume a partial download.

Design notes
------------
* :func:`extract_image_urls` scrapes the embedded media manifest rather than
  hitting the rate-limited ``api.imgur.com`` endpoint (which 429s without a
  client id). The album originally held ~1100 cards; the owner curated it down
  to ~700, which is the authoritative count we mirror here.
* Failures are LOUD but non-fatal per-image: a single 404 logs a warning and is
  recorded in the manifest as failed rather than aborting the whole run.
* A ``manifest.json`` is written alongside the images mapping each Imgur URL to
  its local filename, so downstream tooling (transcription, annotation) can join
  the two without re-scraping.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Imgur album id (and public slug) for the real-card photo set.
IMGUR_ALBUM_ID = "rWS9A"
ALBUM_URL = "https://imgur.com/a/blank-white-cards-5-2012-5-2013-rWS9A"

# Local, gitignored destination for the downloaded photos + manifest.
IMAGES_DIR = Path(__file__).resolve().parent / "images"
MANIFEST_PATH = IMAGES_DIR / "manifest.json"

# Tracked (committed) list of the album's direct image URLs, so the link set is
# available without re-scraping. Written by :func:`run`.
URLS_PATH = Path(__file__).resolve().parent / "image_urls.json"

# A genuine Imgur direct image link, e.g. https://i.imgur.com/abc123.jpeg.
_IMGUR_DIRECT_URL_RE = re.compile(
    r"https://i\.imgur\.com/[A-Za-z0-9]+\.(?:jpg|jpeg|png|gif|webp)",
    re.IGNORECASE,
)

# The album's embedded media manifest lists each full-size image as
# ``\"url\":\"https://i.imgur.com/<hash>.<ext>\"``. Parsing this (rather than a
# blanket grep) avoids picking up thumbnail variants (e.g. ``<hash>h.jpg``).
_MEDIA_URL_RE = re.compile(
    r'url\\+":\\+"(https://i\.imgur\.com/[A-Za-z0-9]+\.(?:jpe?g|png|gif|webp))',
    re.IGNORECASE,
)

# Browser-ish UA: Imgur serves the full embedded manifest to real browsers.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


class DownloadError(RuntimeError):
    """Raised when the album page cannot be fetched or yields no image URLs."""


def extract_image_urls(html: str) -> list[str]:
    """Return every unique direct Imgur image URL embedded in the album HTML.

    Args:
        html: Raw HTML of the public Imgur album page.

    Returns:
        A de-duplicated list of ``https://i.imgur.com/<hash>.<ext>`` links in
        album order.

    Raises:
        DownloadError: If the page contains no direct image links.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    # Prefer the embedded media manifest (full-size links only, in album order);
    # fall back to a blanket grep if the page format changes.
    for match in _MEDIA_URL_RE.findall(html) or _IMGUR_DIRECT_URL_RE.findall(html):
        if match not in seen:
            seen.add(match)
            ordered.append(match)
    if not ordered:
        raise DownloadError(
            "No direct Imgur image URLs found in album HTML; the page format "
            "may have changed or the album may be unavailable."
        )
    logger.info("Extracted %d unique image URL(s) from the album page.", len(ordered))
    return ordered


def fetch_album_html(url: str = ALBUM_URL, client: httpx.Client | None = None) -> str:
    """Fetch the album page HTML, following redirects with a browser UA."""
    owns_client = client is None
    client = client or httpx.Client(headers=_BROWSER_HEADERS, follow_redirects=True, timeout=30.0)
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.text
    except httpx.HTTPError as exc:
        raise DownloadError(f"Failed to fetch album page {url}: {exc}") from exc
    finally:
        if owns_client:
            client.close()


def _filename_for(url: str) -> str:
    """Derive a stable local filename (``<hash>.<ext>``) from an Imgur URL."""
    return url.rsplit("/", 1)[-1]


def download_images(
    urls: list[str],
    dest_dir: Path = IMAGES_DIR,
    client: httpx.Client | None = None,
    delay: float = 0.05,
) -> dict:
    """Download each image into ``dest_dir``, skipping files already present.

    Args:
        urls: Direct Imgur image URLs to download.
        dest_dir: Destination directory (created if missing).
        client: Optional injected httpx client (for testing).
        delay: Seconds to sleep between requests to stay polite to Imgur.

    Returns:
        A manifest dict mapping each URL to ``{"filename", "status"}`` where
        status is one of ``"downloaded"``, ``"skipped"`` (already local), or
        ``"failed"``.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    owns_client = client is None
    client = client or httpx.Client(headers=_BROWSER_HEADERS, follow_redirects=True, timeout=60.0)

    manifest: dict[str, dict] = {}
    downloaded = skipped = failed = 0
    try:
        for i, url in enumerate(urls, start=1):
            filename = _filename_for(url)
            target = dest_dir / filename
            if target.exists() and target.stat().st_size > 0:
                manifest[url] = {"filename": filename, "status": "skipped"}
                skipped += 1
                continue
            try:
                response = client.get(url)
                response.raise_for_status()
                target.write_bytes(response.content)
                manifest[url] = {"filename": filename, "status": "downloaded"}
                downloaded += 1
                if i % 50 == 0:
                    logger.info("Progress: %d/%d images processed.", i, len(urls))
            except httpx.HTTPError as exc:
                logger.warning("Failed to download %s: %s", url, exc)
                manifest[url] = {"filename": filename, "status": "failed"}
                failed += 1
            if delay:
                time.sleep(delay)
    finally:
        if owns_client:
            client.close()

    logger.info(
        "Done: %d downloaded, %d skipped (already local), %d failed of %d total.",
        downloaded,
        skipped,
        failed,
        len(urls),
    )
    return manifest


def run(dest_dir: Path = IMAGES_DIR) -> dict:
    """Scrape the album, download every image, and write ``manifest.json``.

    Returns:
        The download manifest (see :func:`download_images`).
    """
    html = fetch_album_html()
    urls = extract_image_urls(html)
    # Persist the (committed) URL list so the link set is available without
    # re-scraping the album page.
    URLS_PATH.write_text(
        json.dumps(
            {
                "album_id": IMGUR_ALBUM_ID,
                "album_url": ALBUM_URL,
                "image_count": len(urls),
                "image_urls": urls,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    logger.info("Wrote %d image URL(s) to %s", len(urls), URLS_PATH)
    manifest = download_images(urls, dest_dir=dest_dir)
    manifest_path = dest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    logger.info("Wrote manifest for %d image(s) to %s", len(manifest), manifest_path)
    return manifest


def main() -> None:
    """Entry point: download the full album into ``data/eval/images/``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=IMAGES_DIR,
        help="Destination directory for downloaded images (default: data/eval/images/).",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    manifest = run(dest_dir=args.dest)
    ok = sum(1 for m in manifest.values() if m["status"] in ("downloaded", "skipped"))
    print(f"{ok}/{len(manifest)} card images available in {args.dest}")


if __name__ == "__main__":
    main()
