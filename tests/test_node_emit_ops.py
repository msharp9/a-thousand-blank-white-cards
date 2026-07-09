"""Tests for the emit_ops node."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tbwc.agent.schemas import Interpretation


def test_emit_ops_sets_program(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_program = MagicMock()
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = fake_program
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import emit_ops
        from tbwc.models.effects import EffectProgram

        interp = Interpretation(placement="self", timing="immediate", mode="immediate", rationale="test")
        state = {
            "card_draft": {"title": "Gain 5 Points", "description": "Gain 5 points."},
            "interpretation": interp,
        }
        result = emit_ops(state)
        assert result["program"] is fake_program
        fake_llm.with_structured_output.assert_called_once_with(EffectProgram)


def test_emit_ops_includes_classification_in_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = MagicMock()
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import emit_ops

        interp = Interpretation(placement="self", timing="immediate", mode="immediate", rationale="rx")
        emit_ops({"card_draft": {"title": "Zap", "description": "Lose 5."}, "interpretation": interp})
        human = fake_llm.with_structured_output.return_value.invoke.call_args.args[0][1]["content"]
        assert "Zap" in human
        assert "Classification:" in human


def test_emit_ops_injects_retrieved_exemplars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = MagicMock()
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import emit_ops

        interp = Interpretation(placement="self", timing="immediate", mode="immediate", rationale="r")
        state = {
            "card_draft": {"title": "Zap", "description": "Lose 5."},
            "interpretation": interp,
            "retrieved": [{"title": "Lose 3", "description": "Lose 3 pts", "canonical": '{"ops":[]}', "score": 0.9}],
        }
        emit_ops(state)
        human = fake_llm.with_structured_output.return_value.invoke.call_args.args[0][1]["content"]
        assert "Lose 3" in human
        assert "example" in human.lower()


def test_emit_ops_fewshot_disabled_via_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = MagicMock()
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import emit_ops

        interp = Interpretation(placement="self", timing="immediate", mode="immediate", rationale="r")
        state = {
            "card_draft": {"title": "Zap", "description": "Lose 5."},
            "interpretation": interp,
            "retrieved": [{"title": "SHOULD_NOT_APPEAR", "description": "x", "canonical": "{}", "score": 0.9}],
        }
        emit_ops(state, {"configurable": {"few_shot_exemplars": False}})
        human = fake_llm.with_structured_output.return_value.invoke.call_args.args[0][1]["content"]
        assert "SHOULD_NOT_APPEAR" not in human


def test_emit_ops_normalizes_authoring_target_to_chooser(monkeypatch: pytest.MonkeyPatch) -> None:
    """A program whose LLM leaked target='player' is normalized to 'chooser'."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from tbwc.models.effects import AddPointsOp, EffectProgram

    # Simulate LLM structured-output leaking authoring vocab "player" into a runtime
    # op. Built via model_construct to bypass strict Literal validation (mirrors a
    # provider whose json_schema decoding did not enforce the enum).
    leaked_op = AddPointsOp.model_construct(op="add_points", target="player", amount=5)
    leaked = EffectProgram.model_construct(ops=[leaked_op], requires_choice=False)
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = leaked
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import emit_ops

        interp = Interpretation(placement="player", timing="immediate", mode="immediate", rationale="r")
        state = {"card_draft": {"title": "Zap", "description": "A player loses 5."}, "interpretation": interp}
        program = emit_ops(state)["program"]

    assert program.ops[0].target == "chooser"
    assert program.requires_choice is True


def test_emit_ops_normalizes_steal_from_and_to_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    """A steal op's from_target/to_target are both normalized."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from tbwc.models.effects import EffectProgram, StealPointsOp

    leaked_op = StealPointsOp.model_construct(op="steal_points", from_target="opponent", to_target="self", amount=3)
    leaked = EffectProgram.model_construct(ops=[leaked_op], requires_choice=False)
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = leaked
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import emit_ops

        interp = Interpretation(placement="player", timing="immediate", mode="immediate", rationale="r")
        state = {"card_draft": {"title": "Rob", "description": "Steal 3 from a player."}, "interpretation": interp}
        program = emit_ops(state)["program"]

    assert program.ops[0].from_target == "chooser"
    assert program.ops[0].to_target == "self"
    assert program.requires_choice is True


def test_emit_ops_sets_requires_choice_for_chosen_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """A destroy op with card_target='chosen_card' flips requires_choice."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from tbwc.models.effects import DestroyCardOp, EffectProgram

    op = DestroyCardOp(card_target="chosen_card")
    leaked = EffectProgram(ops=[op], requires_choice=False)
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = leaked
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import emit_ops

        interp = Interpretation(placement="self", timing="immediate", mode="immediate", rationale="r")
        state = {"card_draft": {"title": "Nuke", "description": "Destroy a card you pick."}, "interpretation": interp}
        program = emit_ops(state)["program"]

    assert program.ops[0].card_target == "chosen_card"
    assert program.requires_choice is True


def test_emit_ops_no_choice_for_all_in_play_card_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-choice CardTarget ('all_in_play') does NOT flip requires_choice."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from tbwc.models.effects import DestroyCardOp, EffectProgram

    op = DestroyCardOp(card_target="all_in_play")
    leaked = EffectProgram(ops=[op], requires_choice=False)
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = leaked
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import emit_ops

        interp = Interpretation(placement="self", timing="immediate", mode="immediate", rationale="r")
        state = {"card_draft": {"title": "Wipe", "description": "Destroy all in-play cards."}, "interpretation": interp}
        program = emit_ops(state)["program"]

    assert program.requires_choice is False


def test_emit_ops_leaves_targetless_ops_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ops without target fields (reverse_order) survive normalization unchanged."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from tbwc.models.effects import EffectProgram

    leaked = EffectProgram.model_validate({"ops": [{"op": "reverse_order"}, {"op": "add_points", "amount": 1}]})
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = leaked
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import emit_ops

        interp = Interpretation(placement="self", timing="immediate", mode="immediate", rationale="r")
        state = {"card_draft": {"title": "Flip", "description": "Reverse order."}, "interpretation": interp}
        program = emit_ops(state)["program"]

    assert program.ops[0].op == "reverse_order"
    assert program.ops[1].target == "self"
    assert program.requires_choice is False
