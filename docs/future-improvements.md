# Future improvements — frontend interactions for card effects

Ideas mined from the real card corpus (`data/eval/real_cards.json`, ~700
transcribed physical cards) for interactions the frontend could support so
more card effects become mechanically real instead of honor-system
`custom_note`s. A list only — none of these are scheduled.

## Already shipped (don't rebuild)

The generic interaction protocol (`docs/dynamic-card-resolution.md`,
`InteractionPanel`) already covers what used to be the biggest gaps:

- **Countdown timers with a deadline** — every interaction barrier has
  `timeout_seconds` (10–300s) and a client countdown.
- **Voting / majority decisions** — `choice` steps with `audience: "all"`,
  chainable via `input_refs` (vote on submitted drawings: see "Cat Show").
- **Sealed bids / auctions** — `number` steps with `sealed: true`
  ("Going Once, Going Twice").
- **Free-text answers** — `text` steps (trivia, "name a movie" cards).
- **Drawing input mid-effect** — `drawing` steps with stroke capture.
- **Confirm/decline prompts** — `confirm` steps.
- **Reaction windows** — counterspell-type interrupts when another player
  plays a card (`ReactionWindow`, trigger `on_reaction`).

## Still missing

- **Timed physical dares with self-verification** — "pour someone a glass of
  milk in 30 seconds or lose 1000" (the `Milk` card): a countdown overlay plus
  a did-it / failed-it button pair whose outcome picks between two op sets.
  Today the timeout defaults an answer; a dare wants an explicit
  succeed/fail branch and the point swing attached to each.
- **Table verify/approve button** — honor-system dares ("eat a handful of
  cereal", "sing the anthem with all the correct words" with tiered scoring):
  let the OTHER players confirm the performance before points apply — a
  confirm step aimed at everyone but the performer, majority-carries.
- **Camera / photo proof** — remote games lose all `in_person` cards (152 of
  698). A capture-and-share step (photo or short clip riding the same
  out-of-band path as card art) would let many physical dares work over video.
- **Dice / coin-flip RNG widget** — many cards want visible randomness
  ("flip a coin: heads +500, tails -500"). Sandbox code is deliberately
  deterministic (no random access), so chance needs a server-rolled, animated
  RNG interaction step whose result lands in `ctx["interactions"]`.
- **Chat / say-it-out-loud surfacing** — "tell the player to your right your
  views on X": a prompt overlay directed at a specific player (`player:<id>`
  audience exists; a display-only "do this now" toast variant does not — the
  current steps all demand an input back).
- **Alt-text accessibility display** — `alt_text` is now first-class on every
  card; the frontend should render it (tooltip/aria) both for accessibility
  and so players can see the art descriptions that art-querying cards
  ("double points for cards with monkeys") match against.
- **Reaction-window polish** — grey-out timer sync across clients, a "who can
  still react" indicator (server knows `eligible_ids`; deliberately not
  broadcast today for hand privacy — could show counts instead of names),
  and blanks playable as reactions (author-on-react).
- **Per-player modifier badges** — `placement: "player"` cards render in the
  in-play strip, but conditions written by `set_condition` (poisoned, cursed,
  untargetable) have no visual; a badge row on the player avatar would make
  "the player is poisoned"-type modifiers legible.
- **Spectator participation votes** — spectators can watch but not act;
  audience-vote cards ("the table decides who wore it better") could
  optionally include them as tie-breakers.
