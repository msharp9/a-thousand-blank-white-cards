"""tbwc.agent.nodes — LangGraph interpretation nodes.

Each node is a pure-ish function (InterpretState) -> dict (partial state update).
Later beads append more nodes (retrieve, classify, emit_ops, judge, …) to this file.
"""

from __future__ import annotations

import functools
import logging
import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from tbwc.agent.llm import get_chat_model
from tbwc.agent.prompts import CLASSIFY_TEMPLATE, INTERPRETER_SYSTEM, JUDGE_SYSTEM
from tbwc.agent.schemas import Interpretation, SnippetEffect, Verdict
from tbwc.agent.state import InterpretState
from tbwc.models.effects import EffectProgram
from tbwc.rag.retrievers import advanced_retriever, dense_retriever
from tbwc.sandbox.validate import validate_snippet as ast_validate

logger = logging.getLogger(__name__)


def reason(state: InterpretState) -> dict:
    """Summarise the card's intent in one sentence to guide RAG retrieval.

    Reads: state["card_draft"]
    Writes: state["search_notes"] (a one-sentence intent summary string)
    """
    draft = state["card_draft"]
    llm = get_chat_model(temperature=0)
    messages = [
        {"role": "system", "content": INTERPRETER_SYSTEM},
        {
            "role": "human",
            "content": (
                f"Card title: {draft['title']}\n"
                f"Card description: {draft['description']}\n\n"
                "In ONE sentence, summarise what this card is intended to do "
                "so we can search for similar example cards."
            ),
        },
    ]
    response = llm.invoke(messages)
    return {"search_notes": response.content}


# ---------------------------------------------------------------------------
# retrieve node
# ---------------------------------------------------------------------------

_retriever = dense_retriever()

_RETRIEVER_CACHE: dict[str, Any] = {}


def _get_retriever(mode: str):
    """Return (and cache) the retriever callable for the given mode ('dense'|'advanced').

    The 'dense' mode always returns the module-level ``_retriever`` so tests (and
    callers) can patch ``tbwc.agent.nodes._retriever`` and have it take effect at
    call time. The 'advanced' retriever is constructed lazily and cached.
    """
    if mode == "advanced":
        if "advanced" not in _RETRIEVER_CACHE:
            _RETRIEVER_CACHE["advanced"] = advanced_retriever()
        return _RETRIEVER_CACHE["advanced"]
    return _retriever  # dense: always the (patchable) module-level retriever


def _clear_retriever_cache() -> None:
    """Test helper: reset the module-level retriever cache."""
    _RETRIEVER_CACHE.clear()


def retrieve(state: InterpretState, config: RunnableConfig | None = None) -> dict:
    """Search the RAG store for exemplar cards similar to the card being interpreted.

    Reads: state["card_draft"], state["search_notes"]
    Writes: state["retrieved"] (list of exemplar dicts from the RAG search)

    Config (under 'configurable'): retriever_mode = "dense" (default) | "advanced".
    Backward compatible: callable with just state (defaults to dense).

    Uses search_notes (intent summary) as the query if available, else falls back
    to "title\\ndescription".
    """
    configurable = (config or {}).get("configurable", {}) if config else {}
    mode = configurable.get("retriever_mode", "dense")
    retriever = _get_retriever(mode)

    draft = state["card_draft"]
    query = state.get("search_notes") or f"{draft['title']}\n{draft['description']}"
    exemplars = retriever(query, k=4)
    return {"retrieved": exemplars}


# ---------------------------------------------------------------------------
# route_search node + edge function
# ---------------------------------------------------------------------------

# Heuristic: flag a card for web search if it contains a quoted phrase or a
# multi-word proper noun (consecutive capitalised words) — signals of external
# references the LLM may not know from the card text alone.
_SEARCH_TRIGGERS = re.compile(
    r'"[^"]+"'  # quoted phrase
    r"|'[^']+'"  # single-quoted phrase
    r"|\b(?:the\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+"  # multi-word proper noun
)


