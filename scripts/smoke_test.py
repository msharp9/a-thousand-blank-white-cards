#!/usr/bin/env python3
"""Smoke test for a deployed TBWC stack (backend, frontend, and their wiring).

Usage:
    uv run python scripts/smoke_test.py --backend https://tbwc-backend.onrender.com \\
        --frontend https://tbwc.vercel.app

    # opt in to checks that spend quota / hit third-party APIs
    uv run python scripts/smoke_test.py --backend https://tbwc-backend.onrender.com \\
        --check-tavily --check-langsmith --check-llm

    # skip a normally-on check
    uv run python scripts/smoke_test.py --backend https://tbwc-backend.onrender.com --skip cors,ws

Probes a RUNNING stack over the network via URLs — it never imports
board/app.py or otherwise stands up the app in-process. Importing config /
agent tool modules for key-presence and provider-reachability checks is fine
(tavily/langsmith/llm) since those need the same client code the app uses.

Checks (see build_checks() for exact gating):
  health    GET /health on the backend.
  cors      OPTIONS preflight against the backend from --origin (or
            --frontend). Only requested if an origin is resolvable.
  ws        Full round-trip: POST /rooms, POST /rooms/{code}/join, connect
            the WebSocket, send a join envelope, assert a state snapshot
            arrives.
  frontend  GET --frontend, expect 200 + a recognizable page marker.
  wiring    POST /rooms against the backend with Origin: <frontend origin>;
            asserts CORS allows it and a room code comes back. Only
            requested when --frontend is given (proves the pair deploys
            together).
  tavily    --check-tavily: tavily_api_key is configured AND a live search
            through agent.tools.web_search returns a real (non-fallback)
            result.
  langsmith --check-langsmith: langsmith_tracing + langsmith_api_key are
            configured AND a cheap authenticated call to the LangSmith API
            succeeds.
  llm       --check-llm: a one-token chat completion succeeds through
            agent.llm.get_chat_model.

Exit 0 iff every REQUESTED check passes (skipped checks never affect the
exit code); exit 1 if any requested check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import websockets

FRONTEND_MARKER = "1000 Blank White Cards"
LANGSMITH_PING_PATH = "/api/v1/sessions"


def _origin_of(url: str) -> str:
    """Reduce a URL to its scheme+host[:port] origin, e.g. for an Origin header."""
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}"


async def check_health(backend: str) -> bool:
    url = f"{backend}/health"
    print(f"[health] GET {url}")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=15.0)
        ok = resp.status_code == 200 and resp.json().get("status") == "ok"
        print(f"  -> {resp.status_code} {resp.json()} {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as exc:
        print(f"  -> ERROR {exc}")
        return False


async def check_cors(backend: str, origin: str) -> bool:
    url = f"{backend}/health"
    print(f"[cors] OPTIONS {url} Origin: {origin}")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.options(
                url,
                headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
                timeout=10.0,
            )
        acao = resp.headers.get("access-control-allow-origin")
        ok = acao in (origin, "*")
        print(f"  -> {resp.status_code} ACAO={acao} {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as exc:
        print(f"  -> ERROR {exc}")
        return False


async def check_ws(backend: str) -> bool:
    """Create a room, join via REST, connect the socket, and assert a state snapshot arrives."""
    ws_base = backend.replace("https://", "wss://").replace("http://", "ws://")
    print("[ws] creating a room via POST /rooms")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{backend}/rooms", timeout=15.0)
            code = resp.json()["code"]
            print(f"[ws] joining room {code} via POST /rooms/{code}/join")
            resp = await client.post(f"{backend}/rooms/{code}/join", json={"name": "smoke"}, timeout=15.0)
            player_id = resp.json()["player_id"]
    except Exception as exc:
        print(f"  -> could not create/join room: {exc}")
        return False

    url = f"{ws_base}/ws/{code}"
    print(f"[ws] connect {url}")
    try:
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"type": "join", "player_id": player_id, "name": "smoke"}))
            reply = await asyncio.wait_for(ws.recv(), timeout=10.0)
        msg = json.loads(reply)
        ok = msg.get("type") == "state" and isinstance(msg.get("state"), dict) and "room_code" in msg["state"]
        print(f"  -> reply type={msg.get('type')} {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as exc:
        print(f"  -> ERROR {exc}")
        return False


async def check_frontend(frontend: str) -> bool:
    print(f"[frontend] GET {frontend}")
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(frontend, timeout=15.0)
        has_marker = FRONTEND_MARKER in resp.text
        ok = resp.status_code == 200 and has_marker
        print(f"  -> {resp.status_code} marker={'found' if has_marker else 'MISSING'} {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as exc:
        print(f"  -> ERROR {exc}")
        return False


async def check_wiring(backend: str, frontend: str) -> bool:
    """Prove the frontend origin can actually call the backend: CORS + a real room code."""
    origin = _origin_of(frontend)
    url = f"{backend}/rooms"
    print(f"[wiring] POST {url} Origin: {origin}")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers={"Origin": origin}, timeout=15.0)
        acao = resp.headers.get("access-control-allow-origin")
        code = resp.json().get("code") if resp.status_code == 200 else None
        ok = resp.status_code == 200 and acao in (origin, "*") and bool(code)
        print(f"  -> {resp.status_code} ACAO={acao} code={code} {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as exc:
        print(f"  -> ERROR {exc}")
        return False


async def check_tavily() -> bool:
    """Require a configured key AND a live search that isn't the fallback string."""
    from config import get_settings

    if not get_settings().tavily_api_key:
        print("[tavily] no tavily_api_key configured -> FAIL")
        return False

    from agent.tools.web_search import _UNAVAILABLE, web_search

    print("[tavily] live search via agent.tools.web_search")
    try:
        result = await asyncio.to_thread(web_search.invoke, {"query": "OpenAI"})
        ok = bool(result) and result != _UNAVAILABLE
        print(f"  -> {result[:120]!r} {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as exc:
        print(f"  -> ERROR {exc}")
        return False


async def check_langsmith() -> bool:
    """Require tracing config present AND a cheap authenticated call to the LangSmith API."""
    from config import get_settings

    settings = get_settings()
    if not (settings.langsmith_tracing and settings.langsmith_api_key):
        print("[langsmith] langsmith_tracing/langsmith_api_key not configured -> FAIL")
        return False

    url = f"{settings.langsmith_endpoint}{LANGSMITH_PING_PATH}"
    print(f"[langsmith] GET {url}")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={"x-api-key": settings.langsmith_api_key},
                params={"limit": 1},
                timeout=15.0,
            )
        ok = resp.status_code == 200
        print(f"  -> {resp.status_code} {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as exc:
        print(f"  -> ERROR {exc}")
        return False


async def check_llm() -> bool:
    """Require the configured provider to answer a minimal (one-token) chat call."""
    from agent.llm import get_chat_model

    print("[llm] one-token chat completion via agent.llm.get_chat_model")
    try:
        model = get_chat_model().bind(max_tokens=1)
        reply = await model.ainvoke("hi")
        ok = reply is not None
        print(f"  -> received reply {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as exc:
        print(f"  -> ERROR {exc}")
        return False


@dataclass
class Check:
    """One matrix row: a name, whether the user actually wants it run, and how to run it."""

    name: str
    requested: bool
    run: Callable[[], Awaitable[bool]]


def build_checks(args: argparse.Namespace) -> list[Check]:
    """Assemble the full check matrix (skipped/not-applicable checks are still listed).

    Pure and side-effect free: `run` callables are closures that perform the
    actual network calls only when invoked, so this can be inspected in tests
    without touching the network.
    """
    skip = {name.strip() for name in args.skip.split(",") if name.strip()}
    frontend = args.frontend.rstrip("/") if args.frontend else None
    origin = args.origin or frontend
    backend = args.backend.rstrip("/")

    return [
        Check("health", "health" not in skip, lambda: check_health(backend)),
        Check("cors", origin is not None and "cors" not in skip, lambda: check_cors(backend, origin)),
        Check("ws", "ws" not in skip, lambda: check_ws(backend)),
        Check("frontend", frontend is not None and "frontend" not in skip, lambda: check_frontend(frontend)),
        Check(
            "wiring",
            frontend is not None and "wiring" not in skip,
            lambda: check_wiring(backend, frontend),
        ),
        Check("tavily", args.check_tavily and "tavily" not in skip, check_tavily),
        Check("langsmith", args.check_langsmith and "langsmith" not in skip, check_langsmith),
        Check("llm", args.check_llm and "llm" not in skip, check_llm),
    ]


async def run(args: argparse.Namespace) -> int:
    checks = build_checks(args)
    results: list[tuple[str, str]] = []
    for check in checks:
        if not check.requested:
            results.append((check.name, "SKIP"))
            continue
        try:
            ok = await check.run()
        except Exception as exc:
            print(f"[{check.name}] unhandled ERROR {exc}")
            ok = False
        results.append((check.name, "PASS" if ok else "FAIL"))

    print("\n=== Smoke test summary ===")
    for name, status in results:
        print(f"  {name}: {status}")
    return 0 if all(status != "FAIL" for _, status in results) else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test a deployed TBWC stack.")
    parser.add_argument("--backend", required=True, help="Backend base URL, e.g. https://tbwc-backend.onrender.com")
    parser.add_argument("--frontend", default=None, help="Frontend base URL, e.g. https://tbwc.vercel.app")
    parser.add_argument("--origin", default=None, help="Expected CORS origin; defaults to --frontend if given")
    parser.add_argument("--check-tavily", action="store_true", help="Run a live Tavily search (spends quota)")
    parser.add_argument("--check-langsmith", action="store_true", help="Ping the LangSmith API with the configured key")
    parser.add_argument("--check-llm", action="store_true", help="Run a one-token chat completion (spends quota)")
    parser.add_argument(
        "--skip",
        default="",
        help="Comma-separated checks to skip: health,cors,ws,frontend,wiring,tavily,langsmith,llm",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
