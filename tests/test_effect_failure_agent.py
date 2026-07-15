"""Tests for evals.effect_failure_agent (report mapping, fallback, scheduler)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from config import get_settings
from evals import effect_failure_agent as efa
from evals.effect_failure_agent import (
    EffectFailurePayload,
    EvalReport,
    build_report,
    get_scheduler,
    report_effect_failure,
    reset_scheduler,
    schedule_effect_failure_report,
)


def _payload(**overrides) -> EffectFailurePayload:
    base: dict = {
        "kind": "sandbox_failure",
        "card_title": "Auction",
        "card_description": "Highest bidder wins the pot.",
        "card_id": "card-1",
        "correlation_id": "corr-1",
    }
    base.update(overrides)
    return EffectFailurePayload(**base)


def _report() -> EvalReport:
    return EvalReport(
        diagnosis="Sandbox crashed collecting sealed bids.",
        root_cause_bucket="sandbox_failure",
        what_the_card_wanted="Sealed bids from every player",
        missing_capability="sealed multiplayer numeric input",
        recommendation="Add a sealed-bid interaction primitive",
        severity="medium",
        confidence=0.8,
    )


@pytest.fixture
def wish_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "wishes.jsonl"
    monkeypatch.setenv("CAPABILITY_WISH_PATH", str(path))
    get_settings.cache_clear()
    return path


def test_report_maps_to_wish_fields(wish_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(efa, "build_report", lambda payload, *, model=None: _report())

    result = asyncio.run(report_effect_failure(_payload()))

    assert result["recorded"] is True
    lines = wish_path.read_text().splitlines()
    assert len(lines) == 1
    stored = json.loads(lines[0])
    assert stored["card_title"] == "Auction"
    assert stored["card_description"] == "Highest bidder wins the pot."
    assert stored["what_i_wanted"] == (
        "[sandbox_failure] Sealed bids from every player — recommendation: Add a sealed-bid interaction primitive"
    )
    assert stored["missing_capability"] == "sealed multiplayer numeric input"
    assert len(stored["missing_capability"]) <= 240


def test_llm_down_yields_deterministic_report_and_still_records_wish(
    wish_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(efa, "get_chat_model", MagicMock(side_effect=RuntimeError("gateway down")))
    payload = _payload(kind="hook_failure", exception="boom")

    report = build_report(payload)
    assert report.root_cause_bucket == "hook_failure"
    assert report.confidence == 0.0
    assert report.severity == "low"
    assert "boom" in report.diagnosis

    result = asyncio.run(report_effect_failure(payload))
    assert result["recorded"] is True
    stored = json.loads(wish_path.read_text().splitlines()[0])
    assert stored["what_i_wanted"].startswith("[hook_failure]")


def test_fallback_coerces_unknown_kind_to_sandbox_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(efa, "get_chat_model", MagicMock(side_effect=RuntimeError("down")))
    report = build_report(_payload(kind="something_new"))
    assert report.root_cause_bucket == "sandbox_failure"


def test_report_effect_failure_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(efa, "build_report", MagicMock(side_effect=RuntimeError("explode")))
    result = asyncio.run(report_effect_failure(_payload()))
    assert result == {"recorded": False, "error": "explode"}


def test_dedupe_key() -> None:
    payload = _payload(card_id="c-42", kind="no_op")
    assert payload.dedupe_key == ("c-42", "no_op")


def test_scheduler_caps_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL_AGENT_MAX_CONCURRENCY", "2")
    get_settings.cache_clear()
    reset_scheduler()

    async def main() -> None:
        release = asyncio.Event()
        running = 0
        peak = 0
        completed = 0

        async def worker() -> None:
            nonlocal running, peak, completed
            running += 1
            peak = max(peak, running)
            await release.wait()
            running -= 1
            completed += 1

        scheduler = get_scheduler()
        for _ in range(5):
            scheduler.schedule(worker)
        assert completed == 0
        await asyncio.sleep(0.05)
        assert peak == 2
        release.set()
        await scheduler.drain()
        assert completed == 5
        assert peak == 2

    asyncio.run(main())


def test_disabled_gate_schedules_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL_AGENT_ENABLED", "false")
    get_settings.cache_clear()
    reset_scheduler()
    with patch.object(efa.EvalAgentScheduler, "schedule") as mock_schedule:
        schedule_effect_failure_report(_payload())
        mock_schedule.assert_not_called()


def test_enabled_gate_schedules_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL_AGENT_ENABLED", "true")
    get_settings.cache_clear()
    reset_scheduler()
    with patch.object(efa.EvalAgentScheduler, "schedule") as mock_schedule:
        schedule_effect_failure_report(_payload())
        mock_schedule.assert_called_once()


def test_schedule_without_running_loop_drops_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_scheduler()
    scheduler = get_scheduler()
    scheduler.schedule(lambda: asyncio.sleep(0))
    assert scheduler._tasks == set()
