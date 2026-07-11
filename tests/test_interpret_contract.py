"""Tests for the canonical interpretation contract (agent.contract).

Verify the :class:`agent.contract.InterpretResult` model — its defaults, its
validation/round-trip behaviour, and that it can carry a
:class:`agent.contract.SnippetEffect`. The runtime entry point (``run_agent``)
result shape is covered separately in tests/test_agent_skeleton.py.
"""

from __future__ import annotations

from agent.contract import InterpretResult, SnippetEffect
from models.effects import CustomNoteOp, EffectProgram


def test_interpret_result_defaults():
    r = InterpretResult()
    assert r.program is None
    assert r.snippet is None
    assert r.verdict == "invalid"
    assert r.comment == ""
    assert r.persona_action == "none"


def test_snippet_effect_import_and_fields():
    s = SnippetEffect(code="def apply(state, ctx): return None", explanation="noop")
    assert s.code.startswith("def apply")
    assert s.explanation == "noop"


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