def route_search(state: InterpretState) -> dict:
    """Heuristically decide whether a web search is needed.

    Reads: state["card_draft"], state["search_notes"]
    Writes: state["search_notes"] — appends " [web_search=yes]" or " [web_search=no]".

    Does NOT perform the search; only sets a flag the conditional edge reads.
    """
    draft = state["card_draft"]
    text = f"{draft['title']} {draft['description']}"
    needs_search = bool(_SEARCH_TRIGGERS.search(text))
    existing_notes = state.get("search_notes") or ""
    suffix = " [web_search=yes]" if needs_search else " [web_search=no]"
    return {"search_notes": existing_notes + suffix}


def should_search(state: InterpretState) -> str:
    """Conditional edge function: route to 'search' or 'classify'."""
    notes = state.get("search_notes", "")
    if "[web_search=yes]" in notes:
        return "search"
    return "classify"


# ---------------------------------------------------------------------------
# search node  (Tavily-backed)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _get_tavily_tool():
    """Lazily construct a TavilySearch tool (needs TAVILY_API_KEY)."""
    from langchain_tavily import TavilySearch

    return TavilySearch(max_results=5)


def search(state: InterpretState) -> dict:
    """Web-search for external context about references in the card.

    Uses Tavily to resolve ambiguous pop-culture / game-name references. Appends a
    short results summary to search_notes (kept as a single string). Non-fatal:
    on any failure (missing TAVILY_API_KEY, network error) it appends a notice and
    continues so the graph can still classify.
    """
    draft = state["card_draft"]
    existing = state.get("search_notes") or ""
    query = f"{draft['title']} {draft['description']}"[:200].strip() + " card game rules meaning"

    try:
        tool = _get_tavily_tool()
        results = tool.invoke({"query": query})
        notes: list[str] = []
        if isinstance(results, list):
            for r in results:
                if isinstance(r, dict) and "content" in r:
                    notes.append(str(r["content"])[:500])
                elif isinstance(r, str):
                    notes.append(r[:500])
        elif isinstance(results, str):
            notes = [results[:2000]]
        elif isinstance(results, dict) and "results" in results:
            for r in results["results"]:
                if isinstance(r, dict) and "content" in r:
                    notes.append(str(r["content"])[:500])
        summary = " | ".join(notes) if notes else "no results"
        return {"search_notes": existing + f" [web_search_results: {summary}]"}
    except Exception as exc:
        logger.warning("search node failed (non-fatal): %s", exc)
        return {"search_notes": existing + " [web_search_results: unavailable]"}


# ---------------------------------------------------------------------------
# classify node + edge function
# ---------------------------------------------------------------------------


def _format_exemplars(retrieved: list[dict]) -> str:
    """Format retrieved exemplars as readable text for the classify prompt."""
    if not retrieved:
        return "No similar cards found."
    lines = []
    for i, ex in enumerate(retrieved, 1):
        lines.append(
            f"{i}. Title: {ex.get('title', '?')}\n"
            f"   Description: {ex.get('description', '?')}\n"
            f"   Canonical: {ex.get('canonical', '?')}\n"
            f"   Score: {ex.get('score', 0):.2f}"
        )
    return "\n".join(lines)


def classify(state: InterpretState) -> dict:
    """Classify the card's effect into a structured Interpretation.

    Reads: state["card_draft"], state["retrieved"], state["search_notes"]
    Writes: state["interpretation"] (Interpretation), state["attempts"] (incremented).

    Uses ChatOpenAI.with_structured_output(Interpretation) for typed output.
    """
    draft = state["card_draft"]
    retrieved = state.get("retrieved") or []
    search_notes = state.get("search_notes") or "none"

    human_content = CLASSIFY_TEMPLATE.format(
        title=draft["title"],
        description=draft["description"],
        exemplars=_format_exemplars(retrieved),
        search_notes=search_notes,
    )

    llm = get_chat_model(temperature=0).with_structured_output(Interpretation)
    interpretation = llm.invoke(
        [
            {"role": "system", "content": INTERPRETER_SYSTEM},
            {"role": "human", "content": human_content},
        ]
    )
    return {"interpretation": interpretation, "attempts": state.get("attempts", 0) + 1}


