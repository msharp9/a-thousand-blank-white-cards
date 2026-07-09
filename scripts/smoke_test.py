#!/usr/bin/env python3
"""Smoke test for a deployed TBWC backend.

Usage:
    uv run python scripts/smoke_test.py --backend https://tbwc-backend.onrender.com [--origin https://tbwc.vercel.app]

Probes GET /health, a CORS preflight, and a WebSocket connect+join. Exit 0 if all
critical checks pass, else 1.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx


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
    # Derive ws URL from the http(s) backend URL.
    ws_base = backend.replace("https://", "wss://").replace("http://", "ws://")
    # Create a room first so we have a valid code.
    print("[ws] creating a room via POST /rooms")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{backend}/rooms", timeout=15.0)
        code = resp.json()["code"]
    except Exception as exc:
        print(f"  -> could not create room: {exc}")
        return False

    import websockets

    url = f"{ws_base}/ws/{code}"
    print(f"[ws] connect {url}")
    try:
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"type": "join", "player_id": None, "name": "smoke"}))
            reply = await asyncio.wait_for(ws.recv(), timeout=10.0)
        msg = json.loads(reply)
        # Server replies (likely an error for null player_id) — a reply proves the WS works.
        ok = "type" in msg
        print(f"  -> reply type={msg.get('type')} {'OK' if ok else 'FAIL'}")
        return ok
    except Exception as exc:
        print(f"  -> ERROR {exc}")
        return False


async def run(backend: str, origin: str | None) -> int:
    backend = backend.rstrip("/")
    results: list[tuple[str, bool]] = []
    results.append(("health", await check_health(backend)))
    if origin:
        results.append(("cors", await check_cors(backend, origin)))
    results.append(("ws", await check_ws(backend)))

    print("\n=== Smoke test summary ===")
    for name, ok in results:
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(ok for _, ok in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test a deployed TBWC backend.")
    parser.add_argument("--backend", required=True, help="Backend base URL, e.g. https://tbwc-backend.onrender.com")
    parser.add_argument("--origin", default=None, help="Expected CORS origin (Vercel URL)")
    args = parser.parse_args()
    return asyncio.run(run(args.backend, args.origin))


if __name__ == "__main__":
    raise SystemExit(main())
