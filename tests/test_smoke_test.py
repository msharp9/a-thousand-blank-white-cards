"""Tests for scripts/smoke_test.py.

Unit-level only: flag parsing, check-matrix assembly (requested/skip gating),
and each check's happy/sad path with the network (httpx, websockets) and the
provider modules (agent.tools.web_search, agent.llm) mocked. Never hits a
real network or a real deployed stack.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_test.py"


def _load():
    spec = importlib.util.spec_from_file_location("smoke_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register in sys.modules before exec: the module's dataclass has a string
    # (PEP 563) field annotation, and dataclasses resolves those via
    # sys.modules[cls.__module__] — it must exist by the time exec_module runs.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._json


def fake_async_client(responses):
    """Return a factory standing in for httpx.AsyncClient, popping canned responses."""

    class _Client:
        def __init__(self, *args, **kwargs):
            self._responses = list(responses)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, *args, **kwargs):
            return self._responses.pop(0)

        async def post(self, *args, **kwargs):
            return self._responses.pop(0)

        async def options(self, *args, **kwargs):
            return self._responses.pop(0)

    return _Client


class FakeWS:
    def __init__(self, replies):
        self._replies = list(replies)
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def send(self, message):
        self.sent.append(message)

    async def recv(self):
        return self._replies.pop(0)


# --- flag parsing ------------------------------------------------------------


def test_parse_args_defaults() -> None:
    mod = _load()
    args = mod.parse_args(["--backend", "http://backend.example"])
    assert args.backend == "http://backend.example"
    assert args.frontend is None
    assert args.origin is None
    assert args.check_tavily is False
    assert args.check_langsmith is False
    assert args.check_llm is False
    assert args.skip == ""


def test_parse_args_all_flags() -> None:
    mod = _load()
    args = mod.parse_args(
        [
            "--backend",
            "http://backend.example",
            "--frontend",
            "http://frontend.example",
            "--origin",
            "http://custom-origin.example",
            "--check-tavily",
            "--check-langsmith",
            "--check-llm",
            "--skip",
            "ws,cors",
        ]
    )
    assert args.frontend == "http://frontend.example"
    assert args.origin == "http://custom-origin.example"
    assert args.check_tavily is True
    assert args.check_langsmith is True
    assert args.check_llm is True
    assert args.skip == "ws,cors"


def test_parse_args_requires_backend() -> None:
    mod = _load()
    with pytest.raises(SystemExit):
        mod.parse_args([])


# --- check-matrix assembly ---------------------------------------------------


def test_matrix_backend_only_requests_health_and_ws() -> None:
    mod = _load()
    args = mod.parse_args(["--backend", "http://b.example"])
    checks = {c.name: c.requested for c in mod.build_checks(args)}
    assert checks == {
        "health": True,
        "cors": False,
        "ws": True,
        "frontend": False,
        "wiring": False,
        "tavily": False,
        "langsmith": False,
        "llm": False,
    }


def test_matrix_frontend_enables_cors_frontend_and_wiring() -> None:
    mod = _load()
    args = mod.parse_args(["--backend", "http://b.example", "--frontend", "http://f.example"])
    checks = {c.name: c.requested for c in mod.build_checks(args)}
    assert checks["cors"] is True
    assert checks["frontend"] is True
    assert checks["wiring"] is True


def test_matrix_explicit_origin_enables_cors_without_frontend() -> None:
    mod = _load()
    args = mod.parse_args(["--backend", "http://b.example", "--origin", "http://o.example"])
    checks = {c.name: c.requested for c in mod.build_checks(args)}
    assert checks["cors"] is True
    assert checks["frontend"] is False
    assert checks["wiring"] is False


def test_matrix_skip_disables_named_checks() -> None:
    mod = _load()
    args = mod.parse_args(
        ["--backend", "http://b.example", "--frontend", "http://f.example", "--skip", "health,wiring"]
    )
    checks = {c.name: c.requested for c in mod.build_checks(args)}
    assert checks["health"] is False
    assert checks["wiring"] is False
    assert checks["ws"] is True
    assert checks["frontend"] is True


def test_matrix_opt_in_checks_require_their_flag() -> None:
    mod = _load()
    args = mod.parse_args(["--backend", "http://b.example", "--check-tavily", "--check-langsmith", "--check-llm"])
    checks = {c.name: c.requested for c in mod.build_checks(args)}
    assert checks["tavily"] is True
    assert checks["langsmith"] is True
    assert checks["llm"] is True


def test_matrix_opt_in_checks_still_skippable() -> None:
    mod = _load()
    args = mod.parse_args(["--backend", "http://b.example", "--check-tavily", "--skip", "tavily"])
    checks = {c.name: c.requested for c in mod.build_checks(args)}
    assert checks["tavily"] is False


# --- run(): exit code + skip/pass/fail reporting -----------------------------


def test_run_exit_zero_when_all_requested_checks_pass(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    mod = _load()
    monkeypatch.setattr(mod, "check_health", lambda backend: _resolved(True))
    monkeypatch.setattr(mod, "check_ws", lambda backend: _resolved(True))
    args = mod.parse_args(["--backend", "http://b.example"])
    exit_code = asyncio.run(mod.run(args))
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "health: PASS" in out
    assert "ws: PASS" in out
    assert "cors: SKIP" in out
    assert "tavily: SKIP" in out


def test_run_exit_nonzero_when_a_requested_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setattr(mod, "check_health", lambda backend: _resolved(True))
    monkeypatch.setattr(mod, "check_ws", lambda backend: _resolved(False))
    args = mod.parse_args(["--backend", "http://b.example"])
    assert asyncio.run(mod.run(args)) == 1


def test_run_skip_never_fails_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """A check that would fail if run doesn't affect the exit code when skipped."""
    mod = _load()
    monkeypatch.setattr(mod, "check_health", lambda backend: _resolved(True))
    monkeypatch.setattr(mod, "check_ws", lambda backend: _resolved(True))
    args = mod.parse_args(["--backend", "http://b.example", "--check-tavily", "--skip", "tavily"])
    assert asyncio.run(mod.run(args)) == 0


