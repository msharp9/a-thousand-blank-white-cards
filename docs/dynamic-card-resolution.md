# Dynamic card resolution: live contract and acceptance evidence

This document is the operational checklist for cards that combine mechanics,
state-dependent computation, persistent rules, and player interaction. The
authoritative component design remains in [architecture.md](architecture.md),
especially sections 4–6.

## Resolution contract

A card resolves as one bounded, ordered `ResolutionPlan`. Its steps may be:

- `ops`: validated reducer operations for ordinary mechanics;
- `snippet`: sandbox code whose `SandboxGame` calls record those same ops; or
- `interaction`: a persisted barrier that collects typed player responses.

Steps see the state produced by earlier steps. No partial mechanics are visible
while an interaction is pending. The Room commits the completed clone once, or
rolls back every mechanical step and consumes the played card as a visible
fallback. This is why post-draw computation is expressed as `draw_cards` in one
step followed by a snippet in the next, rather than by calling a nonexistent
engine method from generated code.

The canonical sandbox mutator names and argument order match the runtime op
models exactly. For example, both surfaces use
`draw_cards(target, amount)`, `transfer_card(card_target, to_target)`, and
`end_game(winners)`. `read_engine_methods` derives its reference from the live
`SandboxGame` class, static validation rejects unknown calls with suggestions,
and the agent must dry-run the complete plan before it can be committed.

## End-to-end examples

| Behavior | Executable exemplar | Direct regression evidence |
| --- | --- | --- |
| Draw, then score from the resulting hand | `Card Counter` (the Chess Master shape) | `tests/test_gold_exemplars.py::test_card_counter_compiles_draw_then_scores_hand_via_snippet` |
| Replace draw/end/win rules with Uno rules | `Basic Uno` | `tests/test_gold_exemplars.py::test_basic_uno_expresses_empty_hand_end_and_zero_draw`, `::test_basic_uno_gold_ends_when_a_player_empties_their_hand` |
| Add colors and mint Draw 2/Draw 4/Reverse cards | `Spicy Uno` | `tests/test_gold_exemplars.py::test_spicy_uno_gold_executes_rules_attributes_and_created_cards` |
| Enforce color alignment on future plays | `Wild Uno` | `tests/test_gold_exemplars.py::test_wild_uno_gold_registers_and_enforces_color_alignment` |
| End with every player tied for the most draws | `Most Cards Drawn Wins` | `tests/test_structured_history.py::test_most_cards_drawn_snippet_sets_all_tied_winner_overrides` |
| Sealed auction, charge winner, transfer played card, deterministic ties | `Going Once, Going Twice` | `tests/test_room_interactions.py::test_sealed_auction_pauses_atomically_and_resumes_once`, `::test_auction_tie_uses_effective_turn_order` |
| Draw cats, reveal after the barrier, vote, award tied winners | `Cat Show` | `tests/test_room_interactions.py::test_drawing_then_vote_materializes_sealed_submissions_and_tied_winners` |
| Resume safely after reconnect/restart | every generic interaction | `tests/test_room_interactions.py::test_pending_resolution_persists_and_request_replays_without_values`, `::test_pending_resolution_persists_turn_bookkeeping`, `::test_restored_timeout_runs_at_manager_start_without_reconnect` |
| Keep sandbox and op APIs aligned | all generated snippets and hooks | `tests/test_sandbox_api_surface.py::TestWideFacade::test_mutators_record_full_op_parity`, `::test_canonical_mutators_match_op_names_and_parameters` |

The gold exemplars live in `data/seed_cards_gold.json`. Run
`scripts/data_prep/build_seed_corpus.py --check` to prove that the served
`data/seed_cards.json` has not drifted from the reviewed sources. The eval
corpus contains the same capability ladder and scores complete ordered plans,
not isolated first effects.

## Interaction wire contract

The server sends `interaction_request` with schema version 1, a unique
interaction id, a typed descriptor, an authoritative deadline, and safe
progress. The client answers once with `interaction_response`, repeating the
schema version and id and supplying a payload discriminated by kind. Supported
kinds are `choice`, `number`, `text`, `card_pick`, `confirm`, and normalized
vector `drawing`.

Responses are authenticated against the resolved audience. Sealed values stay
private until the barrier completes; shared snapshots and progress contain only
counts. A reconnecting audience member receives their personalized request and
whether they have already submitted, never another player's value. Later steps
receive validated values in `ctx.interactions`, and `input_refs` can materialize
prior submissions as options for a later vote.

Same-process reconnect is part of the production contract. Cold-process restart
of a pending interaction is currently a development feature supplied by
`FileRoomStore`; production deployment still uses process-local rooms and must
run as one worker. Moving rooms across production restarts requires a durable
shared Room store, not a change to card plans or interaction descriptors.

Plans are deliberately bounded: at most eight steps, four interaction barriers,
256 KiB of aggregate interaction data, and 512 KiB for the complete serialized
plan. Timeouts are 10–300 seconds. A partial timeout continues with deterministic
defaults; zero responses roll back the plan and produce a visible fallback.

## Deliberate boundaries

Cards may change rules, create or transform cards, register persistent hooks,
inspect public structured history, request supported player inputs, and combine
all of those capabilities. They may not edit or hot-load server source, execute
agent-authored JavaScript, access private hand/history data through the public
ledger, or bypass reducer revalidation. A genuinely new widget or protocol is a
capability wish and a reviewed between-games code change, not runtime
self-modification.
