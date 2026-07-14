from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from models.effects import InteractionStep, ResolutionPlan
from models.interactions import ChoiceResponse, DrawingResponse, InteractionDescriptor
from models.ws_messages import ClientMsg, InteractionResponseMsg


def test_all_descriptor_kinds_validate_through_discriminated_union() -> None:
    adapter = TypeAdapter(InteractionDescriptor)
    for kind, extra in (
        ("choice", {"options": [{"id": "a", "label": "A"}]}),
        ("number", {"minimum": 0, "maximum": 10}),
        ("text", {"max_length": 50}),
        ("card_pick", {"card_ids": ["c1"]}),
        ("confirm", {}),
        ("drawing", {"max_strokes": 4}),
    ):
        descriptor = adapter.validate_python({"kind": kind, "prompt": "Respond", **extra})
        assert descriptor.kind == kind


def test_response_envelope_is_versioned_and_strict() -> None:
    message = TypeAdapter(ClientMsg).validate_python(
        {
            "type": "interaction_response",
            "schema_version": 1,
            "interaction_id": "auction-1",
            "payload": {"kind": "number", "value": 7},
        }
    )
    assert isinstance(message, InteractionResponseMsg)
    with pytest.raises(ValidationError):
        InteractionResponseMsg.model_validate(
            {
                "interaction_id": "auction-1",
                "payload": {"kind": "number", "value": 7, "extra": "no"},
            }
        )


def test_drawing_payload_is_normalized_and_bounded() -> None:
    DrawingResponse.model_validate(
        {
            "strokes": [
                {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]},
            ]
        }
    )
    with pytest.raises(ValidationError):
        DrawingResponse.model_validate({"strokes": [{"points": [{"x": 2, "y": 0}]}]})
    with pytest.raises(ValidationError):
        DrawingResponse.model_validate({"strokes": [{"points": [{"x": 0.5, "y": 0.5}]} for _ in range(65)]})


def test_dense_frontend_wire_budget_survives_float_reserialization() -> None:
    """48 KiB of compact JS-shaped edge coordinates stays below the typed 65 KiB cap."""
    strokes: list[dict] = []
    payload = {"kind": "drawing", "strokes": strokes}
    for _ in range(64):
        stroke = {"color": "#1a1a1a", "width": 0.01, "points": []}
        strokes.append(stroke)
        for index in range(256):
            stroke["points"].append({"x": index % 2, "y": (index + 1) % 2})
            encoded = json.dumps(payload, separators=(",", ":"))
            if len(encoded.encode()) > 48 * 1024:
                stroke["points"].pop()
                encoded = json.dumps(payload, separators=(",", ":"))
                break
        if len(encoded.encode()) >= 47 * 1024:
            break
    assert len(encoded.encode()) >= 47 * 1024
    DrawingResponse.model_validate_json(encoded)


def test_resolution_plan_requires_unique_ordered_interaction_refs() -> None:
    with pytest.raises(ValidationError, match="prior results"):
        ResolutionPlan.model_validate(
            {
                "steps": [
                    {
                        "kind": "interaction",
                        "result_key": "vote",
                        "request": {"kind": "choice", "prompt": "Vote"},
                        "input_refs": {"options": {"result_key": "drawings"}},
                    }
                ]
            }
        )
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "drawings",
                    "request": {"kind": "drawing", "prompt": "Draw"},
                },
                {
                    "kind": "interaction",
                    "result_key": "vote",
                    "request": {"kind": "choice", "prompt": "Vote"},
                    "input_refs": {"options": {"result_key": "drawings"}},
                },
            ]
        }
    )
    assert isinstance(plan.steps[0], InteractionStep)


def test_invalid_audience_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(InteractionDescriptor).validate_python(
            {"kind": "text", "prompt": "Tell me", "audience": "spectators"}
        )


def test_choice_option_and_response_ids_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="unique"):
        TypeAdapter(InteractionDescriptor).validate_python(
            {
                "kind": "choice",
                "prompt": "Pick",
                "options": [{"id": "same", "label": "A"}, {"id": "same", "label": "B"}],
            }
        )
    with pytest.raises(ValidationError, match="unique"):
        ChoiceResponse(option_ids=["same", "same"])


def test_resolution_plan_caps_interaction_barriers() -> None:
    with pytest.raises(ValidationError, match="interaction barriers"):
        ResolutionPlan.model_validate(
            {
                "steps": [
                    {
                        "kind": "interaction",
                        "result_key": f"answer-{index}",
                        "request": {"kind": "confirm", "prompt": "Continue?"},
                    }
                    for index in range(5)
                ]
            }
        )


def test_resolution_and_timeout_boundaries_are_enforced() -> None:
    ResolutionPlan.model_validate({"steps": [{"kind": "ops", "ops": []} for _ in range(8)]})
    with pytest.raises(ValidationError):
        ResolutionPlan.model_validate({"steps": [{"kind": "ops", "ops": []} for _ in range(9)]})
    TypeAdapter(InteractionDescriptor).validate_python(
        {"kind": "confirm", "prompt": "Continue?", "timeout_seconds": 10}
    )
    with pytest.raises(ValidationError):
        TypeAdapter(InteractionDescriptor).validate_python(
            {"kind": "confirm", "prompt": "Continue?", "timeout_seconds": 9}
        )


def test_static_candidate_interactions_cannot_fail_open() -> None:
    for request in (
        {"kind": "choice", "prompt": "Pick"},
        {"kind": "card_pick", "prompt": "Pick a card"},
    ):
        with pytest.raises(ValidationError, match="requires"):
            ResolutionPlan.model_validate(
                {"steps": [{"kind": "interaction", "result_key": "answer", "request": request}]}
            )


def test_from_hand_card_pick_needs_no_static_card_ids() -> None:
    """A from_hand pick fills each player's own hand at send time, so an empty
    static card_ids is valid (unlike a plain card_pick, which fails open)."""
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "discards",
                    "request": {"kind": "card_pick", "prompt": "Discard a card", "audience": "all", "from_hand": True},
                }
            ]
        }
    )
    assert isinstance(plan.steps[0], InteractionStep)
    assert plan.steps[0].request.from_hand is True


def test_numeric_protocol_rejects_non_finite_values() -> None:
    adapter = TypeAdapter(InteractionDescriptor)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "number", "prompt": "Bid", "maximum": float("inf")})
    with pytest.raises(ValidationError):
        InteractionResponseMsg.model_validate(
            {
                "interaction_id": "bid",
                "payload": {"kind": "number", "value": float("nan")},
            }
        )


def test_resolution_plan_rejects_oversized_snippet() -> None:
    with pytest.raises(ValidationError):
        ResolutionPlan.model_validate({"steps": [{"kind": "snippet", "code": "x" * 65_537}]})