def route_after_classify(state: InterpretState) -> str:
    """Conditional edge: route to emit_ops (immediate) or gen_snippet (snippet)."""
    interp = state.get("interpretation")
    if interp is None:
        return "gen_snippet"  # safe fallback
    if interp.mode == "immediate":
        return "emit_ops"
    return "gen_snippet"


# ---------------------------------------------------------------------------
# emit_ops node
# ---------------------------------------------------------------------------


def _format_exemplars_fewshot(retrieved: list[dict]) -> str:
    """Format up to 3 retrieved exemplars as few-shot guidance for emit_ops.

    Each exemplar shows the card text and its known canonical effect so the model
    can mirror concrete patterns instead of inventing op shapes.
    """
    top = retrieved[:3]
    if not top:
        return ""
    blocks = []
    for i, ex in enumerate(top, 1):
        blocks.append(
            f"Example {i}:\n"
            f"  Title: {ex.get('title', '?')}\n"
            f"  Description: {ex.get('description', '?')}\n"
            f"  Canonical effect: {ex.get('canonical', '?')}"
        )
    return "Here are similar example cards and their known-good effects:\n" + "\n".join(blocks) + "\n\n"


def emit_ops(state: InterpretState, config: RunnableConfig | None = None) -> dict:
    """Generate an EffectProgram for immediate-mode cards, using retrieved exemplars as few-shot.

    Reads: state["card_draft"], state["interpretation"], state["retrieved"]
    Writes: state["program"] (EffectProgram)

    Uses ChatOpenAI.with_structured_output(EffectProgram) so the output is always
    a valid, typed EffectProgram matching the engine schema.

    Config (under 'configurable'): few_shot_exemplars = True (default) | False.
    When False, no retrieved exemplars are injected as few-shot guidance.
    Backward compatible: callable with just state (defaults few-shot on).
    """
    configurable = (config or {}).get("configurable", {}) if config else {}
    use_few_shot = configurable.get("few_shot_exemplars", True)
    draft = state["card_draft"]
    interp = state["interpretation"]
    retrieved = state.get("retrieved") or [] if use_few_shot else []
    fewshot = _format_exemplars_fewshot(retrieved)

    human_content = (
        f"{fewshot}"
        f"Card title: {draft['title']}\n"
        f"Card description: {draft['description']}\n\n"
        f"Classification: {interp.model_dump_json()}\n\n"
        "Generate an EffectProgram: a list of immediate ops that faithfully "
        "implements this card's effect. Mirror the patterns in the examples above where "
        "applicable. Translate exactly — do not balance or modify."
    )

    llm = get_chat_model(temperature=0).with_structured_output(EffectProgram)
    program = llm.invoke(
        [
            {"role": "system", "content": INTERPRETER_SYSTEM},
            {"role": "human", "content": human_content},
        ]
    )
    return {"program": program}


# ---------------------------------------------------------------------------
# gen_snippet node
# ---------------------------------------------------------------------------

_SNIPPET_SYSTEM = """\
You are generating a Python function body for a card effect in "1000 Blank White Cards".

Requirements:
- Write ONLY the body of `def apply(state, ctx)`.
- Do NOT include any import statements.
- Do NOT use exec, eval, open, compile, or access dunder attributes (__class__, etc.).
- `state` is a GameState object with attributes: scores (dict[str,int]),
  hands (dict[str, list]), deck (list), discard (list), turn_order (list[str]),
  current_player_index (int), properties (dict).
- `ctx` is a dict with keys: player_id (str), card (Card), event (str|None).
- The function should mutate `state` in place and return None.
- Be a faithful literalist — implement exactly what the card says.
"""


def gen_snippet(state: InterpretState) -> dict:
    """Generate a Python def apply(state, ctx) body for snippet-mode cards.

    Reads: state["card_draft"], state["interpretation"]
    Writes: state["snippet"] (SnippetEffect), state["snippet_attempts"] (incremented).

    snippet_attempts strictly increases on every call so the
    gen_snippet<->validate_snippet retry loop is guaranteed to terminate.
    """
    draft = state["card_draft"]
    interp = state.get("interpretation")

    human_content = (
        f"Card title: {draft['title']}\n"
        f"Card description: {draft['description']}\n\n"
        f"Classification: {interp.model_dump_json() if interp else 'unknown'}\n\n"
        "Generate the body of def apply(state, ctx) that implements this card's "
        "effect faithfully. Remember: no imports, no forbidden calls."
    )

    llm = get_chat_model(temperature=0.2).with_structured_output(SnippetEffect)
    snippet = llm.invoke(
        [
            {"role": "system", "content": _SNIPPET_SYSTEM},
            {"role": "human", "content": human_content},
        ]
    )
    return {"snippet": snippet, "snippet_attempts": state.get("snippet_attempts", 0) + 1}


