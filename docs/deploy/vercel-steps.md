# Deploying the Frontend to Vercel

This guide walks through deploying the Next.js frontend (`frontend/`) to Vercel and
wiring it up to the backend hosted on Render.

## Prerequisites

- A [Vercel](https://vercel.com) account.
- The backend already deployed (e.g. `https://a-thousand-blank-white-cards.onrender.com`).
- Node.js installed locally (the steps below use `npx`, so no global install is needed).

## 1. Log in and link the project

From the repository root:

```bash
cd frontend
npx vercel login
npx vercel link
```

`vercel link` associates this local directory with a Vercel project. Accept the
prompts to create a new project (or link to an existing one). The project root is
`frontend/`, so run these commands from inside `frontend/`.

## 2. Set production environment variables

The frontend reads two public env vars at build time. Set them in Vercel for the
`production` environment:

```bash
npx vercel env add NEXT_PUBLIC_API_URL production
# value: https://a-thousand-blank-white-cards.onrender.com

npx vercel env add NEXT_PUBLIC_WS_URL production
# value: wss://a-thousand-blank-white-cards.onrender.com
```

Notes:

- `NEXT_PUBLIC_API_URL` is the REST API base URL (no trailing slash), served over
  HTTPS in production.
- `NEXT_PUBLIC_WS_URL` is the WebSocket base URL. It must use `wss://` (secure) in
  production, since the page is served over HTTPS and browsers block mixed-content
  `ws://` connections.
- These are `NEXT_PUBLIC_*` variables, so they are inlined into the client bundle at
  build time. Changing them requires a rebuild/redeploy.

You can add the same variables for the `preview` and `development` environments if you
want preview deployments to point at the production (or a staging) backend.

## 3. Deploy to production

```bash
npx vercel --prod
```

This builds and deploys the frontend. When it finishes, Vercel prints the production
URL (e.g. `https://tbwc-frontend.vercel.app`). Note this URL — you need it for the
next step.

## 4. Update the backend CORS allow-list

The backend restricts cross-origin requests via its `CORS_ORIGINS` env var. After the
first deploy, add the Vercel URL so the browser can call the API and open the
WebSocket.

On Render, update the backend service's `CORS_ORIGINS` environment variable to a JSON
array that includes the Vercel URL, for example:

```json
["http://localhost:3000", "https://tbwc-frontend.vercel.app"]
```

Then **redeploy the backend** on Render so the new value takes effect.

If you later add a custom domain in Vercel, add that origin to `CORS_ORIGINS` as well.

## WebSocket note: no Vercel proxy

The frontend connects to the backend WebSocket **directly** using the `wss://` URL from
`NEXT_PUBLIC_WS_URL`. It does **not** proxy WebSocket traffic through Vercel.

This is intentional: Vercel cannot proxy WebSocket upgrade (HTTP 101) requests, so
there is deliberately **no `/ws` rewrite** in `frontend/next.config.ts`. Adding one
would break the connection. The browser talks straight to the Render backend over
`wss://`.

## Redeploys

- Pushing to the linked Git branch (if the Vercel Git integration is enabled) triggers
  automatic deployments.
- Manual production deploys: `npx vercel --prod`.
- Manual preview deploys: `npx vercel`.
- Changing any `NEXT_PUBLIC_*` env var requires a redeploy to take effect.
