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
- `human_canonical` — **starts `null`**; a human fills this in (see below), and
  the reviewed, labelled record graduates into `eval_cards.json`.

The transcription is a *starting point*. Vision models mis-read handwriting,
drop lines, and hallucinate. Every record needs human review before it counts as
gold data.

## Filling in `human_canonical`

For each card, a human replaces `null` with the canonical, agreed-upon
interpretation of what the card *should* do in the game. Use this shape:

```json
"human_canonical": {
  "timing": "on_play",        // when the effect fires (e.g. on_play, on_draw, continuous, end_of_turn)
  "target": "self",           // who/what it affects (e.g. self, opponent, all_players, a_card)
  "placement": "discard",     // where the card/tokens end up (e.g. discard, in_play, removed)
  "trigger": "when played",   // the condition, in plain words, that activates the effect
  "ops": [                    // structured operations the engine should perform...
    {"op": "add_points", "who": "self", "amount": 5}
  ]
  // ...OR, when the card is too freeform for structured ops, use a code snippet:
  // "snippet": "state.players[actor].score += 5"
}
```

Provide **either** `ops` (preferred, structured) **or** `snippet` (for cards that
resist structured encoding) — not both. Keep `timing`, `target`, `placement`,
and `trigger` filled in for every card; they describe intent even when `ops`
can't fully capture it.

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
