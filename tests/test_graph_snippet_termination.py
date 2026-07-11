"""Regression test: the gen_snippet<->validate_snippet loop must terminate.

Drives the compiled graph with a mocked LLM where classify picks snippet mode and
gen_snippet ALWAYS returns code that fails AST validation (contains an import). The
loop must give up regenerating at MAX_ATTEMPTS and reach judge -> END rather than
cycling until LangGraph raises GraphRecursionError.

This test FAILS against the old code (which routed on a lingering search_notes
substring and never advanced attempts inside the sub-loop) and PASSES against the fix.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.nodes import MAX_ATTEMPTS
from agent.schemas import Interpretation, SnippetEffect, Verdict

# Always-invalid snippet: the leading import fails the AST allowlist every time.
INVALID_SNIPPET_CODE = "import os\ndef apply(state, ctx): pass"


def _snippet_interpretation() -> Interpretation:
    return Interpretation(
        placement="center", timing="modifier", trigger_event="on_play", mode="snippet", rationale="Complex hook."
    )


def _verdict_ok() -> Verdict:
    return Verdict(intent=True, timing=True, target=True, trigger=True, magnitude=True, ok=True, reason="ok")


def _make_fake_llm():
    def _structured(schema):
        inner = MagicMock()
        if schema is Interpretation:
            inner.invoke.return_value = _snippet_interpretation()
        elif schema is SnippetEffect:
            inner.invoke.return_value = SnippetEffect(code=INVALID_SNIPPET_CODE, explanation="Invalid on purpose.")
        elif schema is Verdict:
            inner.invoke.return_value = _verdict_ok()
        else:  # EffectProgram — unused in snippet mode
            inner.invoke.return_value = MagicMock()
        return inner

    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content="An unusual effect.")
    fake_llm.with_structured_output.side_effect = _structured
    return fake_llm


@pytest.fixture(autouse=True)
def _patch_rag():
    with patch("agent.nodes._retriever", MagicMock(return_value=[])):
        yield


def test_snippet_loop_terminates_on_persistent_invalid_snippet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_llm = _make_fake_llm()
    with patch("agent.nodes.get_chat_model", return_value=fake_llm):
        from agent.graph import graph

        # Must RETURN (reach END) instead of raising GraphRecursionError.
        final = graph.invoke(
            {"card_draft": {"title": "Wild Effect", "description": "Something unusual."}, "attempts": 0}
        )

    # The regenerate sub-loop gave up at MAX_ATTEMPTS and the graph terminated.
    assert final.get("snippet_attempts") == MAX_ATTEMPTS
    assert final.get("snippet_valid") is False
    assert final["verdict"].ok is True
