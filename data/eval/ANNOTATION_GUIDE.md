# Real-Card Evaluation Corpus — Annotation Guide

This directory holds an evaluation corpus of **real** (human-made) cards from the
party game *1000 Blank White Cards*, used to measure how well the interpretation
agent turns free-text cards into game effects.

## Three corpora

- **`real_cards.json`** — the full Imgur album transcribed verbatim (~700 cards).
  Photos were downloaded to `images/` (see [`download_images.py`](./download_images.py))
  and transcribed by a vision model. Each record has a real `image_url`, a
  verbatim `title` + `description`, an `alt_text` (the art description, split
  out of the old bracketed description prefix), and a filled `human_canonical`.
- **`eval_cards.json`** — the hand-annotated **gold** set (~35 cards) the eval
  harness scores against. No `image_url`: entries were authored for
  coverage/diversity rather than transcribed from a specific photo.
- **`eval_cards_hard.json`** — the **hard** set (~25 cards): deliberately
  compositional effects (`ops: null`, sandbox-only) that stretch the agent —
  hand/deck inspection, alt_text queries, hooks, reactions, multi-step
  conditionals.

A `real_cards.json` record looks like:

```json
{
  "image_url": "https://i.imgur.com/....jpeg",
  "title": "Gain 5 Points",
  "description": "Whenever you play this card, gain 5 points.",
  "alt_text": "a hand-drawn number five with sparkles",
  "human_canonical": { ... }
}
```

The transcription is a *starting point*. Vision models mis-read handwriting,
drop lines, and hallucinate. Records should still be spot-checked against the
photo before being treated as fully trusted gold data.

## Alt text conventions

`alt_text` describes the card's **art**, not its rule text. It came from the
bracketed `[drawing of ...]` prefixes in the original transcriptions; the
`description` no longer contains them.

- Write what is drawn, concretely: `"a cereal box labeled POPS, a bowl of
  cereal with a spoon, and a hand holding a gun"`.
- Drop the leading "drawing of" boilerplate when writing new alt text; keep
  content words (nouns matter — other cards query alt text: "double points for
  cards with monkeys").
- `null` when the card has no art or nothing describable.

## Filling in `human_canonical`

The canonical shape, every enum value, and the judgement rules are defined in
**[`CANONICAL_SPEC.md`](./CANONICAL_SPEC.md)** — the single source of truth.
In brief:

```json
"human_canonical": {
  "target": "player",            // self | player | all | all_others | card | all_cards | none
  "placement": "discard",        // discard | center | player
  "trigger": null,               // null | GameEvent value | "on_reaction"
  "venue": "all",                // all | in_person | online  (required)
  "magnitude_sign": "positive",  // positive | negative | neutral (eval label)
  "ops": [ {"op": "add_points", "args": {"target": "player", "amount": 5}} ],
  "sandbox": "def apply(state, ctx):\n    state.add_points(\"chooser\", 5)"
}
```

Rules of thumb:

- **Both `ops` and `sandbox`, always.** `sandbox` is executable
  `def apply(state, ctx)` code (validated by `engine.sandbox.validate`), never
  prose. `ops` is `null` only when the effect genuinely can't be expressed as
  structured ops — then sandbox carries the whole effect.
- Sandbox style: runtime target strings (`"self"`, `"chooser"`, `"all"`,
  `"id:<player_id>"`); `state.subtract_points` for losses; `state.note(...)`
  for the table-adjudicated part of dares; defensive `ctx.get(...)` reads.
- One-shot cards: `placement: "discard"`, `trigger: null`. Persistent
  modifiers: `center` (game-wide) or `player` (attached to one player), with
  `trigger` naming the event that re-fires them. Reactions: `discard` +
  `"on_reaction"`.

## How to spot-check transcriptions

1. Open the `image_url` and compare it to `title` + `description` + `alt_text`.
2. Fix mis-read words, dropped lines, or hallucinated text directly in the JSON.
3. Only then write `human_canonical`, encoding the *intended* game behaviour
   (record the agreed interpretation when the card is ambiguous).
4. If a card is illegible or unusable, delete the record rather than guess.