async def _resolved(value: bool) -> bool:
    return value


# --- each check: importable + happy/sad path ---------------------------------


def test_all_checks_are_callable() -> None:
    mod = _load()
    for name in (
        "check_health",
        "check_cors",
        "check_ws",
        "check_frontend",
        "check_wiring",
        "check_tavily",
        "check_langsmith",
        "check_llm",
    ):
        assert callable(getattr(mod, name))


def test_check_health_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setattr(mod.httpx, "AsyncClient", fake_async_client([FakeResponse(200, {"status": "ok"})]))
    assert asyncio.run(mod.check_health("http://b.example")) is True


def test_check_health_fail_wrong_body(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setattr(mod.httpx, "AsyncClient", fake_async_client([FakeResponse(200, {"status": "degraded"})]))
    assert asyncio.run(mod.check_health("http://b.example")) is False


def test_check_health_fail_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            raise RuntimeError("network down")

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Boom)
    assert asyncio.run(mod.check_health("http://b.example")) is False


def test_check_cors_pass_exact_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    origin = "http://f.example"
    monkeypatch.setattr(
        mod.httpx,
        "AsyncClient",
        fake_async_client([FakeResponse(200, headers={"access-control-allow-origin": origin})]),
    )
    assert asyncio.run(mod.check_cors("http://b.example", origin)) is True


def test_check_cors_pass_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setattr(
        mod.httpx, "AsyncClient", fake_async_client([FakeResponse(200, headers={"access-control-allow-origin": "*"})])
    )
    assert asyncio.run(mod.check_cors("http://b.example", "http://f.example")) is True


def test_check_cors_fail_mismatched_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setattr(
        mod.httpx,
        "AsyncClient",
        fake_async_client([FakeResponse(200, headers={"access-control-allow-origin": "http://other.example"})]),
    )
    assert asyncio.run(mod.check_cors("http://b.example", "http://f.example")) is False


def test_check_ws_pass_on_state_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setattr(
        mod.httpx,
        "AsyncClient",
        fake_async_client(
            [
                FakeResponse(200, {"code": "ABCDEF"}),
                FakeResponse(200, {"code": "ABCDEF", "player_id": "p1"}),
            ]
        ),
    )
    reply = json.dumps({"type": "state", "state": {"room_code": "ABCDEF"}})
    monkeypatch.setattr(mod.websockets, "connect", lambda url: FakeWS([reply]))
    assert asyncio.run(mod.check_ws("http://b.example")) is True


def test_check_ws_fail_on_non_state_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setattr(
        mod.httpx,
        "AsyncClient",
        fake_async_client(
            [
                FakeResponse(200, {"code": "ABCDEF"}),
                FakeResponse(200, {"code": "ABCDEF", "player_id": "p1"}),
            ]
        ),
    )
    reply = json.dumps({"type": "error", "message": "nope"})
    monkeypatch.setattr(mod.websockets, "connect", lambda url: FakeWS([reply]))
    assert asyncio.run(mod.check_ws("http://b.example")) is False


def test_check_ws_fail_when_room_setup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, *a, **kw):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Boom)
    assert asyncio.run(mod.check_ws("http://b.example")) is False


