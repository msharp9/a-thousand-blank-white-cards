# Loom Demo Script — 1000 Blank White Cards

A timed script for a graded Loom video, **≤10 minutes total**:
**Part 1** is a 5-minute live demo, **Part 2** is a 5-minute code / architecture
/ approach walkthrough. Each beat gives a **time range**, *what to show on
screen*, and what to say.

> Setup before you hit record: backend running (`uv run uvicorn board.app:app`),
> frontend running (`cd frontend && npm run dev`), a laptop browser tab and a
> phone (or a second, narrow browser window) both open to
> [localhost:3000](http://localhost:3000), side by side and in frame. A `.env`
> with a real `LLM_API_KEY` (and ideally `TAVILY_API_KEY`) so the agent runs
> live. Pre-decide your two demo players' names.

---

## Part 1 — LIVE DEMO (0:00–5:00)

### 0:00–0:45 — Premise + realtime multiplayer

**Show:** the landing page. Create a room on the laptop; join it from the phone
with a second name. Get both devices onto the same room screen, side by side.

**Say:** "This is 1000 Blank White Cards — a party game with *no fixed rules*.
The players write the cards, and the game invents itself as you go. It's a
realtime web app: here's a room on my laptop, and I'm joining it from my phone.
Two devices, one shared game." Point out the room code and the "Connected"
indicator on both screens.

### 0:45–2:00 — Author cards, deal, take normal turns

**Show:** the **setup** phase. On each device, author a couple of cards — one
plain scoring card like **"Windfall — Gain 5 points."** Host starts the game;
the deck deals 5 cards to each player. Take one full turn on the laptop: **Draw
a card**, then play the "Gain 5 points" card. Point at the score updating and at
the effect log line.

**Say:** "First we build the deck together — some pre-made cards, plus five each
that we write ourselves. Then it deals us a hand. A turn is always the same
three steps: draw one, play one, end. Here's the simplest possible card — 'Gain
5 points' — I play it, my score goes up by five, and it's logged. In the basic
game *every* card is just a point change, and that's a complete game." Hand the
turn to the phone and take one more quick normal turn so the loop is obvious on
both devices.

### 2:00–4:00 — THE MONEY SHOT: a wild free-text card

**Show:** on the active device, author (or play a blank as) a genuinely wild
card: **"Heist — Steal 8 points from the player in the lead and reverse the turn
order."** Play it. Point, in order, at:

1. the **"brewing…"** indicator appearing (the card went to the agent, not the
   deterministic compiler);
2. the **arbiter comment** line that lands in the effect log — it's prefixed
   with the 🤖 robot marker to mark it as the AI game master speaking;
3. the **scores changing** — 8 points move off the current leader onto the
   player who played it;
4. the **turn order reversing**;
5. **both devices updating at once** — the phone reflects the new state without
   anyone touching it.

**Say:** "Now the fun part. This card isn't a simple point change — it's free
text with two effects and a *'player in the lead'* that has to be resolved
against the live board. Watch: it says 'brewing', because it's gone to the AI
arbiter. The agent reads the card, pulls up similar cards it's seen before to
ground its interpretation — that's the RAG step — and turns the sentence into an
executable effect. It applies: eight points come off whoever's winning, and the
turn order flips. The new state broadcasts to *every* client — see, my phone
just updated on its own. And the arbiter left a comment in the log, in
character." (If a target picker pops up asking who's in the lead, pick and note
that the server re-interprets the now-targeted card and applies it.)

### 4:00–5:00 — End the game, scoring, epilogue vote

**Show:** play down to the **last card in the deck** (or fast-forward a prepared
game). When the last card is drawn the drawer finishes, then the game ends. Show
the **winner** banner and final scores, then the **epilogue** phase where
players vote on which newly-written cards to keep.

**Say:** "The game ends when the last card is drawn. End-of-game effects resolve,
we total scores, and there's a winner — for now. Then the epilogue: everyone
votes on which cards *we invented* are good enough to keep for next time — the
kept ones get folded back into the deck and the agent's memory. Which is really
the point. Everything's made up, and the points don't matter."

---

