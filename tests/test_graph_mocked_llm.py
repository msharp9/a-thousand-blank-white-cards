"""End-to-end integration tests for the interpretation graph using a mocked ChatOpenAI.

Strategy: patch get_chat_model to return a fake LLM whose .invoke() and
.with_structured_output(schema).invoke() return deterministic Pydantic objects,
then invoke the compiled graph and assert the final state is well-formed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.schemas import Interpretation, SnippetEffect, Verdict
from models.effects import AddPointsOp, EffectProgram
from engine.sandbox.validate import validate_snippet

VALID_SNIPPET_CODE = (
    "def apply(state, ctx):\n    pid = ctx['player_id']\n    state.scores[pid] = state.scores.get(pid, 0) + 5\n"
)


def _immediate_interpretation() -> Interpretation:
    return Interpretation(placement="self", timing="immediate", mode="immediate", rationale="Gain points.")


def _snippet_interpretation() -> Interpretation:
    return Interpretation(
        placement="center", timing="modifier", trigger_event="on_play", mode="snippet", rationale="Complex hook."
    )


def _verdict(ok: bool = True) -> Verdict:
    return Verdict(
        intent=ok,
        timing=ok,
        target=ok,
        trigger=ok,
        magnitude=ok,
        ok=ok,
        reason="All checks passed" if ok else "Failed intent",
    )


def _make_fake_llm(interpretation, effect_program, verdict, snippet_code=VALID_SNIPPET_CODE):
    def _structured(schema):
        inner = MagicMock()
        if schema is Interpretation:
            inner.invoke.return_value = interpretation
        elif schema is SnippetEffect:
            inner.invoke.return_value = SnippetEffect(code=snippet_code, explanation="Add 5 points.")
        elif schema is Verdict:
            inner.invoke.return_value = verdict
        else:  # EffectProgram
            inner.invoke.return_value = effect_program
        return inner

    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content="The card grants extra points.")
    fake_llm.with_structured_output.side_effect = _structured
    return fake_llm


@pytest.fixture(autouse=True)
def _patch_rag():
    with patch("agent.nodes._retriever", MagicMock(return_value=[])):
        yield


def test_graph_immediate_mode_produces_effect_program(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    program = EffectProgram(ops=[AddPointsOp(amount=3)])
    fake_llm = _make_fake_llm(_immediate_interpretation(), program, _verdict(True))
    with patch("agent.nodes.get_chat_model", return_value=fake_llm):
        from agent.graph import graph

        final = graph.invoke({"card_draft": {"title": "Gain 3 Points", "description": "Gain 3 points."}, "attempts": 0})
    assert final["verdict"].ok is True
    assert final["interpretation"].mode == "immediate"
    assert final.get("program") is not None


def test_graph_snippet_mode_produces_valid_snippet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_llm = _make_fake_llm(_snippet_interpretation(), None, _verdict(True))
    with patch("agent.nodes.get_chat_model", return_value=fake_llm):
        from agent.graph import graph

        final = graph.invoke(
            {"card_draft": {"title": "Wild Effect", "description": "Something unusual."}, "attempts": 0}
        )
    assert final["verdict"].ok is True
    snippet = final["snippet"]
    assert isinstance(snippet, SnippetEffect)
    assert "def apply" in snippet.code
    assert validate_snippet(snippet.code).ok


def test_graph_judge_retry_on_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    program = EffectProgram(ops=[AddPointsOp(amount=3)])
    verdict_iter = iter([_verdict(False), _verdict(True)])

    def _structured(schema):
        inner = MagicMock()
        if schema is Interpretation:
            inner.invoke.return_value = _immediate_interpretation()
        elif schema is Verdict:
            inner.invoke.return_value = next(verdict_iter, _verdict(True))
        else:
            inner.invoke.return_value = program
        return inner

    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content="intent summary")
    fake_llm.with_structured_output.side_effect = _structured
    with patch("agent.nodes.get_chat_model", return_value=fake_llm):
        from agent.graph import graph

        final = graph.invoke({"card_draft": {"title": "T", "description": "D"}, "attempts": 0})
    assert final.get("attempts", 0) >= 2
    assert final["verdict"].ok is True
