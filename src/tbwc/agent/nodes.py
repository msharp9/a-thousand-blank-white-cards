"""tbwc.agent.nodes — LangGraph interpretation nodes.

Each node is a pure-ish function (InterpretState) -> dict (partial state update).
Later beads append more nodes (retrieve, classify, emit_ops, judge, …) to this file.
"""

from __future__ import annotations

import re

from tbwc.agent.llm import get_chat_model
from tbwc.agent.prompts import CLASSIFY_TEMPLATE, INTERPRETER_SYSTEM
from tbwc.agent.schemas import Interpretation, SnippetEffect
from tbwc.agent.state import InterpretState
from tbwc.models.effects import EffectProgram
from tbwc.rag.retrievers import dense_retriever
from tbwc.sandbox.validate import validate_snippet as ast_validate


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


def retrieve(state: InterpretState) -> dict:
    """Search the RAG store for exemplar cards similar to the card being interpreted.

    Reads: state["card_draft"], state["search_notes"]
    Writes: state["retrieved"] (list of exemplar dicts from the RAG search)

    Uses search_notes (intent summary) as the query if available, else falls back
    to "title\\ndescription".
    """
    draft = state["card_draft"]
    query = state.get("search_notes") or f"{draft['title']}\n{draft['description']}"
    exemplars = _retriever(query, k=4)
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
# search node  (STUB — Tavily integration wired in a later phase)
# ---------------------------------------------------------------------------


def search(state: InterpretState) -> dict:
    """Perform a web search for additional context about the card.

    STUB: returns a no-op note so the graph proceeds to 'classify'. A later phase
    replaces this body with a Tavily API call.

    Reads: state["card_draft"], state["search_notes"]
    Writes: state["search_notes"] — appends a stub notice.
    """
    # TODO(phase4): call the Tavily search API here
    existing = state.get("search_notes") or ""
    stub_note = " [web_search_results: none — search stub not yet implemented]"
    return {"search_notes": existing + stub_note}


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


def emit_ops(state: InterpretState) -> dict:
    """Generate an EffectProgram (immediate ops) for immediate-mode cards.

    Reads: state["card_draft"], state["interpretation"]
    Writes: state["program"] (EffectProgram)

    Uses ChatOpenAI.with_structured_output(EffectProgram) so the output is always
    a valid, typed EffectProgram matching the engine schema.
    """
    draft = state["card_draft"]
    interp = state["interpretation"]

    human_content = (
        f"Card title: {draft['title']}\n"
        f"Card description: {draft['description']}\n\n"
        f"Classification: {interp.model_dump_json()}\n\n"
        "Generate an EffectProgram: a list of immediate ops that faithfully "
        "implements this card's effect. Translate exactly — do not balance or modify."
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
    Writes: state["snippet"] (SnippetEffect)
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
    return {"snippet": snippet}


# ---------------------------------------------------------------------------
# validate_snippet node + edge function
# ---------------------------------------------------------------------------

MAX_ATTEMPTS = 3  # shared with the judge loop


def validate_snippet_node(state: InterpretState) -> dict:
    """Run the AST allowlist check on the generated snippet.

    Reads: state["snippet"]
    Writes: state["search_notes"] — appends a validation error on failure so
            gen_snippet can see why its code was rejected when it retries.

    On success: passes through (returns {}). On failure: appends the error.
    """
    snippet = state.get("snippet")
    if snippet is None:
        existing = state.get("search_notes") or ""
        return {"search_notes": existing + " [validate_error: no snippet generated]"}

    result = ast_validate(snippet.code)
    if result.ok:
        return {}

    existing = state.get("search_notes") or ""
    return {"search_notes": existing + f" [validate_error: {result.error}]"}


def route_after_validate(state: InterpretState) -> str:
    """Conditional edge: retry gen_snippet on validation failure (under MAX_ATTEMPTS), else judge."""
    notes = state.get("search_notes") or ""
    attempts = state.get("attempts", 0)
    if "[validate_error:" in notes and attempts < MAX_ATTEMPTS:
        return "gen_snippet"
    return "judge"
