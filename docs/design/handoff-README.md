
# Handoff: 1000 Blank White Cards — Digital Tabletop

## Overview
A front end for playing the party card game **1000 Blank White Cards** as a shared digital tabletop. Players create cards on the fly, play them to the table (to everyone, to a specific player, or straight to discard), draw from the deck, track a running play log, keep score, and run an end-of-game "epilogue" to decide which cards survive. The backend (card effects, scoring rules, turn logic, real multiplayer) already exists and is out of scope — this package is the **front end only**.

## About the Design Files
The files in this bundle are **design references created in HTML** — working prototypes that show the intended look, layout, and interaction behavior. They are **not production code to copy directly**.

They are authored as "Design Components" (a small streaming-template runtime; see `support.js`), which is **not** a real production framework. Your job is to **recreate these designs in the target codebase's existing environment** (React, Vue, Svelte, SwiftUI, etc.) using its established patterns, component library, and state management. If no front end exists yet, pick the most appropriate framework for the project and implement there. Treat the HTML/JS as a precise spec of appearance and behavior, not as source to port line-by-line.

## Fidelity
**High-fidelity (hifi).** Final colors, typography, spacing, and interactions are all intentional. Recreate the UI pixel-closely using your codebase's libraries and patterns. The one deliberately scrappy element is the hand-drawn aesthetic — that is the point of this game, not an accident.

## Visual System

### Concept
"Sketchbook Tabletop." Warm paper surface; every game card is a pure-white index card with an inked border, a dashed inner border, and translucent tape at the top corners, slightly rotated. Type mixes a marker font, a handwriting font, and a clean sans for UI chrome. Buttons use a "sticker" style: 2–2.5px black border, hard offset shadow (`box-shadow: 4px 4px 0 #1a1a1a`), and a press effect that translates the button toward the shadow.

### Color tokens
| Token | Hex | Use |
|---|---|---|
| Paper background | `#F2EFE6` | App background (with a dot grid, see below) |
| Panel paper | `#efe9da` | Play-log strip background |
| Card white | `#ffffff` | All cards, panels, top bar |
| Ink | `#1a1a1a` | All borders, primary text |
| Marker red | `#E24A3B` | Primary action, accent, links, positive-emphasis |
| Ballpoint blue | `#2E5EAA` | Secondary accent |
| Marker green | `#1F9E6B` | Confirm / "keep" / save |
| Highlighter yellow | `#F5D547` | Draw button, target highlight |
| Amber (players) | `#E8A33D` | 4th player color, first-place rank |
| Felt green (default) | `#2f7d5b` | Table surface — tweakable: `#8a4a3b`, `#3a4a7d`, `#4a4a4a` |
| Tape | `rgba(238,214,120,0.6)` | Card tape strips |
| Muted text | `#666` / `#888` / `#999` | Secondary / tertiary labels |

Player identity colors: Maya `#E24A3B`, Theo `#2E5EAA`, Iris `#1F9E6B`, Sol `#E8A33D`.

Link styling: `a { color:#E24A3B }`, `a:hover { color:#b83527 }`.

### Typography (Google Fonts)
- **Permanent Marker** — logo, big display headings, points/score numbers, section stamps.
- **Patrick Hand** — card titles, card rule text, handwritten UI labels, player names.
- **Nunito** (400/600/700/800/900) — buttons, body copy, meta text, numeric captions.

Paper dot-grid background:
```css
background-color:#F2EFE6;
background-image: radial-gradient(rgba(26,26,26,0.05) 1px, transparent 1px);
background-size: 22px 22px;
```

### Keyframe animations
- `floaty` — 5s ease-in-out infinite; gentle translateY(-10px) + slight rotate. Home hero cards (staggered 0 / 0.6s / 1.2s delays).
- `popin` — 0.35s ease-out; scale 0.6→1 + rotate -8→0 + fade. Cards entering the table; target chooser (0.2s).

### Design tokens — spacing / radius / shadow
- Card border-radius: `7px`; inner dashed border inset `4px`.
- Panel/button radius: `10–16px`; modal `18px`.
- Card border: `1.6px solid #1a1a1a`; panels `2.5px`; table felt `3px`.
- Card shadow: `0 8px 18px rgba(20,18,14,0.20), 0 2px 4px rgba(20,18,14,0.14)`.
- Sticker-button shadow: `4px 4px 0 #1a1a1a` (or `3px 3px 0`); press → `2px 2px 0` + `translate(1px,1px)`.
- Panel shadow: `4px 4px 0 rgba(26,26,26,0.1–0.12)`.

## The Card Component (shared across every screen)
A reusable card renders in two faces and at many sizes.

