"""Tests for the judge node and route_after_judge edge."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langgraph.graph import END

from tbwc.agent.schemas import Interpretation, Verdict


def _verdict(ok: bool = True) -> Verdict:
    return Verdict(intent=ok, timing=ok, target=ok, trigger=ok, magnitude=ok, ok=ok, reason="test")


def test_judge_sets_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_verdict = _verdict(True)
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = fake_verdict
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import judge

        interp = Interpretation(placement="self", timing="immediate", mode="immediate", rationale="t")
        state = {
            "card_draft": {"title": "T", "description": "D"},
            "interpretation": interp,
            "program": None,
            "snippet": None,
        }
        result = judge(state)
        assert result["verdict"] == fake_verdict
        fake_llm.with_structured_output.assert_called_once_with(Verdict)


def test_route_after_judge_ok_ends() -> None:
    from tbwc.agent.nodes import route_after_judge

    assert route_after_judge({"verdict": _verdict(True), "attempts": 1}) == END


def test_route_after_judge_fail_retries() -> None:
    from tbwc.agent.nodes import route_after_judge

    assert route_after_judge({"verdict": _verdict(False), "attempts": 1}) == "classify"


def test_route_after_judge_max_attempts_ends() -> None:
    from tbwc.agent.nodes import route_after_judge

    assert route_after_judge({"verdict": _verdict(False), "attempts": 3}) == END