## Part 2 — CODE / ARCHITECTURE / APPROACH (5:00–10:00)

### 5:00–6:30 — The shape of the system

**Show:** `docs/architecture.md` §1 — the top-level Mermaid system diagram. Then
open `frontend/app/room/[code]/page.tsx` and scroll to the phase switch
(`phase === "lobby" / "setup" / "playing" / "epilogue" / "ended"`).

**Say:** "Four layers. A Next.js frontend talks to a FastAPI backend over REST
for room lifecycle and a WebSocket for live gameplay. The backend composes a
deterministic game *engine* and a single tool-calling *agent*, and the agent is
backed by a Qdrant vector store for RAG and one OpenAI-compatible LLM gateway.
The frontend is basically a phase router — this one component in
`room/[code]/page.tsx` renders lobby, setup, playing, epilogue, or ended off the
`phase` field in the state snapshot the server pushes. The server is the single
source of truth; the client just draws whatever state arrives."

### 6:30–8:00 — The agent: free text → structured effect

**Show:** `src/agent/runtime.py` (`run_agent`, the `MAX_TOOL_CALLS` and
`AGENT_TIMEOUT_SECONDS` caps, the "never raises — returns a fallback"
comment), then `src/agent/contract.py` (`InterpretResult`: `program` /
`snippet` / `verdict` / `comment` / `persona_action`), then the `src/agent/tools/`
directory — call out `card_rag_hybrid.py` (the RAG tool — BM25+dense hybrid) and `web_search.py` (Tavily).

**Say:** "When a card can't be compiled deterministically, `run_agent` builds one
LangChain tool-calling agent with an in-character persona. Its output contract is
`InterpretResult` — a structured effect program *or* a generated snippet, plus a
verdict and the arbiter's comment. It has a real toolbox: `card_rag_hybrid` retrieves
similar cards from Qdrant (dense + BM25 keyword fusion) so interpretation is grounded in precedent, and
`web_search` hits Tavily when a card references a meme or game term it needs to
look up. It's bounded on purpose — a hard tool-call cap and a wall-clock timeout
— and it *never* raises to its caller; on timeout, cap, or error it returns a
deterministic fallback so a play never hangs the game. That's how 'steal from the
leader and reverse the order' became an executable effect a moment ago."

### 8:00–9:15 — The engine: deterministic reducers + the sandbox

**Show:** `src/engine/reducers.py` (`apply_op`, the `Op` union), `src/engine/apply.py`,
then `src/engine/sandbox/` — point at `validate.py`, `api_surface.py`,
`runner.py`, `revalidate.py`. Optionally show `docs/architecture.md` §3, the
layering table.

**Say:** "The engine is the game's physics: pure reducers that take state plus an
op and return *new* state — no mutation, fully deterministic and testable. For
genuinely novel effects the agent can emit a Python snippet, and the sandbox
isolates it four ways: an AST allowlist for what code can be written, a
restricted game façade for what it can reach, a subprocess with a timeout for
where it runs, and re-validation of the resulting ops through the *same*
reducers as a normal play. And there's a hard layering rule the tests enforce
statically: the agent may import the engine, but the engine may *never* import
the agent. So the deterministic core never depends on the LLM — the board layer
is the one place they're composed together."

### 9:15–10:00 — Approach & production concerns, close

**Show:** `src/config.py` (the single `Settings` / `get_settings` gateway config),
the `src/agent/rag/` directory (the RAG store as the memory component), and the
`src/evals/` directory (`harness.py`) — mention it, don't run it.

**Say:** "A few production choices. Everything goes through one generic
OpenAI-compatible gateway — one base URL and key drive both chat and embeddings,
so it runs against hosted OpenAI, a local model, or any compatible endpoint. The
RAG store doubles as memory: cards players vote to keep get upserted back in, so
the corpus grows across games. LangSmith tracing is wired for observability,
behind a flag. And there's an offline eval harness that scores the interpretation
pipeline — the numbers are still being finalized, so I won't quote figures here.
That's 1000 Blank White Cards: a deterministic game engine, a bounded
tool-calling agent to bring the weird cards to life, and a realtime board tying
it together. Thanks for watching."