**Props:** `title`, `rule`, `emoji`, `img` (optional data-URL artwork), `faceDown` (bool), `showTape` (bool), `w` (px), `h` (px ≈ w×1.4), `rot` (deg).

**Front face** (top→bottom, flex column, padding ≈ w×0.08):
- Title: Patrick Hand, size ≈ w×0.115, centered, single line ellipsis, `border-bottom:1.5px solid #1a1a1a`.
- Art area (flex:1, centered): the `img` if present, otherwise the `emoji` at ≈ w×0.4.
- Rule text: size ≈ max(9, w×0.082), centered, clamped to ≈ h×0.26 tall.

**Back face:** diagonal hatch `repeating-linear-gradient(45deg, #fbfbf9 0 9px, #f2f0e9 9px 18px)`, with a centered dashed circle (rotated -6deg) reading "1K" (Permanent Marker) over "BLANK WHITE" (Patrick Hand).

**Tape:** two strips at top ~14% / ~86%, `rgba(238,214,120,0.6)`, ~16px tall, rotated ±6–7deg.

> Note: an earlier version had a colored corner **points badge**; it was intentionally **removed** per review. Points/effects now live only in the rule text. Do not reintroduce a numeric badge on cards.

Card sizes in use: hand 130×182 · table center 126×176 · opponent/you "in front" 52×73 / 56×78 · deck 92×128 · discard 80×112 · gallery 164×230 · epilogue 160×224 · play-log mini 30×42 · history-modal 52×73 · home hero 120×168 · opponent face-down fan 40×56.

## Screens / Views
Single-page app with a router (`screen` state) and a persistent top bar (except on Home).

### Top bar (all screens except Home)
Sticky, white, `border-bottom:2.5px solid #1a1a1a`. Left: "1KBWC" logo (Permanent Marker, click → Home). Nav tabs: **Table, Create, Gallery, Scores, Epilogue** — active tab is filled black (`#1a1a1a` bg, white text), others white with black border. Right: "Turn N" label (Patrick Hand).

### 1. Home
Full-viewport centered hero. Three floating hero cards overlapped (negative margins, `floaty` animation): a truly blank card ("Blank."), "Free Point! 🎉", "Meow. 🐱". Faint rotated emoji doodles in the four corners (✏️ 🎲 🦆 🌋, opacity 0.5). Title "1000 Blank / White Cards" (Permanent Marker, `clamp(38px,7vw,84px)`). Tagline (Patrick Hand). Two sticker buttons: **Play Now** (red → Table) and **Make a Card** (white → Create). A 3-step "how to play" row beneath.

### 2. Play Table (hero screen)
Vertical stack:
- **Opponents row** (top, centered, wrapping): one panel per non-active player. Panel = translucent white, `2px dashed` in the player color. Header: avatar circle (player color, 34px), name (Patrick Hand), score (Permanent Marker, player color). Below: face-down hand fan (40×56 cards, overlapped -22px, rotated by index). If the player has cards **in front of them**, a dashed-top-bordered row shows "in front:" + those small face-up cards (52×73).
- **Felt table** (flex:1, rounded 22px, felt color, `border:3px`, inset shadow): split into
  - **Center zone** (flex:1) — label "◆ AFFECTS EVERYONE ◆" (Permanent Marker, translucent white), holds cards that affect all players (126×176) each with a "by <name>" tab. Empty state: dashed box "Nothing in play for everyone…".
  - **Deck/discard dock** (right, darker inset column, `border-left:2px dashed`): stacked face-down deck (three 92×128 cards, slight rotations) + "Deck · N"; **Draw a Card** (yellow) and **Draw a Blank** (white) buttons; clickable **discard** card (80×112) labeled "Discard · tap for log".
- **Your zone** (white, top-bordered): header with your avatar/name ("· your turn")/score on the left and **End Turn ⟳** on the right. When a hand card is selected, a **target chooser** bar appears (cream, `2px dashed`, `popin`): "Play "<title>" to:" followed by buttons **Everyone** (👥, yellow), one per player (avatar emoji + name, player-color bg, white text), **Discard** (🗑, white), and **Cancel**. Below, an optional "In front of you:" row of your own in-front cards. Then your **hand fan** (130×182 cards, overlapped -34px, rotated by index; selected card lifts translateY(-34px), unselected dim to opacity 0.55; hover lifts translateY(-24px) and straightens).
- **Play Log strip** (bottom, `#efe9da`, top-bordered): "Play Log" label + horizontal-scroll list of plays, **newest first**. Each entry: mini card (30×42) + "**<by>** → <target>" over "<turn> · <title>". Empty state: "No cards played yet."

