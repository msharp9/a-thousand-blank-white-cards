#!/usr/bin/env python3
"""Migrate card datasets from canonical schema v1 to v2.

Schema v2 (data/eval/CANONICAL_SPEC.md): top-level ``id``/``alt_text``, unified
``trigger``, placement {discard, center, player}, no ``timing``, and executable
``sandbox`` code generated mechanically from ``ops``. Field-level v1→v2 mapping
is delegated to ``models.card.normalise_canonical`` — the same permanent shim
the runtime uses — so script and engine can never drift.

Idempotent: running on already-v2 data is a byte-level no-op (``--check``
verifies exactly that). Cards whose effect the mechanical pass cannot express
(ordered ``steps`` plans, note-only sandboxes, empty canonicals) are printed as
an authoring worklist for the hand/agent pass; nothing is guessed.

Usage:
    uv run python scripts/migrate_card_schema.py --all
    uv run python scripts/migrate_card_schema.py --file data/eval/real_cards.json
    uv run python scripts/migrate_card_schema.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from engine.sandbox.validate import validate_snippet  # noqa: E402
from models.card import normalise_canonical  # noqa: E402

# (path, canonical key, id prefix) for every dataset the migration owns.
DATASETS: tuple[tuple[Path, str, str], ...] = (
    (ROOT / "data" / "seed_cards_gold.json", "canonical", "seed-gold"),
    (ROOT / "data" / "seed_cards_fillers.json", "canonical", "seed-filler"),
    (ROOT / "data" / "seed_cards_simple.json", "canonical", "seed-simple"),
    (ROOT / "data" / "eval" / "eval_cards.json", "human_canonical", "eval"),
    (ROOT / "data" / "eval" / "eval_cards_hard.json", "human_canonical", "hard"),
    (ROOT / "data" / "eval" / "real_cards.json", "human_canonical", "real"),
)

# Leading "[alt text] rule text" prefix used by the photo transcriptions.
_ALT_PREFIX = re.compile(r"^\s*\[(?P<alt>[^\]]*)\]\s*")

# Authoring target → runtime Target string usable inside sandbox code.
# "chooser"-style targets are NOT legal in snippet diffs; they compile to an
# "id:" + ctx["chosen_player_id"] read instead (see _CHOSEN sentinel).
_CHOSEN = object()
_SANDBOX_TARGETS: dict[str, object] = {
    "self": "self",
    "all": "all",
    "all_players": "all",
    "everyone": "all",
    "others": "all_others",
    "all_others": "all_others",
    "player": _CHOSEN,
    "chosen_player": _CHOSEN,
    "opponent": _CHOSEN,
    "target_player": _CHOSEN,
    "chooser": _CHOSEN,
    "next_player": "right_neighbor",
    "left_neighbor": "left_neighbor",
    "right_neighbor": "right_neighbor",
    "player_with_most_points": "player_with_most_points",
    "player_with_least_points": "player_with_least_points",
    "player_with_empty_hand": "player_with_empty_hand",
}


def split_alt_text(description: str) -> tuple[str | None, str]:
    """Split leading ``[alt text]`` prefix(es) out of a transcription description.

    Consecutive bracket groups ("[drawing of a guitar] [scribbled out text]
    RULE…") all describe the art; they merge into one alt_text so no fragment
    is ever silently dropped.
    """
    parts: list[str] = []
    rest = description or ""
    while True:
        match = _ALT_PREFIX.match(rest)
        if not match:
            break
        fragment = match.group("alt").strip()
        if fragment:
            parts.append(fragment)
        rest = rest[match.end() :]
    return ("; ".join(parts) or None), rest


class _Unmappable(Exception):
    pass


def _target_expr(raw: object, *, uses_chosen: list[bool]) -> str:
    """Python source for a sandbox player-target argument."""
    target = str(raw or "self")
    if target.startswith(("id:", "has:")):
        return repr(target)
    mapped = _SANDBOX_TARGETS.get(target)
    if mapped is _CHOSEN:
        uses_chosen[0] = True
        return "chosen"
    if mapped is None:
        raise _Unmappable(f"target {target!r}")
    return repr(mapped)


def _amount(args: dict) -> int:
    value = args.get("amount", args.get("value"))
    if not isinstance(value, int) or isinstance(value, bool):
        raise _Unmappable(f"amount {value!r}")
    return value


def ops_to_sandbox(ops: list[dict]) -> str | None:
    """Mechanically compile authoring ops into equivalent sandbox code.

    Returns None — never guesses — when any op has no clean sandbox
    equivalent (register_hook / create_card carry their own nested code and
    teach better as ops; unknown ops go to the authoring worklist).
    """
    uses_chosen = [False]
    lines: list[str] = []
    try:
        for entry in ops or []:
            name = entry.get("op")
            args = entry.get("args")
            if not isinstance(args, dict):
                args = {k: v for k, v in entry.items() if k != "op"}
            if name in ("add_points", "subtract_points"):
                amount = _amount(args)
                target = _target_expr(args.get("target", "self"), uses_chosen=uses_chosen)
                method = "add_points" if (name == "add_points" and amount >= 0) else "subtract_points"
                lines.append(f"    state.{method}({target}, {abs(amount)})")
            elif name == "set_points":
                target = _target_expr(args.get("target", "self"), uses_chosen=uses_chosen)
                lines.append(f"    state.set_points({target}, {_amount(args)})")
            elif name in ("skip_turn", "extra_turn"):
                target = _target_expr(args.get("target", "self"), uses_chosen=uses_chosen)
                lines.append(f"    state.{name}({target})")
            elif name == "draw_cards":
                target = _target_expr(args.get("target", "self"), uses_chosen=uses_chosen)
                lines.append(f"    state.draw_cards({target}, {args.get('amount', 1)})")
            elif name in ("reverse_order", "scramble_order"):
                lines.append(f"    state.{name}()")
            elif name == "change_draw_count":
                lines.append(f"    state.change_draw_count({_amount(args)})")
            elif name == "steal_points":
                src = _target_expr(args.get("from_target", "chooser"), uses_chosen=uses_chosen)
                dst = _target_expr(args.get("to_target", "self"), uses_chosen=uses_chosen)
                lines.append(f"    state.steal_points({src}, {dst}, {_amount(args)})")
            elif name == "set_win_condition":
                kind = args.get("kind")
                if not isinstance(kind, str):
                    raise _Unmappable("set_win_condition without kind")
                threshold = args.get("threshold")
                extra = f", {threshold}" if threshold is not None else ""
                lines.append(f"    state.set_win_condition({kind!r}{extra})")
            elif name == "set_rule":
                lines.append(f"    state.set_rule({args.get('path')!r}, {args.get('value')!r})")
            elif name == "set_condition":
                target = _target_expr(args.get("target", "self"), uses_chosen=uses_chosen)
                lines.append(f"    state.set_condition({target}, {args.get('key')!r}, {args.get('value')!r})")
            elif name == "destroy_card":
                card_target = args.get("card_target") or args.get("target")
                if card_target in ("this", "all_in_play", "all_in_hand"):
                    lines.append(f"    state.destroy_card(card_target={card_target!r})")
                elif card_target in ("card", "chosen_card"):
                    lines.append('    state.destroy_card(card_id=ctx.get("chosen_card_id"))')
                elif card_target == "all_cards":
                    lines.append('    state.destroy_card(card_target="all_in_play")')
                else:
                    raise _Unmappable(f"destroy_card target {card_target!r}")
            elif name == "transfer_card":
                dst = _target_expr(args.get("to_target", "self"), uses_chosen=uses_chosen)
                lines.append(f"    state.transfer_card({args.get('card_target', 'this')!r}, {dst})")
            elif name == "counter_play":
                lines.append(f"    state.counter_play({args.get('mode', 'negate')!r})")
            elif name == "end_game":
                winner = args.get("winner")
                if winner is None:
                    lines.append("    state.end_game()")
                else:
                    lines.append(f"    state.end_game({_target_expr(winner, uses_chosen=uses_chosen)})")
            elif name == "custom_note":
                note = str(args.get("note") or args.get("text") or "")
                lines.append(f"    state.note({note!r})")
            elif name == "register_hook":
                event = args.get("event")
                code = args.get("code")
                if not isinstance(event, str) or not isinstance(code, str):
                    raise _Unmappable("register_hook without event/code")
                scope = args.get("scope", "center")
                lines.append(f"    state.register_hook({event!r}, scope={scope!r}, code={code!r})")
            else:
                raise _Unmappable(f"op {name!r}")
    except _Unmappable:
        return None
    if not lines:
        return None
    if uses_chosen[0]:
        lines.insert(0, '    chosen = "id:" + (ctx.get("chosen_player_id") or "")')
    code = "def apply(state, ctx):\n" + "\n".join(lines)
    result = validate_snippet(code)
    if not result.ok:
        raise RuntimeError(f"generated sandbox failed validation: {result.error}\n{code}")
    return code


def _is_note_only(sandbox: str | None) -> bool:
    if not sandbox:
        return False
    calls = [line.strip() for line in sandbox.splitlines()[1:] if line.strip()]
    return all(call.startswith("state.note(") for call in calls)


def _has_interaction_steps(canonical: dict) -> bool:
    return any(isinstance(s, dict) and s.get("kind") == "interaction" for s in canonical.get("steps") or [])


def migrate_card(card: dict, *, canonical_key: str, card_id: str) -> tuple[dict, str | None]:
    """Migrate one card dict to v2. Returns (card, authoring-worklist reason)."""
    alt_text, description = split_alt_text(card.get("description") or "")
    migrated: dict = {"id": card.get("id") or card_id}
    if "image_url" in card:
        migrated["image_url"] = card["image_url"]
    migrated["title"] = card.get("title", "")
    migrated["description"] = description
    migrated["alt_text"] = card.get("alt_text") or alt_text

    raw_canonical = card.get(canonical_key)
    if raw_canonical is None:
        # Fillers: title/description only. Annotation is the authoring pass's job.
        return migrated, "no canonical (filler)"

    canonical = normalise_canonical(raw_canonical)
    needs: str | None = None
    if canonical.get("steps"):
        # Ordered plans carry their own executable code; interaction barriers
        # cannot run inside a one-shot sandbox function at all.
        canonical.setdefault("sandbox", None)
        if not _has_interaction_steps(canonical):
            needs = "steps plan without standalone sandbox"
    elif not canonical.get("sandbox"):
        canonical["sandbox"] = ops_to_sandbox(canonical.get("ops") or [])
        if canonical["sandbox"] is None:
            needs = "no mechanical sandbox (ops unmappable or empty)"
    if _is_note_only(canonical.get("sandbox")):
        needs = "note-only effect (prose degraded to custom_note)"

    migrated[canonical_key] = canonical
    return migrated, needs


def render_file(path: Path, canonical_key: str, id_prefix: str) -> tuple[str, list[str]]:
    cards = json.loads(path.read_text(encoding="utf-8"))
    worklist: list[str] = []
    migrated = []
    for index, card in enumerate(cards):
        new_card, needs = migrate_card(card, canonical_key=canonical_key, card_id=f"{id_prefix}-{index:03d}")
        migrated.append(new_card)
        if needs:
            worklist.append(f"{path.name}[{index}] {new_card['title'][:40]!r}: {needs}")
    return json.dumps(migrated, indent=2, ensure_ascii=False) + "\n", worklist


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, help="migrate a single dataset file")
    parser.add_argument("--all", action="store_true", help="migrate every known dataset")
    parser.add_argument("--check", action="store_true", help="verify datasets are already migrated (no writes)")
    args = parser.parse_args()

    if args.file:
        targets = [entry for entry in DATASETS if entry[0].resolve() == args.file.resolve()]
        if not targets:
            print(f"unknown dataset: {args.file} (known: {[str(d[0]) for d in DATASETS]})")
            return 2
    elif args.all or args.check:
        targets = list(DATASETS)
    else:
        parser.print_help()
        return 2

    stale: list[str] = []
    worklist: list[str] = []
    for path, canonical_key, id_prefix in targets:
        rendered, needs = render_file(path, canonical_key, id_prefix)
        worklist.extend(needs)
        if path.read_text(encoding="utf-8") != rendered:
            if args.check:
                stale.append(str(path.relative_to(ROOT)))
            else:
                path.write_text(rendered, encoding="utf-8")
                print(f"migrated {path.relative_to(ROOT)}")
        else:
            print(f"unchanged {path.relative_to(ROOT)}")

    if args.check and stale:
        print("stale (run scripts/migrate_card_schema.py --all):", *stale, sep="\n  ")
        return 1
    if worklist:
        print(f"\nauthoring worklist ({len(worklist)} cards need hand/agent sandbox work):")
        for line in worklist:
            print("  ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
