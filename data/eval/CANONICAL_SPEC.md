# `human_canonical` Annotation Spec (real_cards.json)

Every card in `real_cards.json` gets a `human_canonical` object describing how it
behaves in *1000 Blank White Cards*. Annotate from the card's `title` +
`description` (verbatim transcription). Encode the card's **intended** effect,
not a literal word-for-word reading.

## Fields

```json
"human_canonical": {
  "timing": "immediate",         // see TIMING
  "target": "player",            // see TARGET
  "placement": "discard",        // see PLACEMENT
  "trigger_event": "on_play",    // see TRIGGER_EVENT (null for pure immediate)
  "venue": "all",                // see VENUE
  "magnitude_sign": "positive",  // see MAGNITUDE_SIGN
  "ops": [ ... ]                 // see OPS  (use ops OR snippet, not both)
}
```

### TIMING — `"immediate" | "modifier"`
- `immediate` — the effect fires once when the card is played, then the card is done.
- `modifier` — the card stays in play and changes future events (a lasting rule).

### TARGET — who/what the effect primarily affects
Prefer **`player`** for anything that can be played on anyone. Only use `self`
when the card explicitly limits the effect to the person who plays it.
- `self` — only the actor (card says "you", "the player of this card", or it's
  clearly a self-only gain/loss with no choice of who).
- `player` — a chosen player (the actor picks; may be an opponent OR themselves).
  **This is the default** for point swings, effects, and challenges that any
  player could receive. When in doubt between `self` and `player`, choose `player`.
- `all` — every player, including the actor ("everyone", "all players").
- `all_others` — every player except the actor.
- `card` — another single card (e.g. "destroy a card", "steal a card in play").
- `all_cards` — a set of cards (e.g. "all cards in front of a player", "all cards
  in the middle", "everyone's in-play cards").
- `none` — no player/card target (e.g. reverse turn order, change the draw count,
  a global rule with no per-player effect).

### PLACEMENT — where the physical card goes after being played
- `discard` — resolves once and goes to the discard pile. **Most immediate cards.**
- `center` — stays in the shared table/center area as an ongoing rule (global modifiers).
- `player` — stays in front of a specific player it was played on (a modifier attached to that player).
- `self` — stays in front of the actor (a personal ongoing modifier).
- `destroy` — the card is destroyed / removed from the game entirely (not just discarded).

Rule of thumb: `immediate` → almost always `discard`; `modifier` → `center` /
`player` / `self` depending on whom the ongoing rule sits with.

### TRIGGER_EVENT — for modifiers, the event that fires the hook
`on_play` (fires when played — the default for immediates), `on_draw`,
`on_turn_start`, `on_turn_end`, `on_score`, or `null`. Immediate one-shot cards
use `"on_play"`; ongoing rules that fire on some future event name it here.

### VENUE — where the card can be played `"all" | "in_person" | "online"`
Whether the card works when players are remote or must be physically together.
Used to filter decks for remote games.
- `all` — works both remotely and in person. **The default** — most cards.
- `in_person` — requires physical presence, contact, real objects, or shared
  space: kissing/touching another player, high-fives, sharing/eating food,
  passing a physical object, standing up / moving around the room, wearing
  something, drawing on someone, physical dares.
- `online` — only makes sense in a digital/online setting (rare; e.g. references
  a chat feature, emoji, screen). Use sparingly.

Note: purely verbal/social challenges that DO work over video (sing a song,
recite a line, do an impression, answer a trivia question) are `all`, not
`in_person` — a camera is enough.

### MAGNITUDE_SIGN — `"positive" | "negative" | "neutral"`
Net effect on the target's standing: `positive` (gains points/advantage),
`negative` (loses points/disadvantage), `neutral` (no point change or a wash,
e.g. skip a turn, reverse order, a rule with no scoring).

### OPS — structured operations (use `ops` OR `snippet`, never both)
A list of `{"op": <name>, "args": {...}}`. Op vocabulary:
- `add_points` — `{"target": <TARGET>, "amount": <int, +/->}` (use a negative amount for losses)
- `steal_points` — `{"from_target": <TARGET>, "to_target": <TARGET>, "amount": <int>}`
- `set_points` — `{"target": <TARGET>, "amount": <int>}`
- `skip_turn` — `{"target": <TARGET>}`
- `extra_turn` — `{"target": <TARGET>}`
- `draw_cards` — `{"target": <TARGET>, "amount": <int>}`
- `reverse_order` — `{}`
- `change_draw_count` — `{"amount": <int>}` (new absolute draw count)
- `destroy_card` — `{"target": "card" | "all_cards"}`
- `set_win_condition` — `{"kind": "highest_points"|"lowest_points"|"first_to"|"last_standing"|"none", "threshold": <int|null>}`
- `custom_note` — `{"note": <str>}` — for flavour / table-adjudicated actions with no engine effect.

When the effect is a real-world action the engine can't execute (a dare, a
performance, a physical challenge) but it DOES change points, encode the point
change with `add_points` and add a `custom_note` describing the action. When
there is no clean structured encoding at all, use `snippet` (a one-sentence
plain-English rule) instead of `ops`.

### SNIPPET — free-text rule (alternative to `ops`)
A single sentence describing the rule when `ops` can't capture it. Example:
`"Ongoing: any player who says 'um' loses 2 points, adjudicated by the table."`
