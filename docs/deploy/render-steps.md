# Deploying the TBWC Backend to Render

This guide covers deploying the FastAPI backend to [Render](https://render.com).
The live production service is:

```
https://a-thousand-blank-white-cards.onrender.com
```

There are two ways to create the service; both build the same root `Dockerfile`:

- **Public-URL web service** (how the current service was created): paste the
  public GitHub URL into **New +** → **Web Service** → *Public Git repository*
  and configure everything (env vars, plan, health check) by hand in the
  dashboard. This flow **ignores `render.yaml` entirely**.
- **Blueprint** (infrastructure-as-code): Render reads
  [`render.yaml`](../../render.yaml) as a
  [Blueprint](https://render.com/docs/blueprint-spec) and applies the service
  configuration automatically. The **New +** → **Blueprint** flow only lists
  repositories connected through your Git account integration on Render — it
  cannot take a public URL, which is why the repo doesn't appear there unless
  you connect the account first.

## Prerequisites

- **The Docker image builds locally.** Confirm the backend builds and runs before
  deploying:

  ```bash
  docker build -t tbwc-backend .
  docker run --rm -p 8000:8000 -e PORT=8000 tbwc-backend
  # In another terminal:
  curl http://localhost:8000/health
  ```

  The Dockerfile binds uvicorn to `0.0.0.0:${PORT:-8000}`, so it honors whatever
  `PORT` the platform injects.

- **A Render account.** For the Blueprint flow it must be connected to the
  GitHub account/org hosting this repo; for the public-URL flow the repo just
  needs to be public.

- **Your secret values on hand:** `LLM_API_KEY` and `LLM_BASE_URL` (if you use a
  gateway rather than hosted OpenAI), the model ids the gateway serves
  (`LLM_CHAT_MODEL`, `LLM_EMBEDDING_MODEL`), `TAVILY_API_KEY`,
  `LANGSMITH_API_KEY`, and the deployed frontend origin(s) for `CORS_ORIGINS`
  (e.g. your Vercel URL).

## What `render.yaml` declares

| Field | Value | Notes |
| --- | --- | --- |
| `type` | `web` | Public HTTP service. |
| `name` | `a-thousand-blank-white-cards` | Matches the live service/URL. |
| `runtime` | `docker` | Builds from the repo `Dockerfile`. |
| `dockerfilePath` | `./Dockerfile` | Root Dockerfile from bead m3r.1. |
| `plan` | `free` | Free tier (spins down when idle; cold starts on first request). |
| `region` | `oregon` | US West. |
| `healthCheckPath` | `/health` | Matches the FastAPI health route. |
| `envVars` | see below | Non-secret values are set inline; secrets use `sync: false`. |

### Environment variables

Values set inline in `render.yaml` (no action needed under the Blueprint flow;
set them manually under the public-URL flow):

- `PORT=10000` — Render's conventional port. The container binds to it via `${PORT}`.
- `LLM_EMBEDDING_DIMENSIONS=1536` — must match the embedding model's output
  size; the vector store collection is created with this dimension and a
  mismatch fails every upsert.
- `LANGSMITH_PROJECT=tbwc-prod`
- `LANGSMITH_TRACING=true`
- `TRIAGE_AGENT_ENABLED=true` — the failure-triage agent makes extra LLM calls
  on failed/no-op card plays; set `false` to save tokens.

Values marked `sync: false` are **not** stored in the repo; fill them in through
the dashboard:

- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_CHAT_MODEL` — **must be a model id that exists on the gateway** behind
  `LLM_BASE_URL`. The code default (`gpt-5.4-mini`) assumes hosted OpenAI; on a
  gateway with different ids the deploy looks healthy (`/health` is green) but
  the first card interpretation fails with model-not-found.
- `LLM_EMBEDDING_MODEL` — same concern as `LLM_CHAT_MODEL`.
- `TAVILY_API_KEY`
- `LANGSMITH_API_KEY`
- `CORS_ORIGINS`

Two footguns worth calling out:

1. **`CORS_ORIGINS` must be a JSON array string.** The backend uses
   `pydantic-settings`, which parses `list[str]` fields as JSON. A bare URL will
   fail to parse and **crash the app at boot**. Use a JSON array:

   ```
   ["https://tbwc.vercel.app"]
   ```

   For multiple origins:

   ```
   ["https://tbwc.vercel.app", "https://www.tbwc.app"]
   ```

2. **Don't copy `DEV_MODE=true` from a local `.env`.** Dev mode wires
   unauthenticated `POST /rooms/{code}/dev/*` endpoints and file-backed room
   persistence. Leave `DEV_MODE` unset (defaults to `false`) in production.

Render redeploys automatically when environment variables change.

## Deploy steps (Blueprint flow)

1. Connect your GitHub account to Render if you haven't
   (Dashboard → Settings → Git Providers).
2. Click **New +** → **Blueprint** and select this repository.
3. Render reads `render.yaml` and pre-fills the service configuration. Confirm
   the service name and plan, supply the `sync: false` secret values when
   prompted, and click **Deploy Blueprint**.
4. On later pushes Render auto-syncs the Blueprint: dashboard settings that
   conflict with `render.yaml` are overwritten, but `sync: false` env var
   *values* entered in the dashboard are left alone.

### Adopting the existing manually-created service

The current service was created via the public-URL flow, so `render.yaml` is not
yet attached to it. To bring it under Blueprint management later:

1. Connect the GitHub account to Render (required for Blueprints).
2. Either use the dashboard's **Generate Blueprint** feature on the existing
   service, or apply this repo's `render.yaml` — the service `name` matches the
   live service, so Render adopts it instead of creating a duplicate.
3. Before the first sync, make sure `render.yaml` mirrors every setting
   currently configured in the dashboard; conflicting dashboard settings are
   overwritten on sync.

## Health check & URL

- Render polls `healthCheckPath: /health`. Once the app responds `200`, the deploy
  is marked live. You can watch this in the service's **Events** / **Logs** tabs.
- Verify the live service directly:

  ```bash
  curl https://a-thousand-blank-white-cards.onrender.com/health
  ```

- Use that base URL as the backend endpoint the frontend calls, and make sure the
  frontend's own origin is included in `CORS_ORIGINS`.

## POC persistence caveats (free tier)

The free tier has an ephemeral filesystem and spins down when idle; the app is
designed to tolerate this, but know what resets:

- **Rooms and game state are in-memory.** A restart/spin-down ends every active
  game.
- **The RAG card corpus is an in-memory vector store** rebuilt from
  `data/seed_cards.json` at startup. Cards kept via the epilogue do not survive
  restarts. (The `QDRANT_*` env vars are currently unused — the store is
  hardcoded to `:memory:`.)
- **Agent memory (`agent_memory.db`) and capability-wish telemetry
  (`.devstate/capability_wishes.jsonl`) are lost on restart.** Both fail soft;
  gameplay is unaffected.
- **The embedding cache starts empty on every boot**, so each cold start
  recomputes seed embeddings through the LLM gateway (small cost + slower first
  interpretation). The prewarmed local `.embedding_cache.json` is gitignored and
  Render builds from git, so it cannot ship.

## Notes on the free plan

- Free web services **spin down after inactivity**; the first request after idle
  incurs a cold-start delay of up to ~1 minute while the container restarts.
- Pushes to the connected branch trigger automatic redeploys.
