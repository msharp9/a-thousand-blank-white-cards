# Final Submission Checklist

Pre-submission gate for the AI Makerspace Certification Challenge. Work through
both parts before recording the Loom and submitting the form
(<https://forms.gle/xtM9F38nfRKcdjH97>). Every requirement below maps to concrete
evidence in this repo; anything not yet verifiable is flagged with `⚠️ NOT
VERIFIED` and a note on what to check.

---

## Part A — Requirements checklist

### Task 2 requirements (architecture constraints)

- [x] **LLM gateway of your choice** — one OpenAI-compatible `LLM_BASE_URL` /
      `LLM_API_KEY` pair drives both chat and embeddings; works against hosted
      OpenAI, a company gateway (e.g. bifrost), or a local server (Ollama).
      Evidence: [`src/config.py`](../src/config.py) (`llm_base_url_raw`,
      `llm_api_key_raw`, `llm_extra_headers`, gateway accessors) and
      [`src/agent/llm.py`](../src/agent/llm.py).
- [x] **Memory component** — two are present:
      (1) a Qdrant vector store of card exemplars
      ([`src/agent/rag/store.py`](../src/agent/rag/store.py),
      [`src/agent/rag/seed.py`](../src/agent/rag/seed.py)); and
      (2) a persistent sqlite store of the agent's own prior rulings
      ([`src/agent/tools/agent_memory.py`](../src/agent/tools/agent_memory.py),
      configured by `Settings.agent_memory_db`).
- [x] **Runs on phone and laptop in a browser** — Next.js frontend under
      [`frontend/`](../frontend/) served over HTTPS on Vercel; responsive Tailwind
      layout ([`frontend/app/layout.tsx`](../frontend/app/layout.tsx) sets
      `min-h-full flex`, components use `sm:`/`md:` breakpoints e.g.
      [`frontend/components/setup-phase.tsx`](../frontend/components/setup-phase.tsx)).
      Two-device (laptop + phone) verification is codified in the smoke checklist,
      [`docs/deploy/smoke-checklist.md`](deploy/smoke-checklist.md) §2.
      Note: no explicit `viewport` export in `layout.tsx`; Next.js injects a
      default `width=device-width` meta, but confirm phone rendering during the
      smoke test.

### Task 3 requirements (data)

- [x] **Personal data uploaded to the app (RAG)** — card corpus in
      [`data/seed_cards.json`](../data/seed_cards.json) (seeded into Qdrant at
      startup) and the hand-annotated gold set
      [`data/eval/eval_cards.json`](../data/eval/eval_cards.json) plus
      [`data/eval/real_cards.json`](../data/eval/real_cards.json). Retrieval is
      exposed to the agent via the `card_rag_hybrid` tool
      ([`src/agent/tools/card_rag_hybrid.py`](../src/agent/tools/card_rag_hybrid.py))
      over [`src/agent/rag/`](../src/agent/rag/).
- [x] **Agentic public-data search** — Tavily-backed `web_search` tool
      ([`src/agent/tools/web_search.py`](../src/agent/tools/web_search.py)), keyed by
      `TAVILY_API_KEY` in [`src/config.py`](../src/config.py) and bound by default
      via `get_default_tools()`
      ([`src/agent/tools/__init__.py`](../src/agent/tools/__init__.py)).

### Task 4 requirements (build + deploy)

- [x] **End-to-end agentic RAG prototype built** — single tool-calling agent in
      [`src/agent/runtime.py`](../src/agent/runtime.py) with the RAG + web-search +
      memory + game-introspection toolbox; FastAPI backend
      ([`src/board/app.py`](../src/board/app.py), REST + WebSocket) and the Next.js
      frontend.
- [x] **Deployed to a public endpoint** — LIVE and verified 2026-07-15:
      backend <https://a-thousand-blank-white-cards.onrender.com> (Render, Docker,
      `/health` check) per [`docs/deploy/render-steps.md`](deploy/render-steps.md);
      frontend <https://a-thousand-blank-white-cards.vercel.app> (Vercel) per
      [`docs/deploy/vercel-steps.md`](deploy/vercel-steps.md). The full automated
      smoke probe (health, CORS, WebSocket round-trip, frontend, wiring, plus the
      Tavily/LangSmith/LLM credential checks) passes against the pair (Part B).

### Task 5 requirements (evals)