**History modal** (opens on discard tap): fixed full-screen scrim `rgba(20,18,14,0.55)`; centered card-styled panel (`border:3px`, radius 18, `box-shadow:8px 8px 0`). Header "Everything Played" (Permanent Marker) + ✕ close. Scrollable list of every play: 52×73 card + title (Patrick Hand) + rule (Nunito) + right-aligned "**<by>** → <target> / <turn>". Click scrim or ✕ to close.

### 3. Card Creator Studio (Create)
Three columns, centered, wrapping:
- **Left toolbar** (white panel): "Ink" — five round color swatches (Ink `#1a1a1a`, Red, Blue, Green, Yellow); active swatch has a 3px red ring. "Nib" — three sizes (2.5 / 5 / 9px shown as a growing bar); active ringed. Undo (↺) and Clear (🗑) buttons.
- **Center card** (400px wide white card with tape + dashed inset): a **title `<input>`** (Patrick Hand 26px, bottom border) at top; a **freehand drawing `<canvas>`** (360×300, `cursor:crosshair`) as the art body with a "draw something ✏️" placeholder when empty; a **rule `<textarea>`** below ("What does this card DO?"). Caption: "This card joins your hand + the shared deck."
- **Right panel**: "Stamps" — a 5-column emoji grid; tap a stamp to arm it, then tap the canvas to place it (active stamp highlighted yellow). **✓ Add to Deck** (green sticker button) and **Start Over**.

> The creator has **no points control** — removed per review; effects are described in the rule text only.

### 4. Gallery (The Deck)
Centered max-width 1100. Header "The Deck" (Permanent Marker) + "N cards invented so far" + **+ New Card** button (→ Create). Wrapping flex of all cards (164×230), each slightly rotated (straightens + scales 1.04 on hover), with a "by <name>" caption.

### 5. Scoreboard (Scores)
Centered max-width 720. "Scoreboard" title + subtitle. One row per player, **sorted by score desc**, each a white card-panel (slight rotation): rank "#N" (Permanent Marker; first place amber, rest grey), avatar circle, name + medal (🥇🥈🥉), a progress bar (`height:10px`, black border, filled to score/max in player color), and the score (Permanent Marker 34px, player color).

### 6. Epilogue
Centered max-width 1000. "The Epilogue" title + subtitle. A counter line: "<n> kept · <n> cut · <n> to decide". Wrapping grid of all cards (160×224); under each, **Keep** and **Cut** buttons. Keep selected → green fill; Cut selected → red fill and the card dims (opacity 0.35) + `grayscale(0.9)`. Clicking the active choice again clears it.