def test_check_frontend_pass_with_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    html = f"<html><head><title>{mod.FRONTEND_MARKER}</title></head></html>"
    monkeypatch.setattr(mod.httpx, "AsyncClient", fake_async_client([FakeResponse(200, text=html)]))
    assert asyncio.run(mod.check_frontend("http://f.example")) is True


def test_check_frontend_fail_missing_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setattr(
        mod.httpx, "AsyncClient", fake_async_client([FakeResponse(200, text="<html>nothing here</html>")])
    )
    assert asyncio.run(mod.check_frontend("http://f.example")) is False


def test_check_frontend_fail_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setattr(mod.httpx, "AsyncClient", fake_async_client([FakeResponse(500, text=mod.FRONTEND_MARKER)]))
    assert asyncio.run(mod.check_frontend("http://f.example")) is False


def test_check_wiring_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setattr(
        mod.httpx,
        "AsyncClient",
        fake_async_client(
            [FakeResponse(200, {"code": "ABCDEF"}, headers={"access-control-allow-origin": "http://f.example"})]
        ),
    )
    assert asyncio.run(mod.check_wiring("http://b.example", "http://f.example")) is True


def test_check_wiring_fail_cors_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setattr(
        mod.httpx,
        "AsyncClient",
        fake_async_client([FakeResponse(200, {"code": "ABCDEF"}, headers={})]),
    )
    assert asyncio.run(mod.check_wiring("http://b.example", "http://f.example")) is False


def test_check_wiring_fail_no_code(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setattr(
        mod.httpx,
        "AsyncClient",
        fake_async_client([FakeResponse(200, {}, headers={"access-control-allow-origin": "http://f.example"})]),
    )
    assert asyncio.run(mod.check_wiring("http://b.example", "http://f.example")) is False


# --- tavily / langsmith / llm (flag-gated) checks ----------------------------


def test_check_tavily_fail_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert asyncio.run(mod.check_tavily()) is False


def test_check_tavily_fail_on_fallback_result(monkeypatch: pytest.MonkeyPatch) -> None:
    # Import the submodule directly (bound by the `as` clause, bypassing
    # `agent.<name>` package-attribute lookup) — some other test in the suite
    # pops "agent" from sys.modules, which would otherwise break dotted-string
    # monkeypatch targets like "agent.tools.web_search.web_search".
    import agent.tools.web_search as web_search_mod

    mod = _load()
    monkeypatch.setenv("TAVILY_API_KEY", "tv-key")
    monkeypatch.setattr(
        web_search_mod, "web_search", SimpleNamespace(invoke=lambda payload: web_search_mod._UNAVAILABLE)
    )
    assert asyncio.run(mod.check_tavily()) is False


def test_check_tavily_pass_on_real_result(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.tools.web_search as web_search_mod

    mod = _load()
    monkeypatch.setenv("TAVILY_API_KEY", "tv-key")
    monkeypatch.setattr(
        web_search_mod,
        "web_search",
        SimpleNamespace(invoke=lambda payload: "OpenAI — the AI research company — https://openai.com"),
    )
    assert asyncio.run(mod.check_tavily()) is True


def test_check_langsmith_fail_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert asyncio.run(mod.check_langsmith()) is False


def test_check_langsmith_pass_when_ping_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    monkeypatch.setattr(mod.httpx, "AsyncClient", fake_async_client([FakeResponse(200, [])]))
    assert asyncio.run(mod.check_langsmith()) is True


def test_check_langsmith_fail_when_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load()
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "bad-key")
    monkeypatch.setattr(mod.httpx, "AsyncClient", fake_async_client([FakeResponse(401, {})]))
    assert asyncio.run(mod.check_langsmith()) is False


def test_check_llm_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.llm as llm_mod

    mod = _load()

    class _FakeBound:
        async def ainvoke(self, message):
            return SimpleNamespace(content="hi")

    class _FakeModel:
        def bind(self, **kwargs):
            assert kwargs == {"max_tokens": 1}
            return _FakeBound()

    monkeypatch.setattr(llm_mod, "get_chat_model", lambda: _FakeModel())
    assert asyncio.run(mod.check_llm()) is True


def test_check_llm_fail_on_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.llm as llm_mod

    mod = _load()

    class _FakeBound:
        async def ainvoke(self, message):
            raise RuntimeError("provider unreachable")

    class _FakeModel:
        def bind(self, **kwargs):
            return _FakeBound()

    monkeypatch.setattr(llm_mod, "get_chat_model", lambda: _FakeModel())
    assert asyncio.run(mod.check_llm()) is False
