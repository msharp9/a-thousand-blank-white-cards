"""Tests for the C0 interpret contract + backward-compatible shim.

Verify the NEW canonical result shape and signature of ``agent.graph.interpret_card``
and the :class:`agent.contract.InterpretResult` model, without running any real LLM
(the underlying ``graph.invoke`` is mocked).
"""

from __future__ import annotations

from unittest.mock import patch

from agent.contract import InterpretResult
from agent.graph import interpret_card
from agent.schemas import SnippetEffect
from models.effects import CustomNoteOp, EffectProgram

_CONTRACT_KEYS = {"program", "snippet", "verdict", "comment", "persona_action"}


def _ok_final():
    """A fake graph.invoke() result whose verdict passes."""

    class _V:
        ok = True

    program = EffectProgram(ops=[CustomNoteOp(note="hi")])
    return {"program": program, "snippet": None, "verdict": _V()}


def test_two_positional_args_returns_full_contract():
    with patch("agent.graph.graph.invoke", return_value=_ok_final()):
        result = interpret_card("Title", "Description")

    assert isinstance(result, dict)
    assert _CONTRACT_KEYS <= set(result)
    assert result["comment"] == ""
    assert result["persona_action"] == "none"
    assert result["verdict"] == "ok"


def test_new_optional_params_accepted_and_ignored():
    with patch("agent.graph.graph.invoke", return_value=_ok_final()) as invoke:
        result = interpret_card("Title", "Description", state={"phase": "play"}, actor_id="p1")

    assert _CONTRACT_KEYS <= set(result)
    # state/actor_id are ignored: the graph is invoked with only the card draft.
    (call_arg,), _ = invoke.call_args
    assert call_arg == {"card_draft": {"title": "Title", "description": "Description"}, "attempts": 0}


def test_legacy_keys_preserved_for_back_compat():
    with patch("agent.graph.graph.invoke", return_value=_ok_final()):
        result = interpret_card("t", "d")

    # Old callers read only these three keys and must keep working.
    assert set(result) >= {"program", "snippet", "verdict"}
    assert isinstance(result["program"], EffectProgram)
    assert result["snippet"] is None


def test_invalid_verdict_still_full_contract():
    class _V:
        ok = False

    final = {"program": None, "snippet": None, "verdict": _V()}
    with patch("agent.graph.graph.invoke", return_value=final):
        result = interpret_card("t", "d")

    assert result["verdict"] == "invalid"
    assert result["comment"] == ""
    assert result["persona_action"] == "none"


def test_interpret_result_defaults():
    r = InterpretResult()
    assert r.program is None
    assert r.snippet is None
    assert r.verdict == "invalid"
    assert r.comment == ""
    assert r.persona_action == "none"


def test_interpret_result_validate_and_round_trip():
    program = EffectProgram(ops=[CustomNoteOp(note="note")])
    snippet = SnippetEffect(code="def apply(state, ctx): return None", explanation="noop")
    r = InterpretResult(
        program=program,
        snippet=snippet,
        verdict="ok",
        comment="funny",
        persona_action="chaos_monkey",
    )
    dumped = r.model_dump()
    assert dumped["verdict"] == "ok"
    assert dumped["comment"] == "funny"
    assert dumped["persona_action"] == "chaos_monkey"

    restored = InterpretResult.model_validate(dumped)
    assert restored.verdict == "ok"
    assert restored.comment == "funny"
    assert restored.persona_action == "chaos_monkey"
    assert restored.program is not None
    assert restored.snippet is not None
    assert restored.snippet.code == snippet.code