# ---------------------------------------------------------------------------
# validate_snippet node + edge function
# ---------------------------------------------------------------------------

MAX_ATTEMPTS = 3  # shared with the judge loop


def validate_snippet_node(state: InterpretState) -> dict:
    """Run the AST allowlist check on the generated snippet.

    Reads: state["snippet"]
    Writes: state["snippet_valid"] (bool). On failure also appends a
            "[validate_error: ...]" note to search_notes so gen_snippet can see
            why its code was rejected when it retries.

    Routing depends on the dedicated snippet_valid flag, not the search_notes
    substring, so a later VALID snippet is never re-routed by a prior error.
    On success: returns {"snippet_valid": True} and does NOT touch search_notes.
    """
    snippet = state.get("snippet")
    if snippet is None:
        existing = state.get("search_notes") or ""
        return {
            "snippet_valid": False,
            "search_notes": existing + " [validate_error: no snippet generated]",
        }

    result = ast_validate(snippet.code)
    if result.ok:
        return {"snippet_valid": True}

    existing = state.get("search_notes") or ""
    return {
        "snippet_valid": False,
        "search_notes": existing + f" [validate_error: {result.error}]",
    }


def route_after_validate(state: InterpretState) -> str:
    """Conditional edge: regenerate on failure (under MAX_ATTEMPTS), else judge.

    Routes on the dedicated snippet_valid flag and the monotonically increasing
    snippet_attempts counter, so the loop always terminates: a valid snippet goes
    straight to judge, and after MAX_ATTEMPTS we give up regenerating and let judge
    score the best-effort (or absent) snippet.
    """
    if state.get("snippet_valid"):
        return "judge"
    if state.get("snippet_attempts", 0) >= MAX_ATTEMPTS:
        return "judge"
    return "gen_snippet"


# ---------------------------------------------------------------------------
# judge node + edge function
# ---------------------------------------------------------------------------


def judge(state: InterpretState) -> dict:
    """Score the interpretation against the original card text.

    Reads: state["card_draft"], state["interpretation"], state["program"], state["snippet"]
    Writes: state["verdict"] (Verdict)

    Uses ChatOpenAI.with_structured_output(Verdict) for a typed multi-dimensional verdict.
    """
    draft = state["card_draft"]
    interp = state.get("interpretation")
    program = state.get("program")
    snippet = state.get("snippet")

    if program is not None:
        effect_summary = f"EffectProgram: {program}"
    elif snippet is not None:
        effect_summary = f"SnippetEffect:\n  code: {snippet.code}\n  explanation: {snippet.explanation}"
    else:
        effect_summary = "No effect produced."

    human_content = (
        f"Original card:\n"
        f"  Title: {draft['title']}\n"
        f"  Description: {draft['description']}\n\n"
        f"Interpretation:\n"
        f"  {interp.model_dump_json() if interp else 'none'}\n\n"
        f"Generated effect:\n"
        f"  {effect_summary}\n\n"
        "Score each dimension: intent, timing, target, trigger, magnitude. Set ok=True only if ALL are True."
    )

    llm = get_chat_model(temperature=0).with_structured_output(Verdict)
    verdict = llm.invoke(
        [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "human", "content": human_content},
        ]
    )
    return {"verdict": verdict}


def route_after_judge(state: InterpretState) -> str:
    """Conditional edge: END if verdict.ok or attempts >= MAX_ATTEMPTS; else 'classify'."""
    from langgraph.graph import END

    verdict = state.get("verdict")
    attempts = state.get("attempts", 0)
    if (verdict is not None and verdict.ok) or attempts >= MAX_ATTEMPTS:
        return END
    return "classify"
