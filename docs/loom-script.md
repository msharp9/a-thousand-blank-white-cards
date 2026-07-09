# Loom Demo Script — 1000 Blank White Cards

A timed script + shot checklist for a **≤ 10-minute** Loom walkthrough of the
project. Segments below sum to exactly 10:00. Each segment has a **time budget**,
a **talking track** (what to say), and a **shot checklist** (what to have on
screen). Read the talking track as a guide, not a teleprompter — sound like a
person, not a narrator.

> **What we're demoing:** *1000 Blank White Cards* — a realtime multiplayer party
> card game where players invent free-text cards and an AI referee (a LangGraph
> agent with RAG + a code sandbox) interprets them into executable game effects.

---

## Pre-flight checklist

Do **all** of this *before* you hit record. A cold start on camera kills momentum.

**Backend**
- [ ] `.env` filled in from `.env.example` — `OPENAI_API_KEY` set, and for the
      under-the-hood segment set `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY`
      + `LANGSMITH_PROJECT=tbwc-dev`.
- [ ] `TAVILY_API_KEY` set (the agent's web-search tool uses it for pop-culture cards).
- [ ] Qdrant reachable at `QDRANT_URL` (local Docker: `docker run -p 6333:6333 qdrant/qdrant`),
      and the card corpus seeded into the `tbwc_cards` collection.
- [ ] `SNIPPET_EXECUTION_ENABLED=true` so the sandbox path is live.
- [ ] Backend running: `uv run uvicorn tbwc.app:app --reload --port 8000`
      (or your deployed URL — have it warm, hit it once so it's not cold).
- [ ] Do one throwaway card interpretation now to warm caches / connections.

**Frontend**
- [ ] Frontend up: `cd frontend && npm run dev` (Next.js on `http://localhost:3000`),
      or your deployed Vercel URL. Confirm it points at the right backend.
- [ ] Landing page loads clean; no console errors.

**Two devices / realtime**
- [ ] Laptop = "host". Second device ready = a **phone** on the same network (best
      visual) or a **second browser tab / incognito window** (simplest, always works).
- [ ] Know how to make the phone reach the laptop backend (same LAN, or use the
      deployed URL for both).

**Content prepped**
- [ ] 2–3 pre-written **wild cards** copied somewhere you can paste from (see below).
- [ ] Player names decided (short, readable on camera — e.g. `Ada`, `Grace`).

**Observability + eval**
- [ ] LangSmith project open in a tab, filtered to `tbwc-dev`, ready to refresh.
- [ ] A terminal open in the repo root for the eval harness segment.
- [ ] `src/tbwc/evals/RETRIEVER_ANALYSIS.md` open in a tab (the A/B + few-shot tables).

**Recording hygiene**
- [ ] Close Slack/email/notifications. Full-screen browser. Zoom the UI up one notch.
- [ ] Loom set to "screen + cam bubble". Mic tested.

### Pre-written wild cards to demo

Pick one "scores" card and one "house rule" card. Suggestions:

| # | Card text | Why it's a good demo |
|---|-----------|----------------------|
| 1 | *"Anyone wearing glasses gains 3 points. If you interpret this card, you lose 1 point."* | Conditional + self-referential; shows targeting + point ops. |
| 2 | *"Summon the Kraken: the player with the most points must give half their points (rounded down) to the player with the fewest."* | Absurd + math; shows the code-snippet path (gen_snippet → sandbox). |
| 3 | *"HOUSE RULE: from now on, everyone must speak in pirate voice or lose a point each turn."* | Becomes a persistent center-zone house rule, not a one-shot. |
| 4 | *"Taylor Swift walks in — every player named after a musician draws 2 cards."* | Pop-culture → triggers the web-search/retrieval path. |

> Use **#1 or #2** for the "scores change" moment and **#3** for the house-rule moment.

### Backup plan (READ THIS)

The live agent takes a few seconds per card, and it depends on OpenAI/Tavily/Qdrant
being up. If any of that is slow or flaky on the day:
- [ ] Have a **pre-recorded clip** of a successful card interpretation (brewing →
      verdict → effect log) ready to drop in.
- [ ] Have a **local run** ready as fallback (backend + frontend on localhost, not
      the deployed URL) so you're not at the mercy of a cold serverless host.
- [ ] Have a **LangSmith trace from an earlier run** bookmarked, so the under-the-hood
      segment works even if the live call is slow.
- [ ] If a card genuinely errors on camera: stay calm, say "the validator caught
      that — it refuses to emit unsafe or invalid effects," and move to the next
      pre-written card. That failure mode is actually a selling point.

---

## Segment map (sums to 10:00)

| # | Time | Segment |
|---|------|---------|
| 1 | 0:00–0:45 | Hook & problem |
| 2 | 0:45–1:30 | Solution overview + architecture |
| 3 | 1:30–3:30 | Live demo pt 1 — create/join room, realtime sync, start |
| 4 | 3:30–6:00 | Live demo pt 2 — the AI magic (wild card + house rule) |
| 5 | 6:00–7:30 | Under the hood — graph, trace, sandbox, RAG |
| 6 | 7:30–9:00 | Evals — harness, 35-card testset, judge, A/B tables |
| 7 | 9:00–10:00 | Wrap — what's next + links |

---

## 1 · Hook & problem — (0:00–0:45)

**Talking track**
> "This is *1000 Blank White Cards* — a party game where the entire deck starts
> blank. Players draw cards, write their own rules on them in real time, and play
> them. It's hilarious and chaotic. But there's one thing that always kills the
> vibe: **someone writes a ridiculous card, and then everyone stops to argue about
> what it actually *means* and how it changes the score.** Manual rules arbitration
> is the bottleneck. So I built an AI referee that reads any free-text card and
> just... resolves it."

**Shot checklist**
- [ ] Open on the landing page (hero + game name visible).
- [ ] Optional: a 1-second glimpse of a physical/handwritten blank card image if you have one.
- [ ] Cam bubble on — this is the human hook; make eye contact.

---

## 2 · Solution overview + architecture — (0:45–1:30)

**Talking track**
> "The referee is a **LangGraph agent**. When a card is played, it runs a pipeline:
> it reasons about the card, retrieves similar example cards from a vector store,
> optionally does a web search for pop-culture references, classifies the card, and
> then either **emits structured game operations** or, for weird cards, **generates
> a little code snippet** that's validated and run in a **sandbox**. Everything is
> traced in LangSmith. Frontend's a Next.js app talking to a FastAPI backend over
> **WebSockets** for realtime state."

**Shot checklist**
- [ ] Show the architecture diagram from `WRITEUP.md` (or the README) — front and center.
- [ ] Trace the pipeline with your cursor as you name the nodes:
      **reason → retrieve → (route) search → classify → emit_ops / gen_snippet → validate → judge**.
- [ ] Keep it to ~45s. Don't rabbit-hole here; the payoff is the live demo.

---

## 3 · Live demo pt 1 — create/join room, realtime sync — (1:30–3:30)

**Talking track**
> "Let's play. I'll create a room on my laptop..." *(create)* "...and it gives me a
> **join code**. Now I'll join from my phone with that code." *(join)* "Notice the
> player list updated **instantly on both screens** — that's the WebSocket sync; no
> refresh. Everyone's in the setup phase now, where we author our starting cards.
> I'll add a couple, my other player adds a couple..." *(author)* "...and I'll
> **start the game**. Now we're in the playing phase: each player has a hand, there's
> a shared effect log, and a center zone for house rules."

**Shot checklist**
- [ ] Laptop: landing → **Create room**. Show the room code clearly (zoom if small).
- [ ] Phone / second tab: **Join by code**. Enter a player name.
- [ ] **Split or quick-cut both screens** at the moment the player list updates —
      this is the "realtime" money shot. Say "instantly on both."
- [ ] Setup phase: author 1–2 quick cards on each device.
- [ ] Host clicks **Start game**. Land on the playing view: hands, effect log,
      center/house-rule zone, scoreboard all visible.
- [ ] Point out the scoreboard state *before* any wild card (so the change later is obvious).

---

## 4 · Live demo pt 2 — the AI magic — (3:30–6:00)

This is the centerpiece. Budget ~2.5 min: ~1:20 for the scoring card, ~1:10 for the
house-rule card.

**Talking track (card A — scores)**
> "Here's where it gets fun. I'll author a genuinely absurd card." *(paste wild card
> #1 or #2)* "Watch the **brewing indicator** — the agent is interpreting it right
> now: reasoning, pulling similar cards, deciding on effects. ... And there's the
> **verdict**: it understood the card, and here's the **interpreted effect** it's
> going to apply. I'll **play** it — and look at the **effect log**: it logged
> exactly what happened, and the **scores just changed** on both screens."

**Talking track (card B — house rule)**
> "Now a different kind of card — a **house rule**." *(paste card #3)* "Instead of a
> one-shot effect, the agent recognizes this as a **persistent rule**, so it drops it
> into the **center zone** where it stays in effect for the rest of the game. From
> now on the referee enforces it every turn."

**Shot checklist**
- [ ] Author card A (paste the pre-written text). Play it.
- [ ] **Linger on the "brewing" indicator** — don't cut it; the wait *is* the story
      of "the AI is thinking." Narrate over it.
- [ ] Show the **verdict + interpreted effect** panel clearly.
- [ ] After playing: **effect log entry appears** and **scoreboard changes** — show
      both devices reflecting it (realtime again).
- [ ] Author card B (house rule). Play it.
- [ ] Show it landing in the **center zone** and persisting.
- [ ] If a card is slow, calmly cover with the trace tab or cut to the backup clip.

---

## 5 · Under the hood — (6:00–7:30)

**Talking track**
> "So how does that actually work? Here's the **LangGraph agent graph**." *(show
> graph)* "Each card runs through these nodes. Here's a real **LangSmith trace** of
> the card I just played — you can see **per-node spans**: reason, retrieve, classify,
> and the branch to either emit ops or generate a snippet. For the code path, safety
> matters: any generated snippet goes through an **AST validator** — it rejects
> imports, dunder access, anything dangerous — and only then runs in a **separate
> subprocess sandbox** with limits. And the retrieval step is grounded in **RAG
> exemplars**: real example cards in Qdrant that show the agent how to translate
> new cards into the engine's DSL."

**Shot checklist**
- [ ] Show the graph (from `src/tbwc/agent/graph.py`, or a rendered diagram in `WRITEUP.md`).
- [ ] LangSmith: open the trace for the card from Segment 4. **Expand the spans** so
      per-node timing/latency is visible. Point at `reason`, `retrieve`, `classify`,
      `emit_ops`/`gen_snippet`, `validate_snippet`, `judge`.
- [ ] Show the AST validator + subprocess sandbox code briefly (`src/tbwc/sandbox/`).
      Name one thing it blocks (imports / `__` attribute access).
- [ ] Show a couple of RAG exemplar cards (the seed corpus) so "grounded in examples"
      is concrete, not hand-wavy.

---

## 6 · Evals — (7:30–9:00)

**Talking track**
> "This isn't vibes — it's evaluated. I built an **eval harness** over a **35-card
> real test set** — actual cards people wrote, transcribed and hand-annotated with
> the correct effects. An **LLM judge** scores each interpretation across multiple
> dimensions: did it classify the card right, are the emitted ops correct, is the
> target and timing right, is the generated DSL valid, and an overall score. Then I
> ran experiments: an **A/B on the retriever** — baseline dense vs. a multi-query
> retriever — and a **few-shot exemplar** improvement in the emit step. Here are the
> before/after tables." *(show RETRIEVER_ANALYSIS.md)* "The advanced retriever plus
> few-shot exemplars measurably improved the judge scores."

**Shot checklist**
- [ ] Terminal: show the harness command, e.g.
      `uv run python -m tbwc.evals.harness --data data/eval/real_cards.json`
      (run it earlier off-camera if it's slow; show the tail of results here).
- [ ] Show `data/eval/real_cards.json` — call out **35 cards**, hand-annotated.
- [ ] Show the **multi-dimensional judge** scores (classification, ops,
      target/timing accuracy, DSL validity, overall).
- [ ] Open `src/tbwc/evals/RETRIEVER_ANALYSIS.md` — show the **retriever A/B** table
      and the **few-shot improvement** table.
- [ ] Be honest: note the analysis numbers are regenerated against a live key
      (don't overclaim precise figures if you're showing placeholder tables).

---

## 7 · Wrap — (9:00–10:00)

**Talking track**
> "That's the whole loop: invent a card, the AI referees it, the game state updates
> for everyone in real time — and it's measured, traced, and sandboxed. **What's
> next for Demo Day:** I want to run a live game with the audience, let them submit
> the wildest cards they can think of, and let the crowd vote on **keep or change**
> in the epilogue phase. If you want to poke at it, the code and the writeup are
> linked below. Thanks for watching!"

**Shot checklist**
- [ ] Optional: quick glimpse of the **epilogue / keep-or-destroy voting** screen to
      tease the "what's next."
- [ ] End card / final frame with links:
      - GitHub repo
      - Deployed app URL
      - `WRITEUP.md` (architecture + write-up)
      - `src/tbwc/evals/RETRIEVER_ANALYSIS.md` (eval methodology + results)
- [ ] Cam bubble on for the sign-off. End on time — do **not** run past 10:00.

---

## Timing cheat-sheet (glance while recording)

```
0:45   done: hook
1:30   done: architecture
3:30   done: room created/joined + game started
6:00   done: wild card played + house rule set   <-- the centerpiece; protect this time
7:30   done: graph + trace + sandbox + RAG
9:00   done: evals
10:00  done: wrap. STOP.
```

If you're running long, the safe cuts are: trim Segment 5 (show trace only, skip
sandbox code) and Segment 6 (show the table, skip running the harness live).
Never cut Segment 4.
