"""Unit tests for scripts/data_prep/migrate_card_schema.py (v1 → v2 dataset migration)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from engine.sandbox.validate import validate_snippet

_SPEC = importlib.util.spec_from_file_location(
    "migrate_card_schema", Path(__file__).parent.parent / "scripts" / "data_prep" / "migrate_card_schema.py"
)
migrate = importlib.util.module_from_spec(_SPEC)
sys.modules["migrate_card_schema"] = migrate
_SPEC.loader.exec_module(migrate)


class TestSplitAltText:
    def test_splits_bracket_prefix(self) -> None:
        alt, rest = migrate.split_alt_text("[drawing of a cereal box labeled POPS] EAT A HANDFUL OF CEREAL (+500)")
        assert alt == "drawing of a cereal box labeled POPS"
        assert rest == "EAT A HANDFUL OF CEREAL (+500)"

    def test_no_prefix_untouched(self) -> None:
        alt, rest = migrate.split_alt_text("Gain 5 points.")
        assert alt is None
        assert rest == "Gain 5 points."

    def test_empty_brackets_yield_none(self) -> None:
        alt, rest = migrate.split_alt_text("[] rule text")
        assert alt is None
        assert rest == "rule text"

    def test_alt_only_description_leaves_empty_rule(self) -> None:
        alt, rest = migrate.split_alt_text("[drawing of a prune]")
        assert alt == "drawing of a prune"
        assert rest == ""


class TestOpsToSandbox:
    def test_simple_add_points(self) -> None:
        code = migrate.ops_to_sandbox([{"op": "add_points", "args": {"target": "self", "amount": 5}}])
        assert code == "def apply(state, ctx):\n    state.add_points('self', 5)"
        assert validate_snippet(code).ok

    def test_negative_add_points_becomes_subtract(self) -> None:
        code = migrate.ops_to_sandbox([{"op": "add_points", "args": {"target": "player", "amount": -1000}}])
        assert "state.subtract_points(chosen, 1000)" in code
        assert 'chosen = "id:" + (ctx.get("chosen_player_id") or "")' in code
        assert validate_snippet(code).ok

    def test_chooser_targets_read_ctx(self) -> None:
        code = migrate.ops_to_sandbox(
            [
                {"op": "steal_points", "args": {"from_target": "player", "to_target": "self", "amount": 3}},
                {"op": "reverse_order", "args": {}},
            ]
        )
        assert "state.steal_points(chosen, 'self', 3)" in code
        assert "state.reverse_order()" in code
        # "chooser" must never leak into sandbox code — snippet diffs reject it.
        assert '"chooser"' not in code

    def test_custom_note_and_skip(self) -> None:
        code = migrate.ops_to_sandbox(
            [
                {"op": "custom_note", "args": {"note": "Eat a handful of cereal."}},
                {"op": "skip_turn", "args": {"target": "next_player"}},
            ]
        )
        assert "state.note('Eat a handful of cereal.')" in code
        assert "state.skip_turn('right_neighbor')" in code

    def test_refuses_to_guess_unknown_op(self) -> None:
        assert migrate.ops_to_sandbox([{"op": "summon_dragon", "args": {}}]) is None

    def test_register_hook_embeds_inner_code(self) -> None:
        inner = "def apply(state, ctx):\n    state.add_points('self', 1)"
        code = migrate.ops_to_sandbox(
            [{"op": "register_hook", "args": {"event": "on_draw_step", "scope": "center", "code": inner}}]
        )
        assert "state.register_hook('on_draw_step', scope='center', code=" in code
        assert validate_snippet(code).ok

    def test_refuses_register_hook_without_code(self) -> None:
        assert migrate.ops_to_sandbox([{"op": "register_hook", "args": {"event": "on_play"}}]) is None

    def test_empty_ops_yield_none(self) -> None:
        assert migrate.ops_to_sandbox([]) is None


class TestMigrateCard:
    def _gold(self, **canonical) -> dict:
        return {
            "title": "Gain 5 Points",
            "description": "Gain 5 points.",
            "canonical": {
                "timing": "immediate",
                "target": "self",
                "placement": "self",
                "ops": [{"op": "add_points", "args": {"target": "self", "amount": 5}}],
                **canonical,
            },
        }

    def test_v1_gold_card_migrates_to_v2(self) -> None:
        card, needs = migrate.migrate_card(self._gold(), canonical_key="canonical", card_id="seed-gold-000")
        assert needs is None
        assert card["id"] == "seed-gold-000"
        assert card["alt_text"] is None
        canonical = card["canonical"]
        assert canonical["placement"] == "discard"
        assert "timing" not in canonical
        assert canonical["venue"] == "all"
        assert canonical["sandbox"].startswith("def apply(state, ctx):")

    def test_real_card_alt_text_split(self) -> None:
        card, _ = migrate.migrate_card(
            {
                "image_url": "https://i.imgur.com/x.jpeg",
                "title": "CEREAL KILLER",
                "description": "[drawing of a cereal box] EAT CEREAL (+500)",
                "human_canonical": {
                    "timing": "immediate",
                    "target": "player",
                    "placement": "discard",
                    "trigger_event": "on_play",
                    "venue": "in_person",
                    "magnitude_sign": "positive",
                    "ops": [{"op": "add_points", "args": {"target": "player", "amount": 500}}],
                },
            },
            canonical_key="human_canonical",
            card_id="real-000",
        )
        assert card["alt_text"] == "drawing of a cereal box"
        assert card["description"] == "EAT CEREAL (+500)"
        assert card["image_url"] == "https://i.imgur.com/x.jpeg"
        hc = card["human_canonical"]
        assert hc["trigger"] is None  # on_play on a one-shot is meaningless
        assert "trigger_event" not in hc
        assert hc["magnitude_sign"] == "positive"
        assert "chosen" in hc["sandbox"]

    def test_steps_cards_keep_steps_and_skip_sandbox(self) -> None:
        steps = [
            {"kind": "interaction", "result_key": "bids", "request": {"kind": "number", "prompt": "Bid!"}},
            {"kind": "snippet", "code": "def apply(state, ctx):\n    pass"},
        ]
        card, needs = migrate.migrate_card(
            self._gold(ops=None, steps=steps), canonical_key="canonical", card_id="seed-gold-001"
        )
        assert card["canonical"]["steps"] == steps
        assert card["canonical"]["sandbox"] is None
        assert needs is None  # interaction cards are exempt from the sandbox rule

    def test_prose_snippet_degrades_and_lands_on_worklist(self) -> None:
        card, needs = migrate.migrate_card(
            self._gold(ops=None, snippet="Any player who stands up loses 5 points."),
            canonical_key="canonical",
            card_id="seed-gold-002",
        )
        canonical = card["canonical"]
        assert canonical["ops"][-1]["op"] == "custom_note"
        assert needs is not None and "note-only" in needs

    def test_filler_without_canonical_flagged(self) -> None:
        card, needs = migrate.migrate_card(
            {"title": "Compliment Someone", "description": "Say something nice."},
            canonical_key="canonical",
            card_id="seed-filler-000",
        )
        assert "canonical" not in card
        assert needs is not None and "filler" in needs

    def test_idempotent_on_v2_output(self) -> None:
        first, _ = migrate.migrate_card(self._gold(), canonical_key="canonical", card_id="seed-gold-000")
        second, _ = migrate.migrate_card(first, canonical_key="canonical", card_id="seed-gold-000")
        assert first == second


def test_split_alt_text_merges_consecutive_bracket_groups() -> None:
    alt, rest = migrate.split_alt_text("[drawing of a guitar] [scribbled out text] CREATE A LYRIC")
    assert alt == "drawing of a guitar; scribbled out text"
    assert rest == "CREATE A LYRIC"
