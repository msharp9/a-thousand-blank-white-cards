"""agent.tools.card_rag — LangChain tool wrapping the dense RAG retriever.

Exposes a single ``card_rag`` @tool the agent can call to find precedent cards
similar to the one it is interpreting. This is the dense baseline retriever
tool; ``card_rag_hybrid`` (BM25+dense RRF) is the one bound in the default
toolbox. This module stays importable for tests and eval experiments, and
``card_rag_hybrid`` reuses its ``_format_hits`` helper.

The tool uses the deterministic dense (cosine) retriever from
``agent.rag.retrievers`` and degrades gracefully: if the store is empty,
uninitialised, or the retriever raises (missing embeddings key / Qdrant), it
returns a short human-readable string instead of propagating the exception.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from agent.rag.retrievers import dense_retriever

logger = logging.getLogger(__name__)

_NO_RESULTS = "no similar cards found"
_UNAVAILABLE = "card retrieval unavailable"
_RESULT_MAX_CHARS = 8_000
_OMITTED = "canonical omitted to preserve the result budget"


def _format_canonical(raw: object) -> str:
    """Compact stored canonical JSON without slicing executable code.

    Returns "" if raw is missing/empty/unparseable so callers can omit it.
    """
    if not raw or not isinstance(raw, str):
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    return json.dumps(parsed, separators=(",", ":"))


def _format_hits(hits: list[dict[str, Any]]) -> str:
    """Format retriever payload dicts into a concise text block, one line per hit.

    Each line is ``title — description — score — canonical={...}``, with the
    canonical segment omitted when the hit has no usable payload. Missing
    fields degrade to sensible placeholders rather than raising.
    """
    lines: list[str] = []
    for hit in hits:
        title = str(hit.get("title") or "(untitled)").strip()
        description = str(hit.get("description") or "").strip()
        score = hit.get("score")
        try:
            score_str = f"{float(score):.3f}"
        except TypeError:
            score_str = "n/a"
        except ValueError:
            score_str = "n/a"
        line = f"{title} — {description} — score={score_str}"
        canonical = _format_canonical(hit.get("canonical"))
        if canonical:
            with_canonical = f"{line} — canonical={canonical}"
            current_size = sum(len(existing) + 1 for existing in lines)
            if not lines or current_size + len(with_canonical) <= _RESULT_MAX_CHARS:
                line = with_canonical
            else:
                line += f" — {_OMITTED}"
        lines.append(line)
    return "\n".join(lines)


@tool
def card_rag(query: str, k: int = 4) -> str:
    """Retrieve previously-seen cards similar to the given text, to compare a new card against precedent interpretations."""
    try:
        retrieve = dense_retriever()
        hits = retrieve(query, k)
    except Exception as exc:
        logger.warning("card_rag retrieval unavailable (non-fatal): %s", exc)
        return _UNAVAILABLE
    if not hits:
        return _NO_RESULTS
    return _format_hits(hits)


def get_card_rag_tool():
    """Return the ``card_rag`` LangChain tool object."""
    return card_rag
