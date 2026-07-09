"""Tests for the eval judge (schema + mocked evaluate)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tbwc.evals.judge import JudgeLLM, Verdict


def _verdict() -> Verdict:
    return Verdict(
        intent_match=0.9,
        timing_correct=1.0,
        target_placement_correct=1.0,
        trigger_event_correct=1.0,
        magnitude_sign_correct=1.0,
        overall=0.95,
        reason="Correct immediate point gain.",
    )


def test_verdict_schema_valid() -> None:
    assert _verdict().overall == 0.95


def test_verdict_score_range_rejected() -> None:
    with pytest.raises(Exception):
        Verdict(
            intent_match=1.5,
            timing_correct=1.0,
            target_placement_correct=1.0,
            trigger_event_correct=1.0,
            magnitude_sign_correct=1.0,
            overall=1.0,
            reason="Bad.",
        )


def test_verdict_has_all_seven_fields() -> None:
    fields = set(Verdict.model_fields.keys())
    assert fields == {
        "intent_match",
        "timing_correct",
        "target_placement_correct",
        "trigger_event_correct",
        "magnitude_sign_correct",
        "overall",
        "reason",
    }


def test_judge_evaluate_calls_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    expected = _verdict()
    with patch("tbwc.evals.judge.ChatOpenAI") as MockLLM:
        structured = MagicMock()
        structured.invoke.return_value = expected
        MockLLM.return_value.with_structured_output.return_value = structured
        judge = JudgeLLM()
        out = judge.evaluate(
            card_description="Gain 5 points.",
            generated_summary="add_points(5, self)",
            human_canonical={"timing": "immediate", "target": "self"},
        )
        assert out is expected
        MockLLM.return_value.with_structured_output.assert_called_once_with(Verdict)


def test_judge_evaluate_rejects_non_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with patch("tbwc.evals.judge.ChatOpenAI") as MockLLM:
        structured = MagicMock()
        structured.invoke.return_value = {"not": "a verdict"}
        MockLLM.return_value.with_structured_output.return_value = structured
        judge = JudgeLLM()
        with pytest.raises(ValueError):
            judge.evaluate(card_description="x", generated_summary="y", human_canonical={})
