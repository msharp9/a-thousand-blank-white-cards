# 1000 Blank White Cards

A digital, AI-supported implementation of the party game **1000 Blank White Cards**, where players draw, write, and play their own cards to invent the game as they go.

"Everything's made up and the points don't matter."

## What it is

[1000 Blank White Cards](https://en.wikipedia.org/wiki/1000_Blank_White_Cards) is a party game with no fixed rules: players write free-text cards ("Gain 5 points", "Everyone swaps hands", "Draw a cat, +2 to anyone who compliments it") and play them to make up the game collaboratively. This project brings that to a realtime web app where an AI agent brings the cards to life by translating their description to executable game effects. 1000 Blank White Cards is a non-serious party game played for laughs.

## How to play

The deck is made of cards *you and the other players write*. You build the deck
together, then play it out one turn at a time. The player with the most points at
the end wins (unless they don't), but the real fun is inventing the cards. 

A game runs in these steps:

1. **Join the lobby.** Everyone opens the room and enters a name. One player is
   the host.
2. **Start the game.** The host starts it once everyone's in.
3. **Build the deck.** The deck is assembled from three sources:
   - **30 pre-made cards** are shuffled in.
   - **Each player writes 5 cards** — title and description
     (e.g. *"Gain 5 points"*, *"Everyone else loses 2"*).
   - **5 blank cards per player** are shuffled in, to be written later.
4. **Deal.** The deck is shuffled and **5 cards are dealt to each player**.
5. **Take turns.** Play passes around the table. Each turn is exactly:
   1. **Draw** one card from the deck.
   2. **Play** one card from your hand.
      - If it's a **blank card**, you write it *as you play it* — give it a title
        and effect, then it resolves. (Blanks are the only cards you author
        mid-game; your other cards were written back in step 3.)
      - A played card applies its effect, then goes to the discard pile (or stays
        in front of you if it's a lasting card).
      - If you can't play a card, you may draw a second card. **Note:** A blank card
        is always playable.
   3. **End your turn.**
6. **The game ends** when the **last card is drawn from the deck**: the player who
   drew it finishes their turn, and then the game is over.
7. **Score and win.** Any end-of-game card effects resolve first (for example, a
   card worth points *only if you're still holding it*). Then everyone totals
   their points — **the highest score wins.**
8. **Epilogue.** Players vote on which of the newly-written cards are good enough
   to keep in the pile for future games. The rest are discarded.
   - **Note:** Many players agree that creating a card that gets the most votes during the Epilogue phase is the true victory.

**The basic game.** In the simplest game, every card just **adds or subtracts
points** from one or more players — no special rules needed. That's all it takes
to play a full game start to finish.

**Going further (optional AI assist).** Because cards are free text, you can write
almost anything — *"Steal 8 points from whoever's winning"*, *"Reverse the turn
order"*, *"Everyone swaps hands"*. When a card isn't a simple point change, an
**AI agent** reads the text, works out what it means, and applies it. If the agent can't figure out the intent of the card, look out! Anything can happen!

**Example cards**

| Card | What it says | What happens |
| --- | --- | --- |
| **Windfall** | "Gain 5 points." | +5 points to you, immediately. |
| **Tax Season** | "Every player loses 10 points. No exceptions." | −10 points to all players. |
| **Robin Hood** | "Steal 8 points from the player with the most points." | Moves 8 points from the current leader to you. |
| **Keepsake** | "Worth 10 points if it's still in your hand at the end." | +10 at game end, only if you never played it. |

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
# Edit .env and set LLM_API_KEY (hosted OpenAI), or point LLM_BASE_URL at an
# OpenAI-compatible gateway / local server (see comments in .env.example).
# Optional: TAVILY_API_KEY, LANGSMITH_*, QDRANT_* (see comments in .env.example).

# 4. Run the API
uv run uvicorn board.app:app --reload

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
uv run pytest              # ~710 tests, ~92% coverage (fails under 80%)
uv run ruff check .        # lint
uv run ruff format --check .   # formatting check
```

Optionally install pre-commit hooks (linting/testing also run in CI):

```bash
uvx prek install
```

## Evals

Offline evaluation of the agent + retriever. All eval scripts call the LLM gateway, so configure `LLM_API_KEY` (and optionally `LLM_BASE_URL`) first.

```bash
uv run python -m evals.harness          # main eval harness
uv run python -m evals.retriever_ab     # retriever A/B comparison
uv run python -m evals.improvement_ab   # few-shot before/after eval
```

See [`src/evals/RETRIEVER_ANALYSIS.md`](src/evals/RETRIEVER_ANALYSIS.md) for retriever analysis.

## Deployment

- **Backend** — deployed to [Render](https://render.com) via [`render.yaml`](render.yaml) (Docker runtime, `/health` health check). See [`docs/deploy/render-steps.md`](docs/deploy/render-steps.md).
- **Frontend** — deployed to [Vercel](https://vercel.com). See [`docs/deploy/vercel-steps.md`](docs/deploy/vercel-steps.md).
- **Observability** — LangSmith setup in [`docs/deploy/langsmith-setup.md`](docs/deploy/langsmith-setup.md).
- **Smoke test** — post-deploy checklist in [`docs/deploy/smoke-checklist.md`](docs/deploy/smoke-checklist.md).

## Docs & links

- [Project write-up](docs/WRITEUP.md) — problem, solution, architecture diagrams, eval results, and Demo Day notes (rubric tasks 1–7).
- [Retriever analysis](src/evals/RETRIEVER_ANALYSIS.md) — eval findings.
- [Loom demo script](docs/loom-script.md) — timed ≤10-minute demo walkthrough.
- [Deployment docs](docs/deploy/) — Render, Vercel, LangSmith, and smoke checklist.
