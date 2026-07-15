# Post-Deploy Smoke Checklist

Run this checklist after every deploy of the TBWC backend (Render) and frontend
(Vercel). It has two parts: an **automated probe** you run first, and a set of
**manual end-to-end checks** you run against the live site with two devices.

Do not announce a deploy as healthy until both parts pass.

## Prerequisites

- The backend URL (Render), e.g. `https://a-thousand-blank-white-cards.onrender.com`
- The frontend URL (Vercel), e.g. `https://a-thousand-blank-white-cards.vercel.app`
- Two devices/browsers (laptop + phone) for the real-time checks
- A local checkout with `uv` available

## 1. Automated probe

From the repo root, pass both URLs so the probe can verify the pair deploys
together (not just each service in isolation):

```bash
uv run python scripts/smoke_test.py \
  --backend https://a-thousand-blank-white-cards.onrender.com \
  --frontend https://a-thousand-blank-white-cards.vercel.app
```

This runs the checks that are on by default and prints a pass/fail/skip
matrix:

- **`health`** — `GET /health` returns `200` with `{"status": "ok"}`
- **`cors`** — an `OPTIONS /health` from the frontend's origin (or
  `--origin`) returns an `Access-Control-Allow-Origin` that matches it (or
  `*`). Skipped if neither `--frontend` nor `--origin` is given.
- **`ws`** — creates a room via `POST /rooms`, joins via
  `POST /rooms/{code}/join`, opens `wss://…/ws/{code}`, sends a `join`
  envelope, and asserts a full `state` snapshot comes back.
- **`frontend`** — `GET` the frontend URL and confirm it returns `200` with
  the app's page title in the HTML. Only requested when `--frontend` is
  given.
- **`wiring`** — `POST /rooms` on the backend with `Origin: <frontend
  origin>` and asserts CORS allows it and a room code comes back. Combined
  with `frontend`, this proves the two services are actually deployable as a
  pair, not just independently healthy. Only requested when `--frontend` is
  given.

Skip a normally-on check with `--skip`, e.g. `--skip cors,ws`.

Three more checks are opt-in because they spend third-party quota or hit a
paid API — pass the flag to run them:

```bash
uv run python scripts/smoke_test.py --backend https://a-thousand-blank-white-cards.onrender.com \
  --check-tavily --check-langsmith --check-llm
```

- **`tavily`** (`--check-tavily`) — `tavily_api_key` is configured and a live
  search through `agent.tools.web_search` returns a real result (not the
  "web search unavailable" fallback).
- **`langsmith`** (`--check-langsmith`) — `langsmith_tracing` +
  `langsmith_api_key` are configured and a cheap authenticated call to the
  LangSmith API succeeds.
- **`llm`** (`--check-llm`) — a one-token chat completion succeeds through
  `agent.llm.get_chat_model`, proving the configured LLM gateway/provider is
  reachable and the key is valid.

The script exits `0` iff every REQUESTED check passes — skipped checks (not
requested, or explicitly `--skip`ped) never affect the exit code. Investigate
any `FAIL` line before continuing.

> Note: Render free-tier services cold-start. If the first run times out, wait
> ~30–60s for the service to wake and re-run.

## 2. Manual end-to-end checks

Do these against the **live Vercel URL** with two devices.

- [ ] **Load** — Open the Vercel URL on the laptop. The landing page renders with
      no console errors.
- [ ] **Phone load** — Open the same URL on the phone. It renders and can reach
      the backend (no CORS or mixed-content errors).
- [ ] **Create room** — On the laptop, create a new room. A join code is shown.
- [ ] **Join by code** — On the phone, join using that code. Both devices now
      show both players in the room.
- [ ] **Real-time state sync** — Take an action on one device (e.g. change name,
      ready up, advance phase) and confirm the other device updates within a
      second or two without a manual refresh.
- [ ] **Play a card** — Play a card from a hand and confirm the **effect log**
      updates on **both** devices with the resulting effect.
- [ ] **Author a wild card** — Write a new custom (wild) card and submit it.
      Confirm the **brewing indicator** appears while the agent interprets it,
      then a **verdict** is returned and applied. The card's effect should be
      reflected in game state / the effect log.
- [ ] **Reconnect** — Refresh one device mid-game and confirm it reconnects to
      the same room and restores state (no lost session).
The next four checks exercise specific card behaviors. What matters is the
card's **description** — that is what the agent interprets; the title is
flavor. Author a wild card with the description given (or play the matching
seed card if you happen to draw it).

- [ ] **Ordered post-draw effect** — Author a card described like: *"Draw two
      cards, then gain a point for each card in your hand."* Confirm the draw
      happens once and the score uses the post-draw hand size.
- [ ] **Rule replacement** — Author a card described like: *"Use Uno's
      empty-hand ending and zero-draw rule. Track the color of each played
      card and reject plays that do not match the current color."* (seed deck:
      "Wild Uno"). Confirm draw count 0, the empty-hand end/win rules, and the
      color-alignment rule appear in the dynamic-state panel and affect later
      turns.
- [ ] **Sealed auction** — Author a card described like: *"Everyone secretly
      bids points for this card. The highest bidder pays their bid and takes
      this card into their hand. Ties go to the earliest player in turn
      order."* (seed deck: "Going Once, Going Twice"). Bid from both devices
      and confirm no values leak before completion. Confirm the winner pays,
      receives the played card, and tied bids follow visible turn order.
- [ ] **Drawing and vote chain** — Author a card described like: *"Everyone
      draws a cat, then everyone votes for the best cat. The artist with the
      most votes gains 3 points; ties all score."* (seed deck: "Cat Show").
      Submit a drawing from both devices and confirm the vote appears only
      after both submissions. Vote, then confirm every tied winning artist
      receives 3 points.
- [ ] **Reconnect during interaction** — Refresh one device after submitting a
      sealed bid or drawing. Confirm it returns to the same barrier marked as
      submitted without revealing values, then complete the interaction from
      the other device exactly once.
- [ ] **Epilogue + vote** — Play through to the epilogue and confirm the voting
      UI appears and a vote can be cast and recorded on both devices.

## If something fails

- Re-run the automated probe to isolate backend vs. frontend.
- Check the Render logs for backend errors and Vercel logs for build/runtime
  errors.
- Confirm the frontend's configured backend URL and the backend's `CORS_ORIGINS`
  include the current Vercel URL.
- File an issue with `bd` capturing the failing step and any logs.
