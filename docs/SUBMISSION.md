# Final Submission Checklist

This is the Phase 7 submission-assembly checklist for **1000 Blank White Cards**.

## Automated pre-submission checks (verified locally ✅)

- [x] All deliverable files present:
  - `docs/WRITEUP.md` (rubric tasks 1–7), `docs/loom-script.md`
  - `docs/deploy/render-steps.md`, `docs/deploy/vercel-steps.md`, `docs/deploy/langsmith-setup.md`, `docs/deploy/smoke-checklist.md`
  - `README.md`, `Dockerfile`, `render.yaml`
  - `src/tbwc/evals/RETRIEVER_ANALYSIS.md`, `data/eval/real_cards.json`
- [x] No secrets committed — `git grep` for `sk-…` / `tvly-…` patterns returns nothing.
- [x] `.env` is not tracked (only `.env.example` templates are committed).
- [x] Quality gates green: `uv run pytest` (367 passed, ~91% coverage), `uv run ruff check .`, `uv run ruff format --check .`.
- [x] Frontend production build passes: `cd frontend && npm run build`.
- [x] Backend container builds: `docker build -t tbwc .`.

## Manual steps remaining (require human action / live services)

These steps are intentionally **not** automated — they touch external services or require a live deploy + recording, and should be performed by a human before final submission:

1. **Deploy the backend** to Render using `render.yaml` — see `docs/deploy/render-steps.md`. Set the `sync: false` secrets (`OPENAI_API_KEY`, `TAVILY_API_KEY`, `LANGSMITH_API_KEY`, `CORS_ORIGINS`) in the Render dashboard.
2. **Deploy the frontend** to Vercel — see `docs/deploy/vercel-steps.md`. Set `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_WS_URL` to the Render backend URL. Then update the backend's `CORS_ORIGINS` to the Vercel URL and redeploy.
3. **Run the post-deploy smoke test**: `uv run python scripts/smoke_test.py --backend <render-url> --origin <vercel-url>` (see `docs/deploy/smoke-checklist.md`).
4. **Verify LangSmith traces** appear: `uv run python ops/verify_langsmith.py` with `LANGSMITH_*` env set (see `docs/deploy/langsmith-setup.md`).
5. **Regenerate live eval numbers** (need `OPENAI_API_KEY`):
   - `uv run python -m tbwc.evals.conclusions` → `data/eval/conclusions.md`
   - `uv run python -m tbwc.evals.retriever_ab` and `uv run python -m tbwc.evals.improvement_ab` → paste real numbers into `src/tbwc/evals/RETRIEVER_ANALYSIS.md` and `docs/WRITEUP.md` (currently marked as illustrative placeholders).
6. **Record the Loom demo** following `docs/loom-script.md`, then fill the Loom URL into `docs/WRITEUP.md` (Task 4) and `README.md`.
7. **Fill deployed URLs** into `docs/WRITEUP.md` (Task 4) and `README.md` (replace the placeholder `https://tbwc.vercel.app` / `https://tbwc-backend.onrender.com`).
8. **Make the GitHub repo public** (outward-facing — do this deliberately):
   ```bash
   gh repo view msharp9/a-thousand-blank-white-cards --json visibility -q .visibility
   gh repo edit msharp9/a-thousand-blank-white-cards --visibility public
   ```
9. **Final verification** once live:
   - `curl <render-url>/health` → `{"status":"ok"}`
   - `curl -I <vercel-url>` → `200`
   - Repo visibility → `PUBLIC`

## Submission artefacts

- **Repository:** `github.com/msharp9/a-thousand-blank-white-cards`
- **Write-up:** `docs/WRITEUP.md`
- **Deployed app:** _(fill after Vercel deploy)_
- **Loom demo (≤10 min):** _(fill after recording)_
