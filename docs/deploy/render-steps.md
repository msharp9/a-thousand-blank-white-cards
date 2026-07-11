# Deploying the TBWC Backend to Render

This guide walks through deploying the FastAPI backend to [Render](https://render.com)
using the Infrastructure-as-Code (IaC) definition in [`render.yaml`](../../render.yaml)
at the repository root.

Render reads `render.yaml` as a [Blueprint](https://render.com/docs/blueprint-spec),
so most of the service configuration is applied automatically. You only need to
supply the secret environment variables through the dashboard.

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

- **A Render account** connected to the GitHub organization/repo that hosts this
  project. Render needs read access to the repo so it can pull code and detect
  `render.yaml`.

- **Your secret values on hand:** `LLM_API_KEY` (and `LLM_BASE_URL` if you use a
  gateway rather than hosted OpenAI), `TAVILY_API_KEY`, `LANGSMITH_API_KEY`, and
  the deployed frontend origin(s) for `CORS_ORIGINS` (e.g. your Vercel URL).

## What `render.yaml` declares

| Field | Value | Notes |
| --- | --- | --- |
| `type` | `web` | Public HTTP service. |
| `name` | `tbwc-backend` | Becomes part of the URL (`tbwc-backend.onrender.com`). |
| `runtime` | `docker` | Builds from the repo `Dockerfile`. |
| `dockerfilePath` | `./Dockerfile` | Root Dockerfile from bead m3r.1. |
| `plan` | `free` | Free tier (spins down when idle; cold starts on first request). |
| `region` | `oregon` | US West. |
| `healthCheckPath` | `/health` | Matches the FastAPI health route. |
| `envVars` | see below | Non-secret values are set inline; secrets use `sync: false`. |

### Environment variables

Values set inline in `render.yaml` (no action needed):

- `PORT=10000` — Render's conventional port. The container binds to it via `${PORT}`.
- `LANGSMITH_PROJECT=tbwc-prod`
- `LANGSMITH_TRACING=true`

Values marked `sync: false` are **not** stored in the repo. Render creates the keys
but leaves them blank; you must fill them in through the dashboard (see below):

- `LLM_API_KEY`
- `LLM_BASE_URL`
- `TAVILY_API_KEY`
- `LANGSMITH_API_KEY`
- `CORS_ORIGINS`

## Deploy steps

1. In the Render dashboard, click **New +** → **Web Service** (or **New +** →
   **Blueprint** to apply `render.yaml` for the whole repo at once).
2. **Connect the repository** containing this project. Grant Render access to the
   GitHub org if prompted.
3. Render **auto-detects `render.yaml`** at the repo root and pre-fills the service
   configuration (Docker runtime, region, health check, env var keys).
4. **Confirm the service name is `tbwc-backend`** and the plan is `free`. The
   settings should already match the blueprint — you generally don't need to change
   anything here.
5. Click **Apply** / **Create Web Service**. Render builds the Docker image and
   starts the first deploy.

## Set the secret environment variables

The `sync: false` variables must be filled in before (or immediately after) the
first deploy, otherwise the service will start without its credentials.

1. Open the `tbwc-backend` service → **Environment** tab.
2. Set each secret value:
   - `LLM_API_KEY` — your LLM gateway / OpenAI API key.
   - `LLM_BASE_URL` — OpenAI-compatible endpoint URL; leave blank for hosted OpenAI.
   - `TAVILY_API_KEY` — your Tavily API key.
   - `LANGSMITH_API_KEY` — your LangSmith API key.
   - `CORS_ORIGINS` — the allowed browser origins for your frontend.

3. **`CORS_ORIGINS` must be a JSON array string.** The backend uses
   `pydantic-settings`, which parses `list[str]` fields as JSON. A bare URL will
   fail to parse. Use a JSON array, for example:

   ```
   ["https://tbwc.vercel.app"]
   ```

   For multiple origins:

   ```
   ["https://tbwc.vercel.app", "https://www.tbwc.app"]
   ```

4. Save. Render redeploys automatically when environment variables change.

## Health check & URL

- Render polls `healthCheckPath: /health`. Once the app responds `200`, the deploy
  is marked live. You can watch this in the service's **Events** / **Logs** tabs.
- The service is reachable at:

  ```
  https://tbwc-backend.onrender.com
  ```

  Verify it directly:

  ```bash
  curl https://tbwc-backend.onrender.com/health
  ```

- Use that base URL as the backend endpoint the frontend calls, and make sure the
  frontend's own origin is included in `CORS_ORIGINS`.

## Notes on the free plan

- Free web services **spin down after inactivity**; the first request after idle
  incurs a cold-start delay of up to ~1 minute while the container restarts.
- Pushes to the connected branch trigger automatic redeploys.
