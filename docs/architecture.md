# Architecture

A durable technical reference for **1000 Blank White Cards** — an AI-arbitrated,
real-time party card game. This document describes the running system: its
module boundaries, the import-layering contract the tests enforce, how a client
action travels through the stack, the deterministic engine + snippet sandbox,
and the RAG pipeline that grounds the interpretation agent.

> Scope note: this file is the technical reference. Narrative / submission
> context lives in `docs/WRITEUP.md`; setup lives in the README. The two
> hand-authored diagrams `docs/agent.excalidraw.svg` and `docs/game.excalidraw.svg`
> are the owner's authoritative design sketches — the Mermaid diagrams below
> complement them, and any place the code has since diverged is called out
> under [Diagram reconciliation](#7-diagrams).

---

## 1. Overview

The system is a Next.js frontend talking to a FastAPI backend over both REST
(room lifecycle) and a WebSocket (live gameplay). The backend owns an in-memory
game state per room and routes each played/authored card through a two-tier
interpreter: a **deterministic engine** (pure reducers that lower structured
"ops" onto game state) and, only when a card carries no compilable ops, a
**single tool-calling LLM agent** that reads the live board and RAG-retrieved
exemplars to interpret free-text cards in character. Retrieval is backed by an
in-memory Qdrant vector store; both chat and embeddings go through one
OpenAI-compatible LLM gateway configured in a single `Settings` object.

```mermaid
flowchart LR
  subgraph Client["Next.js frontend"]
    UI["room/[code]/page.tsx<br/>phase router"]
    WS["lib/ws.ts<br/>useGameSocket"]
  end

  subgraph Backend["FastAPI backend (single worker)"]
    REST["board.app<br/>REST: /rooms, /health"]
    WSH["board.ws<br/>/ws/{room_code}"]
    ROOM["board.rooms.Room<br/>state machine + asyncio.Lock"]
    ENGINE["engine.*<br/>pure reducers"]
    SANDBOX["engine.sandbox<br/>subprocess exec"]
    AGENT["agent.runtime.run_agent<br/>tool-calling LLM"]
  end

  subgraph External["External services"]
    QDRANT["Qdrant<br/>(in-memory vector store)"]
    LLM["LLM gateway<br/>(OpenAI-compatible)"]
  end

  UI <--> WS
  WS -->|REST join| REST
  WS <-->|WebSocket| WSH
  WSH --> ROOM
  ROOM --> ENGINE
  ROOM --> AGENT
  ENGINE --> SANDBOX
  AGENT --> QDRANT
  AGENT --> LLM
  ENGINE -.no LLM.-> ENGINE
```

The backend is deliberately **single-worker**: room state lives in
process-local memory (see [§4](#4-request--websocket-flow)).

---

## 2. Module map

All backend code lives flat under `src/`.
Imports are `board.*`, `models.*`, `agent.*`, `engine.*`, `evals.*`, and the
top-level `config` / `logging_config` modules. Run the backend with
`uvicorn board.app:app`.

| Package | Responsibility | Key files |
| --- | --- | --- |
| **`config`** (`src/config.py`) | Single source of truth for all settings: the one OpenAI-compatible LLM gateway (`llm_base_url` / `llm_api_key` / `llm_extra_headers`, driving BOTH chat and embeddings), embedding dimensions, the `vision_enabled` flag (attach card art to the arbiter's model input), Qdrant, LangSmith, CORS, sandbox flag, and the `dev_mode` flag (gates room persistence + `/dev` endpoints). Cached `get_settings()` singleton. | `config.py` (`Settings`, `get_settings`, `warn_if_no_llm_credentials`) |
| **`models`** | Pure data models, no game logic. `GameState`/`Player`/`WinCondition`; the runtime `ResolutionPlan`/step and `Op` discriminated unions + `EffectProgram` + `Target`/`CardTarget` + `map_authoring_target`; card authoring models; the client/server WebSocket envelopes. | `game_state.py`, `effects.py`, `card.py`, `cards.py`, `ws_messages.py` |
| **`engine`** | The game "physics": pure reducers over `GameState`, the turn loop, scoring/win-condition, card compilation, the event bus + persistent hooks, and the untrusted-snippet execution sandbox. Never calls the LLM. | `facade.py` (`GameEngine`), `reducers.py` (`apply_op`), `apply.py` (`apply_effect`), `compile.py` (`compile_card`), `loop.py` (`advance_turn`, `draw_step`), `scoring.py`, `events.py`, `hooks.py`, `epilogue.py` (`tally_votes`), `sandbox/` |
| **`agent`** | The single tool-calling interpretation agent: the persona system prompt, the interpretation result contract, the LLM factory, the RAG pipeline, and the bound toolbox. Reaches down into `engine`/`models` but never up into `board`. | `runtime.py` (`build_agent`, `run_agent`), `contract.py` (`InterpretResult`), `persona.py`, `llm.py` (`get_chat_model`), `rag/` (`embeddings`, `store`, `retrievers`, `seed`), `tools/` |
| **`board`** | The server surface: FastAPI app factory + REST routes, the WebSocket endpoint, and the room state machine (turn enforcement, deck building, epilogue voting, connection registry). The only layer that orchestrates engine + agent together. | `app.py` (`create_app`), `ws.py` (`ws_handler`), `rooms/` (`room.py`, `manager.py`, `connections.py`, `deck.py`, `epilogue.py`, `store.py`) |
| **`evals`** | Offline evaluation of the interpretation pipeline: the production-faithful benchmark runner (per-run config, `enabled_tools` filtering, cost/latency instrumentation, persisted runs), an LLM-as-judge, scorers, and the legacy standalone harness. Not part of the serving path. | `runner.py`, `judge.py`, `scorers.py`, `harness.py`, `eval_core.py`, `store.py`, `analysis.py`, `viz.py`, `conclusions.py` |

---

## 3. Import-layering contract

Layering is enforced **statically** by `tests/test_layering.py`, which parses
every module under `src/` with `ast` (it never imports them, so it is fast and
free of side effects) and checks the top-level package of each import against
the rules below. Only the first dotted segment matters (`agent.rag.store` →
`agent`); relative imports are ignored because they cannot cross a top-level
boundary.

**Shared infra** — `config` and `logging_config` — may be imported by any
layer (`_INFRA` in the test).

The dependency rules, exactly as the test encodes them:

| Layer | May import | Must NOT import | Enforced by |
| --- | --- | --- | --- |
| `models` | (foundation — infra only) | `engine`, `agent`, `board`, `evals` | `test_models_imports_no_higher_layer` |
| `engine` | `models`, infra, and `engine.sandbox` (lives under engine) | `agent`, `board`, `evals` | `test_engine_imports_no_higher_layer` |
| `agent` | `engine`, `models`, infra | `board`, `evals` | `test_agent_imports_no_higher_layer` |
| `engine.sandbox` | `engine` (documented lazy coupling: `sandbox.revalidate` → `engine.apply`) | `agent`, `board`, `evals` | `test_engine_sandbox_may_import_engine` |

`board` and `evals` are the top of the stack; nothing forbids what they import,
so `board` is the single place where `engine` and `agent` are composed together
(the room orchestrates a play by first trying `compile_card` and only then
`run_agent`). `test_src_layout_exists` additionally guards the directory shape
(`agent/rag`, `engine/sandbox`, `board/rooms` must exist), so a future
restructure can't silently make the layering tests pass by testing nothing.

Directional summary:

```mermaid
flowchart TD
  board --> engine
  board --> agent
  board --> models
  evals --> agent
  evals --> engine
  evals --> models
  agent --> engine
  agent --> models
  engine --> models
  sandbox["engine.sandbox"] --> engine
  infra["config / logging_config"]

  board -.-> infra
  agent -.-> infra
  engine -.-> infra
  models -.-> infra
```

This is why the `GameEngine` facade (`engine/facade.py`) deliberately keeps its
`resolve_card` **deterministic-only**: LLM interpretation would require reaching
the `agent` layer, which the engine may not do. That orchestration lives one
layer up in `board.rooms.room.Room._resolve_program`.

---

## 4. Request / WebSocket flow

### Room lifecycle (REST)

Rooms are created and joined over REST (`board.app.create_app`):

- `POST /rooms` → `RoomManager.create_room(mode)` returns a 6-char code.
- `GET /rooms` → lists active rooms, newest first (joinable-only by default;
  `?all=true` also includes rooms already in progress). Backs a "join a game"
  lobby screen.
- `POST /rooms/{code}/join` → `RoomManager.join(code, name)` returns a
  `player_id` (an opaque UUID the client stores per-room in `sessionStorage`)
  and a `spectator` flag. **Join policy**: a joiner arriving while the room is
  in the `lobby` phase becomes a real player; a joiner arriving after the game
  has started becomes a spectator (observes, cannot act).
- `GET /rooms/{code}/state` → read-only debug snapshot.
- `GET /rooms/{code}/cards/{card_id}/art` → a card's hand-drawn art as PNG
  bytes (see [Card art](#card-art-out-of-band-transport)).
- `GET /health` → liveness.
- `POST /rooms/{code}/dev/skip-setup`, `POST /rooms/{code}/dev/end-game` →
  dev-loop shortcuts, active only when `DEV_MODE` is set (they 404 otherwise).
  Skip-setup auto-authors each player's cards and fast-forwards to `playing`;
  end-game forces the current game through the real `_end_game` path so
  end-game triggers, scoring, and the epilogue can be exercised on demand.

Rooms are stored via the `RoomStore` protocol (`board/rooms/store.py`). The
default is `InMemoryRoomStore` — **process-local, single-worker only** — cleared
on restart. Under `DEV_MODE` the singleton swaps in `FileRoomStore`, which
persists each room to `.devstate/rooms/<code>.json` after every mutation (via a
`Room.on_change` hook) and rehydrates them on startup so games survive a
`--reload`; it is a dev convenience, not a durable multi-worker backend. Because
REST-join and the WebSocket connect must hit the same worker to see the same
room, `check_single_worker()` warns at startup if `WEB_CONCURRENCY > 1`. A
distributed backend (Redis, etc.) can implement the same Protocol without
touching `RoomManager`.

### Live gameplay (WebSocket)

`board.ws.ws_handler` serves `/ws/{room_code}`. FastAPI/OpenAPI does not
document WebSocket routes, so the wire protocol is described in the app's
OpenAPI `description` (`WS_PROTOCOL_DESCRIPTION` in `board/app.py`).

Envelopes are typed in `models/ws_messages.py`. Inbound messages form a Pydantic
discriminated union (`ClientMsg`, keyed on `type`) validated by a single
`TypeAdapter`; outbound messages are the `ServerMsg` set.

- **Client → server**: `join`, `start`, `play`, `pass` / `end_turn`,
  `create_card`, `preview_card`, `interaction_response`, `epilogue_vote`. There
  is no client `draw` message: drawing is auto-triggered server-side at the
  start of each turn (see `_start_turn` in `board/rooms/room.py`).
- **Server → client**: `state`, `brewing`, `card_interpreted`, `effect_applied`,
  `preview_result`, `prompt_choice`, `interaction_request`,
  `interaction_progress`, `epilogue`, `error`.

**Handshake and close codes** (`board/ws.py`): the socket is accepted, then the
first message MUST be a `join` carrying a valid `player_id`. The frontend
(`lib/ws.ts` → `closeCodeMessage`) mirrors these codes:

| Close code | Meaning |
| --- | --- |
| `4000` | Bad handshake — first message was not a valid `join`. |
| `4001` | Unknown `player_id` (not registered via REST join for this room). |
| `4004` | Room not found. |
| `4009` | Connection replaced by a newer socket for the same player (duplicate tab). |

On (re)connect the server immediately replays a full `state` snapshot, so a
refresh restores the whole game (including `state.log`, which is why every
effect line is persisted there and not only broadcast live).

Snapshots expose the active data-driven game rather than a fixed board model:
`turn_order`, `rules`, player `conditions`, card `attributes`, and registered
hook metadata. The frontend mirrors the engine's active seat from `players[turn_index]` and
renders these values in a generic dynamic-state panel; there is no legacy
direction flag. Cards also retain a bounded mechanical diagnostic (`pending`,
`applied`, `fallback`, or `rejected`), a public reason, and a correlation id, so
failures remain visible after reconnect and can be matched to server logs.

### Anatomy of an action

Every inbound message is serialized through a per-room `asyncio.Lock`
(`Room.handle_action` → `Room._dispatch`), so concurrent sockets cannot corrupt
turn order. Spectators are rejected from all game-mutating message types.

### Generic interaction barriers

`ResolutionPlan` can interleave `ops`, `snippet`, and `interaction` steps.
Interaction descriptors are versioned, bounded data for `choice`, `number`,
`text`, `card_pick`, `confirm`, or normalized vector `drawing` input, addressed
to `active`, `all`, `all_others`, or `player:<id>`. The legacy
`prompt_choice` target flow remains readable and operational.

At a barrier the Room does not commit the working clone. A private,
persisted `PendingResolution` stores the plan cursor, cloned state, resolved
audience, deadline, correlation id, and sealed responses outside `GameState`.
Normal gameplay is frozen. Shared snapshots expose only safe status and counts;
each audience member receives their own request, which is replayed on reconnect.
Responses are schema-checked and authenticated, then the plan resumes exactly
once with validated values in `ctx.interactions[result_key]`. A later stage may
use `input_refs` to turn prior submissions into choices, enabling drawing then
voting without revealing drawings before the submission barrier closes.

Plans are capped at eight total steps, four interaction barriers, and 256 KiB of
aggregate interaction descriptors/references; individual descriptors and
responses are bounded as well. The deadline defaults to 60 seconds. Partial
timeouts continue with submitted values and deterministic type defaults. If nobody responds, all pre-barrier
mechanics are rolled back, the played card is consumed as a visible no-op, and
the turn continues. `FileRoomStore` persists pending resolutions and turn
bookkeeping for dev reloads; application startup recreates timeout tasks without
requiring a client to reconnect.

The frontend keeps lifecycle phases fixed and renders card-defined interaction
stages in one global `InteractionPanel` overlay. It has typed renderers for all
v1 descriptors, counts-only waiting/sealed states, an authoritative deadline
countdown, reconnect replay, and a visible fallback for unknown future kinds.
Drawing input is normalized vector geometry (never image data), re-bounded to
the server's stroke, point, and coordinate limits plus a conservative 48 KiB
browser wire budget for the server's 65 KiB post-parse cap; completed
drawing submissions can then render as choice previews in a later vote stage.

```mermaid
sequenceDiagram
  participant C as Client (lib/ws.ts)
  participant WS as board.ws.ws_handler
  participant R as Room (asyncio.Lock)
  participant CO as engine.compile
  participant AG as agent.run_agent
  participant AP as engine.apply / reducers
  participant CM as ConnectionManager

  C->>WS: play {card_id, ...}
  WS->>R: handle_action(player_id, msg)
  Note over R: turn / spectator guards
  R->>R: author-on-play (fill blank if needed)
  R->>CO: compile_card_plan(card)
  alt compiled plan exists (deterministic)
    CO-->>R: ResolutionPlan
  else free-text card
    R->>CM: broadcast "brewing"
    R->>AG: run_agent(title, desc, state, actor, creator)
    AG-->>R: InterpretResult (plan | legacy fields | fallback)
    R->>CM: broadcast "card_interpreted"
  end
  Note over R: validate and dry-run complete ordered plan
  loop ops / snippet / interaction steps
    R->>AP: execute next step against cloned state
    AP-->>R: cloned state or persisted barrier
  end
  R->>R: move card hand→(center|in_play|discard)
  R->>CM: broadcast "state" + "effect_applied"
  R->>R: advance_turn (or end game)
```

Key behaviours enforced in `Room`:

- **Turn model is auto-draw → play → end**: `_start_turn` draws for the active
  player automatically (there is no client `draw` message). Drawing the last card
  latches `_deck_exhausted`; the drawer finishes their turn, then the game ends.
- **Author-on-play**: the game is *A Thousand Blank White Cards*, so a blank card
  is authored at the moment it's played. `_handle_play` persists the authored
  `title`/`description` and clears the `blank` flag **before** interpreting, so a
  `prompt_choice` follow-up (which re-sends only `card_id` + the choice)
  re-resolves the now-real card identically.
- **Play never silently no-ops** (`_resolve_program`): compiled ops → best-effort
  agent → deterministic `CustomNoteOp` fallback.
- **End game → epilogue**: `resolve_end_of_game` applies any `on_game_end` card
  effects, `evaluate_win_condition` computes `winner_ids`, then voting opens
  (`EpilogueManager`); kept cards are upserted back into the RAG corpus.

### Card art (out-of-band transport)

Players can draw art for a card (the canvas creator in the frontend). Art is a
PNG data-URL, and it deliberately never rides `GameState` or any WebSocket
broadcast — a few sketches would otherwise multiply every `state` snapshot sent
to every client on every action.

- **Inbound** (`models/ws_messages.py` → `models/card.py`): `create_card` and
  `play` (author-on-play) accept an optional `art` field, validated at the
  message boundary — `data:image/png;base64,` prefix, ≤ `MAX_CARD_ART_BYTES`
  (128 KiB) for the whole data-URL, and a base64-decode + PNG magic-byte check
  (`decode_card_art`, the single decode path), so a prefix claim alone never
  smuggles arbitrary content through.
- **Storage** (`board/rooms/room.py`): `Room.card_art` is an out-of-band
  registry (`card_id → data-URL`); the card in `GameState` carries only a
  `has_art` boolean. A per-room running budget (`MAX_ROOM_ART_BYTES`, 4 MiB)
  guards the registry: rooms are never evicted, so once the budget is hit new
  art is dropped — the card is still created/played, just artless
  (`has_art: false`).
- **Serving** (`board/app.py`): clients fetch
  `GET /rooms/{code}/cards/{card_id}/art`, which decodes the registry entry and
  returns raw `image/png` with `X-Content-Type-Options: nosniff` and
  `Cache-Control: public, max-age=31536000, immutable` — card ids are immutable
  and art is written once at authoring time, so the browser cache does all
  repeat work (the frontend uses a plain `<img src>`, `lib/art.ts`).
- **RAG carry** (`board/rooms/epilogue.py`, `board/rooms/deck.py`): a kept
  card's data-URL rides its Qdrant payload at the epilogue upsert, and a
  prior-game card re-entering a new deck surfaces it as a transient `art` key
  that `Room._absorb_card_art` moves back into the registry (re-checking the
  budget) — so hand-drawn art survives across games without ever touching
  game state.
- **Dev persistence** (`board/rooms/store.py`): `FileRoomStore` does NOT
  persist the registry; restore resets `has_art` on any card whose art did not
  survive the restart so clients never fetch a 404.
- **Arbiter vision** (`agent/runtime.py`): with `VISION_ENABLED=true`
  (`Settings.vision_enabled`, default off), the played/authored card's
  data-URL rides `run_agent`'s `card_art` side-channel argument and is
  attached to the model input as an `image_url` content block, so the drawing
  can influence interpretation (the system prompt tells the persona how to
  weigh it: text wins on conflict). Art still never enters `GameState` or any
  snapshot; if the configured model rejects image input, the interpretation
  retries text-only rather than failing the play.

---

## 5. Engine reducer + sandbox model

### Ops, compilation, and reducers

A card resolves as a `ResolutionPlan`: an ordered sequence of `OpsStep` and
`SnippetStep` computation stages. An `OpsStep` contains an `EffectProgram`-style
list of `Op`s from the
discriminated union in `models/effects.py` (`add_points`, `subtract_points`,
`set_points`, `steal_points`, `skip_turn`, `extra_turn`, `reverse_order`,
`scramble_order`, `change_draw_count`, `draw_cards`, `destroy_card`, `transfer_card`,
`set_win_condition`, `set_rule`, `custom_note`, `end_game`). Each op addresses players via a `Target`
(`self`, `left_neighbor`, `all_others`, `chooser`, `player_with_most_points`, …)
and, for card manipulation, a `CardTarget` (`this`, `chosen_card`,
`all_in_play`, `all_in_hand`).

Game rules are **data** (`GameState.rules`, per `docs/state-example.jsonc`):
draw/play counts, the end condition (`deck_empty`/`empty_hand`/`points_reached`/`now`),
the win condition, and an open `extra` bag for card-invented rules. `set_rule`
writes any of these paths; `change_draw_count`/`set_win_condition` are
specialized writers into the same structure; `end_game` sets
`end_condition={type: "now"}`. The Room evaluates `evaluate_end_condition` /
`win_condition_met` during play, so rule changes take effect live.

Two paths produce a plan:

1. **Deterministic compile** (`engine/compile.py::compile_card_plan`) lowers a card's
   canonical `steps`, or the legacy `ops` followed by `snippet`, into an ordered
   plan. The compatibility `compile_card` function still lowers only
   authoring-vocabulary ops (`card["ops"]` or `card["canonical"]["ops"]`) onto
   the runtime `Op` union. Target aliasing is delegated entirely to
   `models.effects.map_authoring_target`; unknown or malformed ops are skipped
   with a debug log. If nothing compiles, it returns `None` (signalling the
   caller to try the LLM). Choice targets flip `EffectProgram.requires_choice`.
2. **Agent interpretation** (§6) produces an `InterpretResult` with an explicit
   plan or legacy `program`/`snippet` fields. Legacy fields lower to ops first,
   then the snippet, so a later snippet reads the actual state produced by the
   deterministic prefix.

The Room validates all choices before execution, moves the played card out of
the hand in a cloned working state, and applies each stage in order. A complete
success commits the working state; any stage failure discards every mechanical
change, consumes the played card as a visible no-op, and advances normally.
This gives post-effect reads a real computation boundary without teaching the
sandbox to simulate reducers.

Application is pure and immutable — reducers take `(state, op, ctx)` and return
a **new** `GameState`, never mutating the input:

- `engine/reducers.py::apply_op` dispatches an op through the `_REDUCERS` table.
  `_resolve_targets` / `_resolve_card_targets` turn a `Target`/`CardTarget` +
  `HookContext` into concrete id lists (raising if a `chooser`/`chosen_card`
  arrives without a resolved choice — which the Room guards against by prompting).
- `engine/apply.py::apply_effect` iterates a program's ops and emits
  `ON_SCORE_CHANGE` after any op that changed a score, so persistent hooks react.
- `engine/loop.py` owns `advance_turn` (stepping through `GameState.turn_order`
  — an explicit, ordered, mutable list of player ids — honouring per-player
  `skip_next` / `extra_turn` conditions, a named skip-predicate registry, and
  spectator-skipping) and `draw_step`.
- `engine/events.py` / `engine/hooks.py` provide the synchronous `EventBus`,
  `HookContext`, and the `fire_hooks` ordering algorithm (player-scoped hooks
  fire before center-scoped "house rule" hooks; an `uncounterable` source card
  ends the chain early).
- `engine/facade.py::GameEngine` is a thin, stateless ergonomic wrapper naming
  the "physics" surface from the design diagram (`add_points`, `subtract_points`,
  `draw`, `resolve_card`, `check_end_game`, `determine_winner`,
  `update_history`). Every method delegates to the underlying pure function and
  reimplements no logic; `resolve_card` is deterministic-only by design.

### Structured mechanics history

`GameState.history_events` is an append-only `HistoryEvent` ledger alongside the
human-readable `log`. It records public facts only: event sequence and kind,
actor/target player ids, the public played/source card id, actual numeric amount,
and rule path/source. Drawn card ids, hand contents, interaction secrets, and
generated prose never enter this ledger.

`apply_op` records actual draw, score, and rule deltas after each reducer. This
single seam covers compiled effects, ordered snippets, persistent hooks,
end-of-game scoring, and direct façade calls without double-counting. The Room
adds explicit turn/cannot-play draws, committed plays, and final winner ids; the
pure turn/facade paths add their own play and terminal events. Because atomic
plans build on a cloned state, failed plans discard their provisional history
with every other mechanical change.

`engine.history` supplies bounded public queries and exact draw aggregates.
Those are exposed to generated code as `SandboxGame.history()` and
`draw_totals()`, and to the interpreter through the context-bound
`read_game_history` tool. History-based cards therefore inspect typed data, not
the presentation log. `EndGameOp.winners` also accepts multiple target addresses
for deterministic co-winner overrides.

### The snippet sandbox

Genuinely novel effects that no combination of ops can express can be expressed
as a generated Python hook (`SnippetEffect.code`: the body of
`def apply(state, ctx)`). This path is LIVE in the serving layer:
`Room._execute_plan` executes an immediate (trigger-less) snippet through
the pipeline below, and a snippet with a `trigger` becomes a persistent
`HookSpec` on `GameState.hooks` via `RegisterHookOp` — serialized state, so
house rules survive reloads, replay deterministically from a kept card's
canonical ops, and never leak across rooms (each Room derives its registry
from state via `engine.hooks.build_registry`). The Room fires
`on_play`/`on_turn_start`/`on_turn_end`/`on_draw_step`/`on_score_change`/
`on_game_end` (capped per event), and `on_validate_play` hooks may veto a play
(`reject_play`) before it resolves. `engine/sandbox/` isolates the untrusted
code:

- **`validate.py`** — a static AST allowlist run *before* code is ever stored or
  executed: no `import`/`from`-import, no `exec`/`eval`/`open`/`compile`/
  `__import__`/`breakpoint` calls, no private attribute access, and exactly one
  top-level function named `apply`. Direct calls on the `state` argument are also
  checked against the real `SandboxGame` surface, including argument binding and
  close-name suggestions (`state.draw(...)` is rejected with `draw_cards`).
- **`api_surface.py`** — the snippet's `apply` receives a restricted
  `SandboxGame` façade, never raw `GameState`. It exposes reads (player views,
  `my_hand`, `hand_size`, `deck_size`, `rules`, `conditions`, `card` metadata,
  `turn_order`) and mutators at FULL op parity (points/turn ops plus
  `draw_cards`, `destroy_card`, `transfer_card`, `set_win_condition`, `end_game`, `set_rule`,
  `set_condition`, `set_card_attribute`, `create_card`, `register_hook`,
  `unregister_hook`, `custom_note`, `reject_play`) that only **record op dicts** —
  they cannot touch real state. Canonical mutator names and parameter order match
  the `Op` models exactly; compatibility aliases remain available for persisted
  snippets.
- **`runner.py`** — `execute_snippet` spawns an isolated subprocess
  (`python -I` via `_child_runner.py`) with a wall-clock timeout; the child
  emits the recorded op diff as JSON. **The subprocess is the security boundary**
  (in-process exec is not); production would swap in gVisor/Firecracker or a
  hosted exec service. Gated by `Settings.snippet_execution_enabled`.
- **`revalidate.py`** — the final net: the child's op diff is re-parsed through
  the same Pydantic `Op` union (capped at 50 ops; choice-requiring targets are
  rejected, hook-produced diffs may not `register_hook` — no self-replicating
  hooks — and `reject_play` only counts as a veto in `on_validate_play` fires)
  and applied through the
  **same engine reducers** as a normal play. Snippets get no special mutation
  path — this is the documented lazy coupling `engine.sandbox → engine.apply`.

So the sandbox isolates: (1) *what code can be written* (AST allowlist), (2)
*what it can reach* (`SandboxGame` façade, not `GameState`), (3) *where it runs*
(subprocess + rlimit + timeout), and (4) *what it can ultimately do* (re-validated
ops through the normal reducers).

`read_engine_methods` derives its signatures from `SandboxGame`, rather than the
server-only `GameEngine` façade. For any snippet, hook, or mixed plan, the agent
also receives a context-bound `dry_run_effect` tool. It runs the complete ordered
plan against a cloned state through the real reducers and sandbox subprocess. A
server-side final gate repeats static validation and the dry run, attempts one
bounded repair when validation fails, and returns an effectless invalid verdict
if the repair is still unsafe. Snippet handlers close over their own source code;
there is no process-global cache that can collide across rooms or cards.

`preview_card` follows the same boundary without committing: the room gives the
agent a cloned state containing an ephemeral preview card, then dry-runs the
returned plan through the real reducers and sandbox. `preview_result` includes
the plan, mechanical status, sanitized reason, and correlation id. Invalid
methods such as `state.draw` therefore fail before a card is created or played.
Preview binds no persistent-write tools (`remember_decision`).

If no op, sandbox method, hook, or supported interaction can express a card,
that capability gap is recorded out-of-band by the triage agent below, via
`record_capability_wish` (`src/agent/tools/capability_wish.py`), which appends
bounded capability telemetry to configured JSONL.
`scripts/export_capability_wishes.py` exports it for offline human triage. The
sink has a hard byte cap and failures are best-effort/non-throwing. The runtime
never invokes `bd`, writes source, or attempts self-modification.

### Failure-triggered triage agent

Until this feature, the interpreting agent's only capability-gap signal was an
in-loop `wish` tool call — invoked "if none can express the card" — and the
persona all but never reached for it, so capability gaps went mostly
unrecorded. That in-loop `wish` tool has since been removed from the
interpreter entirely: on failure the interpreter now just returns a visible
fallback, with no tool call of its own. The trigger has moved out of the
interpreter: the Room now watches every point a play falls back to its
mechanical no-op and reports it, independent of what (if anything) the agent
decided to do.

```mermaid
flowchart TD
    P[Card played] --> R{Interpret<br/>run_agent}
    R -->|ok + plan| X[Execute plan]
    R -->|verdict invalid| K1[invalid_verdict]
    R -->|ok but empty plan| K2[no_op]
    X -->|snippet raises / timeout| K3[sandbox_failure]
    X -->|interaction setup fails| K4[interaction_setup]
    X -->|hook snippet crashes| K5[hook_failure]
    X -->|success| OK[status: applied]
    K1 & K2 & K3 & K4 & K5 --> FB[CustomNote fallback<br/>turn continues, never blocks]
    FB --> RF["Room._report_failure_for_triage(kind, card, ...)"]
    RF -->|triage_agent_enabled<br/>+ dedupe per card,kind| SC["schedule_triage(CardFailure)"]
    SC --> TS[TriageScheduler<br/>fire-and-forget, concurrency-capped]
    TS --> RT["run_triage()"]
    RT --> BR["build_triage_report() -> TriageReport<br/>(deterministic fallback if LLM down)"]
    BR --> W["record_capability_wish()<br/>.devstate/capability_wishes.jsonl"]
```

`Room._report_failure_for_triage(kind, card, correlation_id, *, exc, verdict, comment)`
(`board/rooms/room.py`) is the single funnel, called from the sites covering
five failure kinds:

- **`sandbox_failure`** — a generated snippet raised, timed out, or produced an
  invalid plan at play time; caught by `_finish_play`'s generic except, which
  logs `[snippet error]` and substitutes a `CustomNoteOp` fallback.
- **`no_op`** — the interpretation returned `verdict="ok"` but an empty plan
  (`_resolve_plan`'s fallback branch).
- **`invalid_verdict`** — `verdict != "ok"` (the agent gave up, or a repair
  attempt still failed) (`_resolve_plan`).
- **`hook_failure`** — a persistent hook's snippet crashed. Errors are drained
  from the engine via `engine.hooks.collect_hook_errors`, which the Room reads
  around `_emit_hooks`; the `on_validate_play` path (`_check_play_veto`)
  reports the same way.
- **`interaction_setup`** — an interaction barrier failed to start safely.

`_report_failure_for_triage` builds a `CardFailure` and hands it to
`agent.triage.schedule_triage`, which — only when
`Settings.triage_agent_enabled` — schedules the triage on a process-global
`TriageScheduler`: a loop-bound semaphore sized to
`triage_agent_max_concurrency`, tracked in a task set. Scheduling returns
immediately; the play and turn advance on the fallback already computed
regardless of how (or whether) the triage completes. `board/app.py`'s lifespan
drains the scheduler on shutdown (`TRIAGE_DRAIN_TIMEOUT_SECONDS`, 5s) so
in-flight reports get a chance to finish rather than being silently abandoned.

The payload carries what a human reviewer needs to triage the gap: the card's
title/description, the failure kind, the agent's verdict/comment (when there
was one), a sanitized exception, the mechanical fallback note, a game-state
summary (`_summarize_state`), recent public history (`public_history` +
`draw_totals`), and the interpretation's `RunMetrics` — token and tool-call
counts captured by attaching `evals.instrumentation.UsageCallback` to the
`run_agent` call. That callback is attached only when the triage agent is
enabled (the default); disabling triage makes the play path pay nothing for it.
Metrics are stashed per-card in `Room._last_run_metrics` and popped once a
failure report consumes them.

`build_triage_report` (`src/agent/triage.py`) is a structured-output
LLM call returning a `TriageReport` — diagnosis, root-cause bucket, what the
card wanted, the missing capability, a recommendation, severity, and
confidence. If the LLM/gateway call fails, it degrades to a deterministic
report built from the payload alone. `run_triage` maps the report
into the same four fields `record_capability_wish` writes
(card title, description, a
`[kind] ... — recommendation: ...` "what I wanted" string, and the missing
capability), landing in the one `.devstate/capability_wishes.jsonl`
sink with no beads/DB dependency. Reports are deduplicated per `(card_id,
kind)` per room session (`Room._reported_failures`, gated by
`triage_agent_dedupe`) so a card that keeps hitting the same failure mode is
reported once.

Everything is config-gated and on by default (`Settings` in `config.py`, env
vars in `.env.example`):

| Flag | Default | Meaning |
| --- | --- | --- |
| `triage_agent_enabled` | `True` | Master gate. Set `false` to make `_report_failure_for_triage` return immediately and skip attaching `UsageCallback` — the play path is then byte-identical to before this feature. |
| `triage_agent_max_concurrency` | `2` | Concurrent triage LLM calls in flight (across all rooms). |
| `triage_agent_model` | `""` (blank) | Model for triage; blank inherits the interpreter's `llm_chat_model`. Override to triage on a different/cheaper model. |
| `triage_agent_timeout_seconds` | `30.0` | Per-report time budget. |
| `triage_agent_dedupe` | `True` | One report per `(card_id, kind)` per room session. |

RunMetrics are captured in-process regardless of LangSmith. When
`Settings.langsmith_tracing` is also on, the interpretation's `run_agent` call
additionally carries LangSmith metadata (`card_id`, `correlation_id`, a
`run_name`), and the failure payload carries `{project, correlation_id}` so a
human triaging a capability wish can jump straight to the trace (see
`docs/deploy/langsmith-setup.md`).

---

## 6. RAG pipeline

The agent grounds its interpretation in exemplar cards stored in Qdrant. The
whole pipeline lives under `agent/rag/` and, like the rest of `agent`, reads its
provider config from the one `Settings` gateway.

```mermaid
flowchart LR
  SEED["data/seed_cards.json"] -->|startup| LOAD["seed.load_seed_cards"]
  LOAD --> STORE
  EMB["rag.embeddings<br/>OpenAIEmbeddings (gateway)"] --> STORE
  STORE["rag.store<br/>Qdrant :memory: 'cards'"]
  RET["rag.retrievers<br/>dense / hybrid (BM25+RRF)"] --> STORE
  TOOL["agent.tools.card_rag_hybrid"] --> RET
  RUN["agent.run_agent"] --> TOOL
  KEPT["epilogue kept cards"] -->|upsert source='player'| STORE
```

- **Embeddings** (`rag/embeddings.py`): a cached `OpenAIEmbeddings` singleton
  pointed at the configured gateway. The vector size is
  `Settings.embedding_dimensions` (default 1536 for `text-embedding-3-small`;
  override for other models), which is threaded into the Qdrant collection so
  sizes always match. `embed_text_cached` / `embed_texts_cached` add a
  disk-backed content-hash cache (`.embedding_cache.json`, keyed by
  model + dimensions + text) so unchanged cards are never re-embedded across
  reloads; the model/dimensions in the key invalidate the cache automatically
  when the embedding model changes.
- **Store** (`rag/store.py`): an in-memory Qdrant client (`location=":memory:"`)
  managing one `cards` collection (cosine distance). `upsert_card` embeds
  `title + description` and stores `canonical`/`source` as payload (not
  embedded); point ids are a stable blake2b hash of the card_id so re-seeding is
  idempotent. `search(query, k)` returns the top-k payloads with scores;
  `list_all_cards()` scrolls all payloads offline (no embedding call) and is the
  card source for deck building.
- **Seeding** (`rag/seed.py`): at startup `board.app`'s lifespan best-effort
  calls `load_seed_cards`, which `init_store()`s and upserts
  `data/seed_cards.json` in one batched `upsert_cards` call (a single embedding
  round-trip for cache misses). A missing file or offline gateway degrades
  gracefully.
  `scripts/data_prep/build_seed_corpus.py` deterministically generates that combined file
  from `seed_cards_gold.json` plus `seed_cards_fillers.json`; CI checks the files
  cannot drift. Gold entries are executable full plans, including static chains,
  post-draw computation, structured-history scoring, and basic/spicy/wild Uno.
- **Retrievers** (`rag/retrievers.py`): `dense_retriever()` is the baseline
  cosine retriever; `hybrid_retriever()` — the one bound in the default toolbox
  via `card_rag_hybrid` — fuses that dense search with a per-call BM25 pass over
  `list_all_cards()` using Reciprocal Rank Fusion, so exact game-mechanic
  keywords (draw, discard, steal, swap) match even when the embedding blurs them.
- **How the agent uses it** (`agent/runtime.py`, `agent/tools/`): `run_agent`
  builds a LangChain tool-calling agent (`create_agent`) with the persona system
  prompt and a bound toolbox. `get_default_tools()` returns the context-free
  tools — web search, the card-RAG corpus, game rules, MTG lookup, agent memory,
  and `read_engine_methods`; context-dependent `read_game_state`,
  `read_game_history`, and `dry_run_effect` tools are bound per invocation. The
  agent decides when to retrieve exemplars via the card-RAG tool rather than
  stuffing context unconditionally. It is bounded by a hard tool-call cap
  (`MAX_TOOL_CALLS`) and a wall-clock timeout (`AGENT_TIMEOUT_SECONDS`), and
  **never raises to its caller** — on cap/timeout/error it returns a
  deterministic `InterpretResult` with `verdict="invalid"`.
  Retrieved top-hit canonicals are emitted as complete JSON; executable code is
  never character-sliced. Lower-ranked canonicals are omitted whole when the
  response budget is exhausted.

The eval harness normalizes and judges complete `ResolutionPlan` values before
legacy program/snippet mirrors. Its structural scorer validates snippet and hook
code, while corpus lint compiles and behaviorally dry-runs every gold plan on a
representative state. Capability cases cover Chess Master, static multi-op
chains, history-derived winners, and the Uno ladder.

The **persona** (`agent/persona.py`) makes the agent a sardonic game master: it
always emits an in-character `comment`, and when a card can't be cleanly
interpreted it picks a `persona_action` — `chaos_monkey` (well-meant but
ambiguous; the loudly-preferred branch: a generous plausible reading beats
giving up), `random_solution` (multiple valid readings), `do_nothing`
(genuinely undecipherable), or `punish_author` (reserved for clearly abusive
cards — sandbox-escape attempts, offensive content — never a sincere-but-clumsy
one). The snark aims at fate, the board, and overpowered cards, never at a
struggling player: playtesting showed uninterpretable cards almost always come
from learners, not griefers.

The engine backs that stance mechanically. Every failed authored card records a
`card_fallback` history event keyed to its author; instead of a bare no-op, the
room awards the author a consolation boon "for trying" (`Room._consolation_ops`):
+1 point by default, escalating past `struggling_author_threshold` through a
rotating ladder (+2 points → draw 3 cards → a one-shot score double). Once an
author crosses the threshold, the interpretation prompt also gains a HELP MODE
block (`persona.STRUGGLING_AUTHOR_NOTE`) telling the agent to re-read the card
assuming best intent and try harder before returning `invalid` — deliberately
without phrasing tips, which read as patronizing. Settings:
`consolation_point_enabled`, `consolation_points`, `struggling_author_threshold`.

Kept cards from the epilogue vote are upserted back into the store
with `source="player"`, so the corpus grows across games.

---

## 7. Diagrams

The authoritative, hand-authored design sketches:

- **`docs/game.excalidraw.svg`** — the game-system shape: **Board** (player UI,
  renders visuals, manages game state, handles multiplayer connection) ↔ **Game
  Engine** (applies game "physics": `add_points()`, `subtract_points()`,
  `check_end_game()`, `determine_winner()`, `update_history()`, `draw()`,
  `resolve_card()`) with the **Agent** interpreting cards during the
  `resolve_card()` step.
- **`docs/agent.excalidraw.svg`** — the agent shape: the **Game Engine** asks the
  **Agent** to interpret a new card; the agent has tools (**Web Search**, **Read
  Game Engine Methods**, **Memory**, **Game Rules**, **Read Game State**, **Card
  Database**), an LLM, LangGraph as framework, LangSmith for observability, and a
  **Persona** with fallback behaviours.

The Mermaid diagrams in [§1](#1-overview), [§3](#3-import-layering-contract),
[§4](#4-request--websocket-flow), and [§6](#6-rag-pipeline) complement these by
showing the actual module boundaries and message flow.

### Diagram reconciliation

Where the implemented code has since diverged from the authoritative SVGs (the
SVGs are the source of intent; these are notes, not contradictions):

1. **`resolve_card()` and LLM interpretation** (`game.excalidraw.svg`). The
   diagram shows the Agent interpreting cards "during the `resolve_card()`
   step". In code, the `GameEngine.resolve_card` facade method
   (`engine/facade.py`) is intentionally **deterministic-only** — it never calls
   the LLM, because the layering contract forbids `engine → agent`. The
   equivalent orchestration (compile → LLM → `CustomNote` fallback) lives one
   layer up in `board.rooms.room.Room._resolve_program`. The *conceptual* step
   is the same; the *code boundary* is one layer higher than the sketch implies.

2. **Persona branches** (`agent.excalidraw.svg`). The diagram lists three
   fallback behaviours (A: does nothing, B: punishes creator, C: does something
   random). The implemented `persona_action` vocabulary has **four**: `do_nothing`,
   `punish_author`, `chaos_monkey`, and `random_solution` — i.e. the diagram's
   "does something random" was split into a well-meant-but-ambiguous branch
   (`chaos_monkey`) and a pick-a-reading-at-random branch (`random_solution`),
   plus the `none` value for cleanly-interpreted cards.

---

## 8. Frontend design system — "Sketchbook Tabletop"

The frontend's visual language is a hand-drawn sketchbook on a table: paper
background with a dot grid, marker-lettered headings, taped-down white cards,
sticker-style buttons, and a green felt play surface.

**Provenance.** The system was designed in Claude Design and exported to
`docs/design/`: `1000-blank-white-cards.dc.html` (the full screen-by-screen
prototype), `Card.dc.html` (the card face spec whose sizing math `SketchCard`
follows), and `handoff-README.md` (tokens, typography, and component notes).
Those files are the source of intent; the code below is the implementation.

**Tokens** (`frontend/app/globals.css`): all colors and fonts are CSS custom
properties bridged into Tailwind via `@theme inline`. The shadcn/ui semantic
set (`--primary` red `#e24a3b`, `--secondary` blue, `--accent` yellow, paper
`--background`, ink `--border`/`--input`) is joined by sketch-specific tokens —
`--color-felt`, `--color-panel-paper`, `--color-marker-green`, `--color-amber`,
`--color-tape`, `--color-ink` — plus utility classes for the paper dot grid,
`sticker-shadow` (the offset hard shadow under buttons), `panel-shadow`, and
the `floaty`/`popin`/`wig` keyframes. Fonts load via `next/font`
(`app/layout.tsx`): Permanent Marker (`--font-marker`, headings), Patrick Hand
(`--font-hand`, all body/game text), Nunito (`--font-sans`).

**SketchCard** (`frontend/components/sketch-card.tsx`): the single card face
used on every card surface — hand fan, table center, opponent minis, setup
lists, epilogue vote, results. All dimensions derive from the `w` prop; it
renders face-up text, face-down card backs, un-authored blanks, art (via
`lib/art.ts` URLs), verdict stickers, and the brewing overlay. `stableRotation`
(exported from the same module) derives a deterministic resting tilt from the
card id so layouts don't shuffle between renders.

**Player identity** (`frontend/lib/players.ts`): `PLAYER_COLORS` +
`playerColor(index)` are the single source for identity colors, keyed to the
original turn-order index everywhere (avatars, score numbers, target buttons,
results bars) so a player's color never changes between views. The card
creator's pen palette reuses the same constants.

**Card creator** (`frontend/components/card-creator.tsx`): the authoring
studio — title input, freehand pointer-drawn canvas (vector strokes redrawn at
device pixel ratio), ink/nib pickers, undo/clear, and an emoji stamp grid.
`getArt()` exports a PNG data-URL, retrying at smaller scales until it fits the
backend's 128 KiB art cap. It is a pure authoring surface with no WS knowledge;
`CreateCardDialog` (setup-only authoring; the server rejects `create_card`
outside the setup phase) and `PlayBlankDialog` (author-on-play of a blank, the
only mid-game authoring path) own submission and pass a flow-specific caption.
