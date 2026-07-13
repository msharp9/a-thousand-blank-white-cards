# Spike: should the agent ever edit real engine source at runtime?

Bead `a-thousand-blank-white-cards-35y.6` (parent epic `35y`, the sandboxed
dynamic registry). Research only — no production code.

**Answer up front: no.** Runtime source self-modification buys almost nothing
that a *generic interaction-descriptor protocol* doesn't buy cheaper, and it
destroys every property the current architecture is built on (subprocess
security boundary, per-room isolation, determinism/replay, the
engine-never-imports-agent layering rule). The honest ceiling of the registry —
new interaction protocols — is real, but it is a **wire-protocol and UI
problem, not an engine-code problem**, and the seed of the solution already
exists in `prompt_choice`. Recommendation: build a wish-note channel now,
generalize `prompt_choice` into data-driven interaction descriptors after the
registry phases land, and keep agent-authored *source* changes strictly
offline (PRs gated by CI + human review). Never hot-load agent code into the
serving process.

---

## 1. Which card ideas actually hit the ceiling?

Assume the full registry has landed (35y.1–.5): rules-as-data, open
targets/conditions/attributes, card creation, hooks-as-data, full-width
facade. Everything below the wire protocol is then agent-expressible. What's
left is cards that need a **new shape of client round-trip** — messages
`ClientMsg`/`ServerMsg` (`src/models/ws_messages.py`) can't carry and the
fixed phase router in `frontend/app/room/[code]/page.tsx` can't render.

Concrete card ideas from classic 1KBWC play and the playtest notes, sorted by
what they actually need:

