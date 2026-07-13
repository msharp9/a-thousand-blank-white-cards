# Post-Deploy Smoke Checklist

Run this checklist after every deploy of the TBWC backend (Render) and frontend
(Vercel). It has two parts: an **automated probe** you run first, and a set of
**manual end-to-end checks** you run against the live site with two devices.

Do not announce a deploy as healthy until both parts pass.

## Prerequisites

- The backend URL (Render), e.g. `https://tbwc-backend.onrender.com`
- The frontend URL (Vercel), e.g. `https://tbwc.vercel.app`
- Two devices/browsers (laptop + phone) for the real-time checks
- A local checkout with `uv` available

## 1. Automated probe

From the repo root:

```bash
uv run python scripts/smoke_test.py \
  --backend https://tbwc-backend.onrender.com \
  --origin https://tbwc.vercel.app
```

This checks:

- **`/health`** returns `200` with `{"status": "ok"}`
- **CORS preflight** — an `OPTIONS /health` from the Vercel origin returns an
  `Access-Control-Allow-Origin` that matches the origin (or `*`)
- **WebSocket** — creates a room via `POST /rooms`, opens `wss://…/ws/{code}`,
  sends a `join`, and confirms the server replies (proving the socket is live)

The script exits `0` when all checks pass and `1` otherwise. Investigate any
`FAIL` line before continuing.

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
- [ ] **Ordered post-draw effect** — Play or author a Chess Master-style card
      that draws two cards and then scores from the resulting hand. Confirm the
      draw happens once and the score uses the post-draw hand size.
- [ ] **Rule replacement** — Play an Uno-style card and confirm draw count 0,
      empty-hand end/win rules, and any color-alignment rule appear in the
      dynamic-state panel and affect later turns.
- [ ] **Sealed auction** — Play `Going Once, Going Twice`, bid from both
      devices, and confirm no values leak before completion. Confirm the winner
      pays, receives the played card, and tied bids follow visible turn order.
- [ ] **Drawing and vote chain** — Play `Cat Show`, submit a drawing from both
      devices, and confirm the vote appears only after both submissions. Vote,
      then confirm every tied winning artist receives 3 points.
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