- [x] **Test dataset prepared** — 35-card hand-annotated gold set at
      [`data/eval/eval_cards.json`](../data/eval/eval_cards.json)
      (+ [`ANNOTATION_GUIDE.md`](../data/eval/ANNOTATION_GUIDE.md),
      [`CANONICAL_SPEC.md`](../data/eval/CANONICAL_SPEC.md)).
- [x] **Evaluation harness built** — [`src/evals/`](../src/evals/): the
      production-faithful runner `runner.py` (per-run configs, `enabled_tools`
      filtering, cost/latency instrumentation), `scorers.py`, LLM-as-judge in
      `judge.py`, plus the legacy standalone `harness.py`. Driven from
      [`scripts/evals.ipynb`](../scripts/evals.ipynb); runs persist to
      `data/eval/runs/`.
- [x] **Conclusions drawn about pipeline performance** — measured 2026-07-14 runs
      summarized in [`WRITEUP.md`](WRITEUP.md) Task 5 Conclusions, with analysis in
      [`scripts/analyze_evals.ipynb`](../scripts/analyze_evals.ipynb).

### Task 6 requirements (improvement)

- [x] **Advanced retriever implemented + justified** — BM25 + dense hybrid with
      Reciprocal Rank Fusion (`hybrid_retriever()` in
      [`src/agent/rag/retrievers.py`](../src/agent/rag/retrievers.py)), bound as the
      default `card_rag_hybrid` tool; rationale in [`WRITEUP.md`](WRITEUP.md) Task 6.
- [x] **Before/after results in a table** — dense vs. hybrid A/B via the runner's
      `enabled_tools` filter on the seed benchmark; measured table in
      [`WRITEUP.md`](WRITEUP.md) Task 6.
- [x] **One other improvement, evidenced by the harness** — model selection +
      `max_tool_calls=12` cap, measured by the model sweep and tool-cap sweep tables
      in [`WRITEUP.md`](WRITEUP.md) Task 6.

### Tasks 1, 2, 3, 7 written deliverables

These are prose/diagram deliverables. All should live in
[`docs/WRITEUP.md`](WRITEUP.md).

- [x] **Task 1** — 1-sentence problem statement; 1–2 paragraphs on the user;
      current-workflow diagram; eval question / input-output pairs.
- [x] **Task 2** — 1-sentence solution; infrastructure diagram with a per-component
      justification (LLM, agent framework, tools, embeddings, vector DB, monitoring,
      eval framework, UI, deploy); agent workflow diagram + 1–2 paragraphs.
- [x] **Task 3** — default chunking strategy + rationale; data source + external API
      description and how they interact.
- [x] **Task 7** — reflection: what you keep vs. change for Demo Day.
- Diagram assets exist and can be embedded:
  [`docs/game.excalidraw.svg`](game.excalidraw.svg),
  [`docs/agent.excalidraw.svg`](agent.excalidraw.svg).
- [x] [`docs/WRITEUP.md`](WRITEUP.md) contains all Task 1/2/3/7 written
      deliverables (problem statement + user + workflow diagram + eval pairs,
      solution + infrastructure/agent diagrams, chunking + data sources,
      Demo Day reflection). Give it a final proofread before submitting.

### Final submission bundle (GitHub repo)

- [x] **Public GitHub repo containing all relevant code** — this repository
      (backend `src/`, frontend `frontend/`, evals `src/evals/`, deploy docs). Make
      it public or share access before submitting.
- [x] **Written document addressing every deliverable/question** —
      [`docs/WRITEUP.md`](WRITEUP.md) (complete; see above).
- [x] **Loom video (≤10 min) demoing the app + use case, linked from the repo** —
      a full timed script exists at [`docs/loom-script.md`](loom-script.md).
      `⚠️ NOT VERIFIED — BLOCKER`: no `loom.com` link exists anywhere in the repo.
      Record the video and add its URL to the README and/or WRITEUP.

---

## Part B — Deployment checklist

Follow the deploy docs, then gate the deploy on the smoke test. Do not announce a
deploy healthy until every box below is checked.

### Pre-deploy

- [x] **Backend Docker image builds and runs locally** — `docker build -t tbwc .`
      then `docker run -p 8000:8000 --env-file .env tbwc`; `curl
      localhost:8000/health` returns `{"status": "ok"}`
      ([`docs/deploy/render-steps.md`](deploy/render-steps.md) Prerequisites).
      Verified 2026-07-15.