| Card idea | What it needs | Registry-expressible? |
| --- | --- | --- |
| "Uno mode": empty-hand win, draw 0, color-match veto | rule data + hooks | **Yes** (the epic's exemplar ladder) |
| "Steal from whoever you point at" | active-player single choice | **Yes today** — `requires_choice` → `prompt_choice` |
| "Auction this card to the highest bidder" | numeric input from **every** player, collected before resolution | No — protocol ceiling |
| "Trade: offer a card; they accept or counter" | two-party offer/accept round-trip | No — protocol ceiling |
| "Everyone secretly picks a card; reveal simultaneously" | hidden commitment from all players, reveal barrier | No — protocol ceiling |
| "Everyone writes a caption; funniest (judged) wins 5" | free-text input from all + a judge pick | No — protocol ceiling |
| "First player to click gains 3" | client-side timer / race semantics | No — protocol ceiling |
| "Add a draft phase every 3 rounds" | a phase string the frontend's phase switch doesn't know | Half — backend phase is just data once rules-as-data lands; frontend router is the blocker |
| "Draw a picture on this card" (the soul of physical 1KBWC) | canvas/image input on authoring | No — but it's a *fixed* feature, not a dynamic one: build it as a normal frontend feature, no self-modification involved |

Two observations that shape everything else:

1. Every "No" row is the same missing primitive repeated: **ask a described
   audience for a described input, wait for all of them, feed the answers back
   into resolution as data.** `prompt_choice` is exactly this primitive
   restricted to (audience = active player, input = pick-one). None of the
   rows needs new *engine physics* — bids, trades and reveals all lower onto
   existing/registry ops (`steal_points`, card moves, notes) once the inputs
   exist.
2. We are guessing at frequency. Before building anything heavier than the
   descriptor protocol, we should measure how often real games hit the wall —
   hence the wish-note channel (§3).

## 2. If we did let the agent edit source: isolation designs

Evaluated against the four invariants: determinism/replay (state is pure
serializable data; snippets are pure fns of state+ctx), multi-room isolation
(the epic already flagged global-singleton leakage as a bug to fix, not a
pattern to extend), the layering rule (`tests/test_layering.py`: engine never
imports agent/board), and the security boundary (today: subprocess + AST
allowlist + `SandboxGame` facade + `revalidate.py`; the code never enters the
serving process).

### 2a. Hot-reload agent-authored modules into the serving process

Agent writes a `.py`, server `importlib.reload`s it into `engine`/`board`.

- **Security:** catastrophic. The boundary moves from an `-I` subprocess with
  a record-only facade (`runner.py` is explicit: "in-process exec is NOT a
  boundary") into the FastAPI worker — full filesystem, network, env, every
  room's state, the LLM API key.
- **Multi-room:** gone. `sys.modules` is process-global; one room's house rule
  rewrites physics for every room. This is the exact global-singleton disease
  (hooks.py/apply.py/loop.py) the epic exists to cure.
- **Determinism/replay:** gone. Behavior is no longer a function of
  serializable state; `FileRoomStore` rehydration and any future replay are
  meaningless without also versioning arbitrary code blobs.
- **Layering:** engine executing agent-authored source at import level is the
  inversion the test suite exists to forbid.

**Verdict: never. This is the one permanently closed door.**

### 2b. Per-room forked engine process

Fork a child process per room owning that room's engine (and, necessarily,
its slice of the WS protocol — new message types live in the serving layer,
so the fork can't stop at `engine`). Parent proxies sockets to children.

- **Isolation:** genuinely per-room; a mutated child dies with its room.
- **Security:** the child still runs agent-authored code with real Python
  privileges. A plain `fork` is not a sandbox — you'd need gVisor/Firecracker
  per room plus a strict IPC schema at the parent boundary. That's a
  platform-engineering project (supervision, state handoff on reconnect,
  crash recovery, resource caps), not a feature.
- **Determinism:** recoverable only if the child's code is snapshotted as
  data with the room — at which point you've reinvented "code as state,"
  which is the registry, with extra steps.

**Verdict: architecturally coherent but wildly disproportionate. Do not build
unless telemetry (§3) proves descriptors insufficient AND microVM infra
already exists for other reasons. Park it.**

### 2c. Agent-authored PRs gated by CI + human review

Agent can't express a card → it drafts a real patch (new `ClientMsg` variant,
Room handler, frontend component), opens a PR; CI (layering tests, coverage)
plus a human gate the merge.

- **All four invariants hold trivially** — this is just software development
  with an unusual contributor. Nothing loads at runtime.
- **Cost:** near zero; the repo already has CI, beads, and worktree
  orchestration.
- **Limitation:** it is not runtime self-modification. The card that needed
  bidding resolves as a `CustomNoteOp` *today* and works *next game*. That's
  the right trade: the game evolves between sessions; sessions stay safe.

**Verdict: yes — this is the sanctioned path for anything that truly needs
new source, and it composes with the wish-note channel (§3).**

### 2d. Plugin API with a review step

A stable extension point (e.g. an "interaction protocol plugin": message
schema + pure server handler + UI descriptor), agent authors a plugin, human
approves, plugin loads at **room creation**, never mid-game.

- Better than 2a/2b (bounded surface, reviewed, versioned), but it forces us
  to design and freeze a plugin ABI before knowing what plugins want — the
  same frozen-vocabulary mistake the epic is un-making, one level up. And its
  human-review loop makes its latency identical to 2c with more machinery.

**Verdict: skip. If §3's descriptors grow a real vocabulary, the descriptor
schema *is* the plugin API, data-shaped, with no code loading.**

## 3. The cheap middle grounds (this is where the value is)

### 3a. Wish-note channel — build first, trivially cheap

Give the interpretation agent a `wish` tool (sibling to `read_engine_methods`
in `agent/tools/`): when it cannot express a card even with the full registry
facade, it records `{card_title, card_description, what_i_wanted,
missing_capability}` in append-only JSONL telemetry — then falls back exactly
as today (persona action /
`CustomNoteOp`; the play still never silently no-ops).

- Turns §1's speculation into telemetry: every real ceiling hit becomes a
  triaged, human-reviewed feature request. It is the input queue for 2c.
- The runtime never invokes `bd` and never edits source. A human exports and
  triages the records offline. In-character, the arbiter can even say "the
  table can't do that yet — I've filed a complaint with management," which is
  extremely on-brand for this game.

### 3b. Interaction descriptors — the real answer to the ceiling

Generalize the existing `prompt_choice` pattern into a small data language
for interactions. Today's flow (the seed): `EffectProgram.requires_choice` →
Room holds the play pending → sends `PromptChoiceMsg {prompt, choices}` to
the active player → the fixed `TargetPickerDialog` renders it → client
re-sends `play` with `chosen_player_id`/`chosen_card_id` → resolution
re-runs with the choice as plain data. Every hard case in §1 is that flow
with three axes widened:

1. **Audience** — not just the active player: `active | all | all_others |
   player:<id>`, with per-audience privacy (a secret pick is only revealed in
   the post-barrier state).
2. **Input kind** — not just pick-one: `choice | number (bid) | text
   (caption) | card_pick | confirm (accept/decline)`, each a small declared
   field, optionally with `min/max`, `timeout_s`, `hidden_until_complete`.
3. **Collection** — not just one answer: a server-side pending-interaction
   table on `Room` keyed by interaction id, with a completion barrier
   (all-responded or timeout) before resolution continues. This is the one
   real backend change: today's design deliberately keeps *no* server-side
   pending state (the follow-up `play` re-resolves; see the bead-jcc note in
   `room.py`), which is exactly what multi-party interactions cannot do.

Wire shape: one new `ServerMsg` (`interaction_request {id, kind, audience,
prompt, fields, choices?, timeout_s?}`) and one new `ClientMsg`
(`interaction_response {id, values}`) — added **once, by humans**, and never
again per card. Resolution consumes the collected responses as data (the way
`chosen_player_id` already threads into `HookContext`), so determinism holds:
player inputs are inputs, the reducers stay pure, replay = state + the
recorded response set. `_MAX_OPS`-style caps bound fan-out (max concurrent
interactions per play, response size, timeout ceiling).

This covers bidding, trading (a two-step chain: `confirm` to the counterparty,
then `card_pick`), simultaneous secret picks, mid-game votes, and judged
free-text — the entire "No" column of §1 except the timer race, with **zero
code editing anywhere**.

### 3c. Dynamic phases

Once rules-as-data lands, a "new phase" backend-side is a string plus rules.
The only blocker is the frontend phase switch — solved by the same move:
a generic fallback branch that renders `state.phase` + the current
interaction descriptor instead of 404-ing on unknown phases.

## 4. The frontend half: minimum dynamic-UI story

The minimum story is **a generic renderer for a closed descriptor vocabulary
— data-driven UI, never code-driven UI**:

- One `InteractionPanel` component (the grown-up `TargetPickerDialog`) that
  renders `interaction_request` by `kind`: buttons for `choice`/`card_pick`
  (reusing the existing card/player chip components), a number input for
  bids, a textarea for captions, accept/decline for `confirm`, a "waiting for
  N players…" barrier state, and a countdown when `timeout_s` is set.
- Agent-defined *card attributes* (registry phase B) render generically too:
  an open `attributes` map shown as chips/badges on the card component — no
  per-attribute frontend work.
- **Forward-compatibility fallback:** an unknown `kind` renders as a labeled
  generic form (or a "this table can't render that — noted" line that
  triggers a wish note server-side). The client must never hard-fail on a
  descriptor it doesn't know; version the descriptor schema in
  `models/ws_messages.py` + `frontend/lib/types.ts` as one source of truth.

Explicitly rejected for the frontend: `eval`ing agent-shipped JS, remote
components, iframe micro-apps, module federation. The Next.js client stays a
fixed, reviewed program; all dynamism arrives as data over the socket. If a
card genuinely needs a new widget (canvas drawing), that's a wish note → a
human-merged PR (2c) that *extends the descriptor vocabulary*.

## 5. Recommendation and phased adoption

**Runtime engine-source self-modification: do not build.** The registry
covers game semantics; interaction descriptors cover interaction protocols;
reviewed PRs cover everything else on a between-games timescale. There is no
remaining niche large enough to justify collapsing the security boundary.

### Phases

1. **Implemented:** wish-note JSONL telemetry + in-character fallback line.
   `scripts/export_capability_wishes.py` supports offline human triage; no
   runtime issue-tracker or source-writing authority exists.
2. **After registry phases A–E prove out:** interaction descriptors —
   `interaction_request`/`interaction_response` envelopes, Room
   pending-interaction table with barrier + timeout, `InteractionPanel`
   generic renderer, `bid`/`confirm`/`text`/`card_pick` kinds, facade op
   `request_interaction` so sandbox snippets/hooks can ask for inputs through
   the same revalidated pipeline. *Follow-up beads: backend protocol; frontend
   renderer; facade op.*
3. **Only if wish-note telemetry demands it:** agent-authored PRs (2c) as a
   formalized loop — wish note → agent drafts patch on a branch → CI
   (layering + coverage + a new descriptor-schema-compat test) → human merge.
   Offline, never runtime.

### Do-not-build list (explicit)

- In-process hot-reload of agent code — permanently closed (§2a).
- Per-room forked/microVM engine — parked until descriptors are measurably
  insufficient; do not start speculatively (§2b).
- A code-loading plugin ABI — the descriptor schema is the plugin surface
  (§2d).
- Any frontend execution of agent-authored code (§4).
- Any mutation path that bypasses `revalidate.py` — the epic's invariant
  binds every phase of this plan too.
