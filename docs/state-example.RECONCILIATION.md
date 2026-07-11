# state-example.jsonc ↔ GameState reconciliation

`docs/state-example.jsonc` is a hand-authored example of the game state. After the
board/engine/agent rewrite it had drifted from the actual model in
`src/models/game_state.py`. This note records each divergence and the decision made
for it, per bead `82f.7`.

**Overall finding:** the old `state-example.jsonc` was a *pre-rewrite design sketch*,
not aspirational design the code fails to implement. In every case the running engine
is a **superset** of what the sketch described — every rule concept in the sketch is
supported (often more flexibly) by the current code. So the resolution was to **update
the doc to match the code**. No follow-up code beads were needed: there is no
intended-but-unimplemented behavior here.

The authoritative shape is `GameState.model_dump()` — the exact JSON the backend
broadcasts to clients over the WebSocket. The new `state-example.jsonc` was generated
from a real `GameState(...)` instance and hand-annotated.

## Per-field decisions

| Old sketch | Current code | Decision | Rationale |
|---|---|---|---|
| `players` as a **map keyed by name** (`{"Player1": {...}}`) | `players: list[Player]`, each with a stable `id`; `turn_index` indexes the list | **Doc updated → list** | Turns and card targets key on `id`, not display name; names aren't unique and a list preserves seat order. |
| Per-player `points`, `cardsInHand`, `conditions` | `score`, `hand`, `in_play`, `connected`, `spectator` | **Doc updated → real fields** | The rewrite added the `in_play` (in-front-of) zone, connection tracking, and the spectator flag; renamed points→score, cardsInHand→hand. |
| Per-player `conditions: {skipNextTurn: 1}` | private `_skip_next` / `_extra_turn` sets (PrivateAttr, **not serialized**) | **Doc updated → noted as non-serialized** | Turn bookkeeping is engine-internal and deliberately kept out of the broadcast snapshot; the doc now says so instead of inventing a `conditions` field. |
| Declarative `rules{}` block: `draw`, `play`, `cannotPlay`, `endCondition`, `winCondition` | flat `draw_count`, `direction`, `skip_predicate` + `win_condition{kind,threshold}`; end-of-deck end handled by the loop | **Doc updated → flat fields** | `draw_count` covers `draw` (mutable via the `change_draw_count` op). There is no separate `play`/`cannotPlay` config — a turn is draw-then-play with a draw-again fallback baked into `src/engine/loop.py`/`room.py`, not a config knob. `win_condition.kind` supports all four modes resolved in `src/engine/scoring.py` (`highest_points`, `lowest_points`, `first_to`, `last_standing`), a superset of the sketch's `maxPoints`. |
| Nested `board{cardsInCenter, discardPile, deck}` | flat `deck`, `discard`, `house_rules` (= the CENTER zone) | **Doc updated → flat zones** | State is a flat `GameState`; the shared center zone is stored in `house_rules` and read via `center_cards()`. Card-zone taxonomy is documented on `GameState`. |
| `turnOrder: [names]` | `turn_index` into `players[]` + `direction` | **Doc updated → turn_index/direction** | Order is the list order; rotation is an index plus a direction, so no separate array. |
| `spectators: [names]` | `Player.spectator: bool` inside `players[]` | **Doc updated → spectator flag** | The rewrite folded spectators into `players` (single list through every layer) rather than a parallel collection. |
| `history: [strings]` | `log: list[str]` | **Doc updated → log** | Renamed; same idea (human-readable event log the frontend renders). |

## Fields the sketch omitted (added by the doc)

`room_code`, `mode` (`online`/`in_person`/`both`), the `cards` registry (id → Card),
`connected`, `phase` (`lobby`/`setup`/`playing`/`epilogue`/`ended`), and `winner_ids`
— all present in `GameState` and now shown in the example.

## Follow-up beads filed

None. The code already implements everything the sketch gestured at.
