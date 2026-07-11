"""Tests for agent.prompts."""

from __future__ import annotations

from agent.prompts import CLASSIFY_TEMPLATE, INTERPRETER_SYSTEM, JUDGE_SYSTEM


def test_prompts_are_strings() -> None:
    assert isinstance(INTERPRETER_SYSTEM, str)
    assert isinstance(JUDGE_SYSTEM, str)
    assert isinstance(CLASSIFY_TEMPLATE, str)
    assert INTERPRETER_SYSTEM
    assert JUDGE_SYSTEM


def test_classify_template_substitution() -> None:
    filled = CLASSIFY_TEMPLATE.format(
        title="Test Card", description="Do a thing.", exemplars="none", search_notes="none"
    )
    assert "Test Card" in filled
    assert "Do a thing" in filled


def test_interpreter_is_literalist() -> None:
    assert "literalist" in INTERPRETER_SYSTEM.lower()
