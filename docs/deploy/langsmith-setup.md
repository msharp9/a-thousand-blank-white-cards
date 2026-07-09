# LangSmith Tracing Setup

This guide walks through enabling [LangSmith](https://smith.langchain.com) tracing
for the tbwc card-interpretation agent in production (Render). With tracing enabled,
every card interpretation emits a hierarchical trace so you can inspect the
`reason → retrieve → classify → emit_ops/gen_snippet → judge` pipeline, latency,
token usage, and any errors.

## Background

The LangChain/LangSmith SDK reads configuration from environment variables. It
supports both the legacy `LANGCHAIN_*` names and the newer `LANGSMITH_*` names.
tbwc's `Settings` (`src/tbwc/config.py`) exposes both conventions; new deployments
should prefer the `LANGSMITH_*` variables:

| Variable             | Purpose                                   | Default                          |
| -------------------- | ----------------------------------------- | -------------------------------- |
| `LANGSMITH_TRACING`  | Master on/off switch for tracing          | `false`                          |
| `LANGSMITH_API_KEY`  | Auth token for the LangSmith API          | *(empty)*                        |
| `LANGSMITH_PROJECT`  | Project traces are grouped under          | `tbwc-dev`                       |
| `LANGSMITH_ENDPOINT` | LangSmith API endpoint (self-host/EU/US)  | `https://api.smith.langchain.com`|

## 1. Sign up and create a project

1. Go to <https://smith.langchain.com> and sign up (or log in).
2. In the left sidebar, open **Projects** → **+ New Project**.
3. Name the project `tbwc-prod` (matching the `LANGSMITH_PROJECT` you will set on
   the production service). Traces from your Render deployment will land here.

## 2. Create an API key

1. Open **Settings** → **API Keys** (or click your avatar → **Settings**).
2. Click **Create API Key**. Give it a descriptive name such as `tbwc-render-prod`.
3. Copy the key immediately — it is shown only once. It looks like `lsv2_...`.

## 3. Configure Render environment variables

In the Render dashboard, open the tbwc backend service → **Environment** and add:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...          # the key you just created
LANGSMITH_PROJECT=tbwc-prod
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

Notes:

- Keep `LANGSMITH_API_KEY` as a **secret** env var — never commit it.
- Only override `LANGSMITH_ENDPOINT` if you use LangSmith EU or a self-hosted
  instance; otherwise the default is correct.
- The legacy `LANGCHAIN_*` variables can be left unset; the SDK honors either set,
  and the app logs based on `LANGSMITH_TRACING`.

## 4. Redeploy

Save the environment changes and trigger a redeploy (Render prompts for this, or
use **Manual Deploy** → **Deploy latest commit**). The new env vars take effect on
the next boot.

## 5. Confirm tracing is enabled in the logs

On startup the app logs its LangSmith status (see the `lifespan` handler in
`src/tbwc/app.py`). Open the Render service **Logs** and look for:

```
LangSmith tracing ENABLED project=tbwc-prod endpoint=https://api.smith.langchain.com
```

If instead you see:

```
LangSmith tracing DISABLED — set LANGSMITH_TRACING=true to enable
```

then `LANGSMITH_TRACING` is not set to `true` — recheck step 3 and redeploy.

## 6. Verify a trace appears

1. Exercise the agent by interpreting at least one card (play a card in a live game,
   or hit the interpretation endpoint).
2. In LangSmith, open the `tbwc-prod` project → **Traces**.
3. You should see a new run. Expand it and confirm the child spans for the graph
   nodes appear in order:

   ```
   reason → retrieve → classify → emit_ops / gen_snippet → judge
   ```

   (Depending on routing, a run resolves to either `emit_ops` or `gen_snippet`
   before `judge`.)

## Helper: `ops/verify_langsmith.py`

A convenience script is provided to sanity-check configuration and emit one sample
trace without needing a live game:

```bash
OPENAI_API_KEY=... LANGSMITH_API_KEY=... LANGSMITH_TRACING=true \
    LANGSMITH_PROJECT=tbwc-prod \
    uv run python ops/verify_langsmith.py
```

It validates the required env vars, runs the compiled graph on a sample card, and
prints a link to the project's Traces view along with the expected span order. Use
it locally (or in a one-off Render shell) to confirm end-to-end tracing before
relying on it in production.

## Troubleshooting

- **No traces show up but logs say ENABLED** — verify `LANGSMITH_API_KEY` is valid
  and that outbound HTTPS to `LANGSMITH_ENDPOINT` is allowed from Render.
- **Traces land in the wrong project** — the SDK falls back to `default` if neither
  `LANGSMITH_PROJECT` nor `LANGCHAIN_PROJECT` is set; confirm `LANGSMITH_PROJECT=tbwc-prod`.
- **Startup log missing entirely** — ensure the deploy picked up the latest commit
  containing the LangSmith startup log in `lifespan`.