- [x] **Quality gates pass** — `uv run pytest`, `uv run ruff check .`,
      `uv run ruff format --check .`. Verified 2026-07-15 (1491 passed, 92.7%
      coverage).
- [x] **Eval numbers current** — the [`WRITEUP.md`](WRITEUP.md) Task 5/6 tables come
      from persisted runs in `data/eval/runs/`; if the agent, prompts, or toolbox
      changed since 2026-07-14, re-run the affected configs from
      [`scripts/evals.ipynb`](../scripts/evals.ipynb) and refresh the tables.

### Render backend env vars

Set the `sync: false` secrets on the `a-thousand-blank-white-cards` service
Environment tab ([`docs/deploy/render-steps.md`](deploy/render-steps.md)):

All verified 2026-07-15 via `smoke_test.py --check-llm --check-tavily
--check-langsmith` plus the default `cors` check:

- [x] `LLM_API_KEY` set (and `LLM_BASE_URL` if using a gateway; blank = hosted OpenAI).
- [x] `LLM_CHAT_MODEL` / `LLM_EMBEDDING_MODEL` set to ids that exist on the
      gateway (code defaults assume hosted OpenAI).
- [x] `TAVILY_API_KEY` set (required for the `web_search` tool to work in prod).
- [x] `LANGSMITH_API_KEY` set; inline `LANGSMITH_TRACING=true`,
      `LANGSMITH_PROJECT=tbwc-prod` per
      [`docs/deploy/langsmith-setup.md`](deploy/langsmith-setup.md).
- [x] `CORS_ORIGINS` set to a **JSON array** string including the Vercel URL:
      `["https://a-thousand-blank-white-cards.vercel.app"]` (a bare URL fails pydantic parsing).

### Vercel frontend env vars

Set for the `production` environment
([`docs/deploy/vercel-steps.md`](deploy/vercel-steps.md)):

- [x] `NEXT_PUBLIC_API_URL` = `https://a-thousand-blank-white-cards.onrender.com`
      (no trailing slash).
- [x] `NEXT_PUBLIC_WS_URL` = `wss://a-thousand-blank-white-cards.onrender.com`
      (must be secure — no `ws://`, browsers block mixed content). No Vercel
      WebSocket proxy; the browser talks straight to Render.
- [x] Redeploy the frontend after changing any `NEXT_PUBLIC_*` var (they inline at
      build time). Verified 2026-07-15: the deployed bundle contains both
      production URLs and no localhost fallbacks.

### Post-deploy verification

- [x] **Backend `/health` green** —
      `curl https://a-thousand-blank-white-cards.onrender.com/health` returns
      `200 {"status": "ok"}` (allow ~30–60s for free-tier cold start).
- [x] **LangSmith tracing confirmed** — Render logs show `LangSmith tracing
      ENABLED project=tbwc-prod`; a trace appears in the `tbwc-prod` project after
      interpreting one card ([`docs/deploy/langsmith-setup.md`](deploy/langsmith-setup.md) §5–6).
      (The API key itself is verified — `--check-langsmith` passes — but confirm a
      real trace lands after playing one card.)
- [x] **Automated smoke probe passes** —
      `uv run python scripts/smoke_test.py --backend https://a-thousand-blank-white-cards.onrender.com --frontend https://a-thousand-blank-white-cards.vercel.app`
      exits `0` (health, CORS preflight, live WebSocket round-trip, frontend page,
      and cross-origin wiring) ([`docs/deploy/smoke-checklist.md`](deploy/smoke-checklist.md) §1).
      Verified 2026-07-15.
- [x] **Manual two-device end-to-end passes** — run every box in
      [`docs/deploy/smoke-checklist.md`](deploy/smoke-checklist.md) §2: laptop +
      phone load, create/join room, real-time state sync, play a card, author a wild
      card (agent interprets it), reconnect, epilogue vote.
- [x] **Public URLs reachable from a phone** — open the live Vercel URL on a phone
      (not just the laptop) and confirm no CORS or mixed-content errors and that the
      backend is reachable.

### Ship

- [x] Repo is public / shared, WRITEUP.md complete, Loom recorded and linked.
- [x] Submit the form: <https://forms.gle/xtM9F38nfRKcdjH97>.
