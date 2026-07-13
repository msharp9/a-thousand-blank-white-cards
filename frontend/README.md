# tbwc-frontend

The Next.js frontend for **1000 Blank White Cards** — a digital, AI-supported implementation of the improv party game. See the root [`../README.md`](../README.md) for the full project.

## What it is

A phase-router UI for a game room. `app/room/[code]/page.tsx` renders the appropriate view — **lobby / setup / playing / epilogue / ended** — based on the game's current phase. State is driven by a live WebSocket connection to the backend via `useGameSocket` in `lib/ws.ts`. Card-defined player input stays inside those phases as a global `InteractionPanel` overlay, with choice, number, text, card-pick, confirmation, and bounded vector-drawing renderers.

## Local development

The backend must be running first — see the backend quickstart in the root [`../README.md`](../README.md).

```bash
cd frontend
npm install
cp .env.example .env.local   # set NEXT_PUBLIC_API_URL and NEXT_PUBLIC_WS_URL
npm run dev                  # http://localhost:3000
```

Environment variables:

| Variable              | Purpose            | Example                 |
| --------------------- | ------------------ | ----------------------- |
| `NEXT_PUBLIC_API_URL` | REST base URL      | `http://localhost:8000` |
| `NEXT_PUBLIC_WS_URL`  | WebSocket base URL | `ws://localhost:8000`   |

## Scripts

| Command              | Description                 |
| -------------------- | --------------------------- |
| `npm run dev`        | Start the dev server        |
| `npm run build`      | Production build            |
| `npm run start`      | Serve the production build  |
| `npm run typecheck`  | Type-check (`tsc --noEmit`) |
| `npm run lint`       | Lint (`eslint`)             |
| `npm run format`     | Format (`prettier --write`) |
| `npm test`           | Run Vitest + RTL tests      |
| `npm run test:watch` | Run tests in watch mode     |

## Deployment

Deployed to Vercel. See [`../docs/deploy/vercel-steps.md`](../docs/deploy/vercel-steps.md).

## ⚠️ Non-standard Next.js

This is **NOT the Next.js you know** — APIs, conventions, and file structure may differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. See [`AGENTS.md`](./AGENTS.md).
