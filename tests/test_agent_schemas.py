"""Tests for tbwc.agent.schemas."""

from __future__ import annotations

from tbwc.agent.schemas import Interpretation, SnippetEffect, Verdict


def test_interpretation_defaults() -> None:
    interp = Interpretation(
        placement="self", timing="immediate", mode="immediate", rationale="Simple immediate effect."
    )
    assert interp.trigger_event is None
    assert interp.properties == {}


def test_interpretation_full() -> None:
    interp = Interpretation(
        placement="center",
        timing="modifier",
        trigger_event="on_draw",
        properties={"uncounterable": True},
        mode="snippet",
        rationale="Stays in play, fires on draw.",
    )
    assert interp.trigger_event == "on_draw"
    assert interp.properties["uncounterable"] is True


def test_verdict_all_fields() -> None:
    v = Verdict(
        intent=True, timing=True, target=True, trigger=True, magnitude=True, ok=True, reason="All checks passed"
    )
    assert v.ok is True


def test_snippet_effect_has_code() -> None:
    s = SnippetEffect(code="pass", explanation="does nothing")
    assert s.code == "pass"
