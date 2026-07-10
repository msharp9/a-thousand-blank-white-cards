# 1000 Blank White Cards

A digital, AI-refereed implementation of the party game **1000 Blank White Cards**, where players draw, write, and play their own cards to invent the game as they go.

## What it is

[1000 Blank White Cards](https://en.wikipedia.org/wiki/1000_Blank_White_Cards) is a party game with no fixed rules: players write free-text cards ("Gain 5 points", "Everyone swaps hands", "Draw a cat, +2 to anyone who compliments it") and play them to make up the game collaboratively. This project brings that to a realtime web app where an **AI referee** — a LangGraph agent backed by retrieval-augmented generation (RAG) — reads each hand-written card, interprets its intent, and turns it into executable game effects. Multiple players join a shared room over WebSockets and play together in the browser.

## How to play

You're a rules author, not just a player. Every card is one you (or someone at the table) invented — a scribble like *"Steal 8 points from whoever's winning"* or *"Everyone must stand up; the last one seated loses 5"* — and an **AI referee** reads it, works out what it means, and makes it happen. The fantasy: dream up any rule you want and watch the game bend around it, instantly, with no arguing.

**Turn flow.** On your turn you:

1. **Draw** a card from the deck (or grab a blank one).
2. **Create** a new card by writing free text, and/or **play** a card from your hand.
3. The referee **resolves** it — interpreting the text into a game effect (gain/lose points, skip a turn, reverse the order, steal, rewrite the win condition…) and updating everyone's board in realtime.

Play passes around the table. Anyone can invent a card that changes the rules — including how you win — so the game you finish is never the game you started.

**Winning.** There's no fixed target: a card sets (or *re-sets*) the win condition. It might be *"first to 1000 points"*, *"lowest score wins"*, or something a player made up thirty seconds ago. When a win condition is met, that game ends — and the best cards can be kept for next time.

**Example cards**

| Card | What it says | What the referee does |
| --- | --- | --- |
| **Windfall** | "Gain 5 points." | +5 points to you, immediately. |
| **Tax Season** | "Every player loses 10 points. No exceptions." | −10 points to all players. |
| **Backwards Day** | "Reverse the direction of play." | Flips turn order for the rest of the game. |
| **Robin Hood** | "Steal 8 points from the player with the most points." | Moves 8 points from the current leader to you. |
| **New Rules** | "Forget 1000 — first player to reach 250 wins." | Rewrites the win condition to *first to 250*. |

**A short exchange**

> **Ana** plays **Windfall** → *Ana +5 (now 5).*
> **Ben** writes and plays a blank card: *"Anyone who laughs loses 3 points."* The referee reads it, applies it as a persistent rule — *watch out.*
> **Ana** plays **Robin Hood**, targeting Ben (who's leading) → *steals 8 from Ben to Ana.*
> **Ben**, rattled, plays **New Rules**: *"Lowest score wins."* → the whole game inverts. Now everyone's racing to give points *away*.

That's the loop: draw, invent, play, watch the rules mutate. The referee keeps it fair and fast so the table can keep being ridiculous.

## Architecture

The backend is a FastAPI app (`src/tbwc/`) with a deterministic game engine, a LangGraph interpretation agent, RAG over a card corpus, and a sandboxed snippet executor; the frontend is a Next.js 16 app in `frontend/`. See the [project write-up](docs/WRITEUP.md#repository-layout) for the full component breakdown and diagrams.

## WebSocket API

Live gameplay runs over a WebSocket. It is intentionally **not** listed in the
interactive API docs at `/docs` — FastAPI/OpenAPI only documents REST routes, so
the Swagger page shows just `/health` and the `/rooms` endpoints. This section is
the durable reference for the realtime protocol.

**Endpoint:** `ws://<host>/ws/{room_code}` (use `wss://` in production).

**Handshake.** Create a room with `POST /rooms`, register a player with
`POST /rooms/{code}/join` (returns a `player_id`), then open the socket. The
**first message must be a `join`** envelope carrying that `player_id`. On connect
(and on reconnect) the server replies with a full `state` snapshot. Every message
is a JSON object with a `type` field.

**Client → server**

| type | fields | purpose |
| --- | --- | --- |
| `join` | `player_id` (null on first join), `name` | Authenticate the socket into the room; must be the first message. |
| `start` | — | Build/shuffle the deck, deal starting hands, begin play. |
| `play` | `card_id`, `placement` (`zone`, `target_player_id`), `chosen_player_id?`, `chosen_card_id?` | Play a card; the AI referee interprets it and applies the effect (active player only). Ends the turn. |
| `pass` | — | End your turn without playing a card (active player only). Drawing is automatic at turn start, so there is no manual `draw`. |
| `create_card` | `title`, `description` | Author a new card and interpret it immediately (allowed off-turn). |
| `preview_card` | `title`, `description` | Dry-run interpretation preview without changing state. |
| `epilogue_vote` | `card_id`, `keep` | Vote to keep/discard a card during the epilogue phase. |

**Server → client**

| type | fields | meaning |
| --- | --- | --- |
| `state` | `state` | Full game-state snapshot (sent on connect and after every mutation). |
| `brewing` | `card_id` | The referee is interpreting a card (in-flight indicator). |
| `card_interpreted` | `card_id`, `program`, `snippet`, `verdict` | Result of interpreting a played/created card. |
| `effect_applied` | `log_entry` | An effect was applied; human-readable log line. |
| `preview_result` | `program`, `snippet`, `verdict` | Reply to `preview_card`. |
| `prompt_choice` | `card_id`, `prompt`, `choices` | Server asks the active player to pick a target. |
| `epilogue` | `cards` | Epilogue phase opened with the cards created this game. |
| `error` | `message` | An error (bad message, not your turn, room not found, …). |

**Close codes:** `4000` bad handshake, `4001` unknown `player_id`, `4004` room
not found, `4009` connection replaced by a newer socket for the same player.

The message envelopes are defined in [`src/tbwc/models/ws_messages.py`](src/tbwc/models/ws_messages.py); the handler lives in [`src/tbwc/ws.py`](src/tbwc/ws.py).

## Quickstart — Backend

**Prerequisites:** [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.14 (uv can install it for you).

```bash
# 1. Install uv (if needed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install Python 3.14 + dependencies
uv python install
uv sync

# 3. Configure environment
cp .env.example .env
# Edit .env and set OPENAI_API_KEY (required).
# Optional: TAVILY_API_KEY, LANGSMITH_*, QDRANT_* (see comments in .env.example).

# 4. Run the API
uv run uvicorn tbwc.app:app --reload

# 5. Health check
curl localhost:8000/health
```

### Docker

```bash
docker build -t tbwc .
docker run -p 8000:8000 --env-file .env tbwc
```

## Quickstart — Frontend

```bash
cd frontend
npm install

# Configure environment
cp .env.example .env.local
# Set NEXT_PUBLIC_API_URL (e.g. http://localhost:8000)
# and NEXT_PUBLIC_WS_URL (e.g. ws://localhost:8000).

npm run dev
```

Then open [http://localhost:3000](http://localhost:3000). Make sure the backend is running first.

## Testing & quality

```bash
uv run pytest              # 367 tests, ~91% coverage (fails under 80%)
uv run ruff check .        # lint
uv run ruff format --check .   # formatting check
```

Optionally install pre-commit hooks (linting/testing also run in CI):

```bash
uvx prek install
```

## Evals

Offline evaluation of the agent + retriever. All eval scripts call OpenAI, so set `OPENAI_API_KEY` first.

```bash
uv run python -m tbwc.evals.harness          # main eval harness
uv run python -m tbwc.evals.retriever_ab     # retriever A/B comparison
uv run python -m tbwc.evals.improvement_ab   # few-shot before/after eval
```

See [`src/tbwc/evals/RETRIEVER_ANALYSIS.md`](src/tbwc/evals/RETRIEVER_ANALYSIS.md) for retriever analysis.

## Deployment

- **Backend** — deployed to [Render](https://render.com) via [`render.yaml`](render.yaml) (Docker runtime, `/health` health check). See [`docs/deploy/render-steps.md`](docs/deploy/render-steps.md).
- **Frontend** — deployed to [Vercel](https://vercel.com). See [`docs/deploy/vercel-steps.md`](docs/deploy/vercel-steps.md).
- **Observability** — LangSmith setup in [`docs/deploy/langsmith-setup.md`](docs/deploy/langsmith-setup.md).
- **Smoke test** — post-deploy checklist in [`docs/deploy/smoke-checklist.md`](docs/deploy/smoke-checklist.md).

## Docs & links

- [Project write-up](docs/WRITEUP.md) — problem, solution, architecture diagrams, eval results, and Demo Day notes (rubric tasks 1–7).
- [Retriever analysis](src/tbwc/evals/RETRIEVER_ANALYSIS.md) — eval findings.
- [Loom demo script](docs/loom-script.md) — timed ≤10-minute demo walkthrough.
- [Deployment docs](docs/deploy/) — Render, Vercel, LangSmith, and smoke checklist.
</content>
</invoke>
