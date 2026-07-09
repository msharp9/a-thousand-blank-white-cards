# Real-Card Evaluation Corpus — Annotation Guide

This directory holds an evaluation corpus of **real** (human-made) cards from the
party game *1000 Blank White Cards*, used to measure how well the interpretation
agent turns free-text cards into game effects.

## Two corpora: `real_cards.json` and `eval_cards.json`

- **`real_cards.json`** — the full Imgur album transcribed verbatim (~700 cards).
  Photos were downloaded to `images/` (see [`download_images.py`](./download_images.py))
  and transcribed by a vision model. Each record has a real `image_url`, a
  verbatim `title` + `description`, and `human_canonical: null`. This is the raw
  pool to draw annotation candidates from.
- **`eval_cards.json`** — the hand-annotated **gold** set (~35 cards) that the
  eval harness actually scores against. Each record has a filled-in
  `human_canonical` label. It has **no `image_url`**: its entries were authored
  for coverage/diversity rather than transcribed from a specific photo.

A `real_cards.json` record looks like:

```json
{
  "image_url": "https://i.imgur.com/....jpeg",
  "title": "Gain 5 Points",
  "description": "Whenever you play this card, gain 5 points.",
  "human_canonical": null
}
```

- `image_url` — where the card photo came from (for spot-checking).
- `title` / `description` — the model's verbatim transcription of the card.
- `human_canonical` — the structured interpretation of the card's intended game
  effect. Every record in `real_cards.json` is now annotated (bulk pass over the
  full album); it is no longer `null`.

The transcription is a *starting point*. Vision models mis-read handwriting,
drop lines, and hallucinate. Records should still be spot-checked against the
photo before being treated as fully trusted gold data.

## Filling in `human_canonical`

The `human_canonical` shape, every enum value, and the judgement rules (e.g.
prefer `target: "player"` over `self`, the `venue` axis for remote-vs-in-person
play) are defined in **[`CANONICAL_SPEC.md`](./CANONICAL_SPEC.md)** — that file
is the single source of truth. In brief:

```json
"human_canonical": {
  "timing": "immediate",         // immediate | modifier
  "target": "player",            // self | player | all | all_others | card | all_cards | none
  "placement": "discard",        // discard | center | player | self | destroy
  "trigger_event": "on_play",    // on_play | on_draw | on_turn_start | on_turn_end | on_score | null
  "venue": "all",                // all | in_person | online
  "magnitude_sign": "positive",  // positive | negative | neutral
  "ops": [ {"op": "add_points", "args": {"target": "player", "amount": 5}} ]
  // ...OR "snippet": "<one-sentence rule>"  (use ops OR snippet, never both)
}
```

Provide **either** `ops` (preferred, structured) **or** `snippet` (for cards that
resist structured encoding) — not both. See the spec for the full op vocabulary
and the target/placement/venue decision rules.

## How to spot-check transcriptions

1. Open the `image_url` for the record and compare it to `title` + `description`.
2. Fix any mis-read words, dropped lines, or hallucinated text directly in the
   JSON so the transcription is truly verbatim.
3. Only then write `human_canonical`, encoding the *intended* game behaviour
   (which may differ from a literal reading if the card is ambiguous — record the
   agreed interpretation).
4. If a card is illegible or unusable, delete the record rather than guess.

## Target

Aim for **30–50 fully annotated cards** (transcription verified *and*
`human_canonical` filled in). That range is enough to give the evaluation
signal without over-investing in one-off manual annotation.