## Interactions & Behavior
- **Navigation:** top-bar tabs and logo set the current screen. Home buttons route to Table / Create.
- **Select a hand card:** click toggles selection; selected lifts and others dim; the target chooser appears.
- **Play a card:** clicking a target button removes it from hand, appends it to the chosen destination (center / that player's in-front list / discard = top of discard), pushes an entry to the play log (`{by, target, turn}`, newest first), and clears the selection. Center/in-front cards get a random rotation −6…+6deg and a `popin` entrance.
- **Cancel:** clears selection.
- **Draw a Card:** decrements deck count, adds a random library card to your hand.
- **Draw a Blank:** clears the canvas draft and routes to the Creator (this is the game's signature "you drew a blank, invent a card" moment).
- **End Turn:** advances the active player index (mod player count), increments the turn counter, clears selection. (The active player is the one whose hand is shown at the bottom; opponents are everyone else.)
- **Canvas drawing:** pointer down/move/up draw polyline strokes in the current ink + nib; strokes stored as an array for **Undo** (pop last) and **Clear** (empty). An armed **stamp** places an emoji at the click point instead of drawing. Map pointer coords to canvas space via `getBoundingClientRect` scale; use `touch-action:none` and `pointercancel/leave` to end strokes.
- **Save card:** captures the canvas to a data-URL as the card's `img` (only if anything was drawn), builds a card `{id, title (or "Untitled"), rule, img, by: activePlayer}`, appends it to the library, the gallery order, and the active player's hand, then routes to Table. If nothing was drawn, the card falls back to a ✏️ emoji.
- **Discard tap:** opens the history modal. Scrim/✕ closes.
- **Epilogue keep/cut:** toggles a per-card decision; updates the counter and card treatment.
- **Hover states:** sticker buttons press toward their shadow; hand cards lift; gallery cards straighten + scale; discard lifts slightly.

## State Management
Core state (all client-side in the prototype; wire to your real backend):
- `screen` — `'home' | 'table' | 'creator' | 'gallery' | 'scoreboard' | 'epilogue'`.
- `turn` (number), `currentIdx` (active player index).
- `players` — `[{ id, name, emoji, color, score }]`.
- `library` — all card objects `{ id, title, rule, emoji, img?, by }`; `order` — array of card ids for the gallery.
- `hands` — `{ [playerId]: cardId[] }`.
- `center` — `[{ id, by, rot }]` (affects everyone); `fronts` — `{ [playerId]: [{ id, by, rot }] }` (single-player effects); `discardId` (top of discard).
- `history` — `[{ id, by, target, t }]`, newest first.
- `selectedId`, `showHistory` (modal).
- Creator draft: `draft {title, rule}`, `brush` (ink color), `nib` (size), `stampEmoji`, plus a non-state stroke array on the canvas controller; `strokeCount` drives the empty-canvas placeholder.

Data fetching / real logic to connect: card effect resolution, authoritative scoring, turn order (incl. reversals), deck contents, and multiplayer sync — all handled by your existing backend. The front end only needs to render server state and emit the actions above (select/play-to-target, draw, draw-blank, end-turn, create-card, keep/cut).

## Assets
- **Fonts:** Google Fonts — Permanent Marker, Patrick Hand, Nunito. Swap for your app's equivalent hand-drawn + sans pairing if brand requires.
- **Icons/art:** native emoji only (avatars, card art, stamps, decorative doodles) — no image files. User-drawn card art is generated at runtime as a PNG data-URL from the canvas.
- No external image or icon assets are required.

## Files
- `1000 Blank White Cards.dc.html` — the full app (all six screens, router, state, canvas logic). The `<script>` block at the bottom holds seed data (`SEED` cards, `PLAYERS`, pen colors, nib sizes, stamp set) and the component logic.
- `Card.dc.html` — the reusable card component (front/back faces, sizing math, tape, artwork).
- `support.js` — the Design-Component runtime that renders the above. **Reference only** — do not port it; it is replaced by your framework's rendering.

Open either HTML file directly in a browser to see the live prototype.

## Dark Mode — "Night Table" (implemented variant)

The production frontend adds a dark variant of the sketchbook palette: the same
paper-and-marker language in a dark room. Physical paper artifacts — every card
face, the card back, and the drawing canvas — **stay white** in dark mode, like
real cards on a dark table; only the room around them darkens. Toggled by a
`dark` class on `<html>` (sun/moon sticker button, persisted in
`localStorage["tbwc_theme"]`, initial value falls back to
`prefers-color-scheme`, applied pre-paint by an inline script in
`app/layout.tsx`).

### Dark color tokens (`.dark` in `frontend/app/globals.css`)

| Token | Light | Dark | Notes |
|---|---|---|---|
| Background | `#f2efe6` | `#1e1b16` | near-black warm paper; dot grid `rgba(236,229,211,0.06)` |
| Ink / foreground | `#1a1a1a` | `#ece5d3` | cream ink for borders + text on dark surfaces |
| Card / panel | `#ffffff` | `#27231c` | UI panels; **card faces stay `#ffffff`** (`--card-face`) |
| Panel paper | `#efe9da` | `#2b261e` | play-log strip, chips, target chooser |
| Felt | `#2f7d5b` | `#1d4f39` | deep night green |
| Primary / marker red | `#e24a3b` | `#e6553f` | ≥3:1 on dark panels; white button text |
| Secondary / ballpoint blue | `#2e5eaa` | `#4e7ecd` | lifted for contrast on dark |
| Marker green | `#1f9e6b` | `#2ab37c` | |
| Amber | `#e8a33d` | `#ecac49` | |
| Highlighter yellow (accent) | `#f5d547` | `#f5d547` | already pops on dark; ink text |
| Muted / muted-foreground | `#efe9da` / `#666` | `#332d23` / `#a89e8b` | |
| Tape | `rgba(238,214,120,0.6)` | `rgba(238,214,120,0.55)` | sits on white card faces in both themes |
| Sticker shadow | `#1a1a1a` | `rgba(0,0,0,0.9)` | hard offset shadow still reads on dark panels |
| Panel shadow | `rgba(26,26,26,0.11)` | `rgba(0,0,0,0.45)` | |
| Player identity | `#E24A3B` / `#2E5EAA` / `#1F9E6B` / `#E8A33D` | `#e8564a` / `#4e7ecd` / `#2ab37c` / `#ecac49` | via `--player-0..3`; `playerColor()` returns the var so identities track the theme |

Drawn card art is ink strokes exported onto a white PNG background, so it stays
legible in both themes because card faces never darken. The `paper-scope`
utility re-scopes ink tokens to light-paper values inside white card faces
(e.g. the card creator).
