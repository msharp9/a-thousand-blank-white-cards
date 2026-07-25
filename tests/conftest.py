"""Shared pytest fixtures.

The whole suite must be hermetic with respect to the developer's ``.env``.
``config.Settings`` loads the repo-root ``.env`` (pydantic-settings
``env_file=".env"``), so a local ``.env`` — e.g. ``LLM_BASE_URL`` plus
``LLM_CHAT_MODEL`` / ``LLM_EMBEDDING_MODEL`` overrides for a local gateway —
would otherwise leak into tests and override the values individual tests set,
producing failures that only reproduce on machines configured for a gateway.

Env vars set via ``monkeypatch.setenv`` still win (they take precedence over the
``.env`` file), so tests that want a specific provider/model keep working; we
just stop the *file* from bleeding in. See bd a-thousand-blank-white-cards-9n4.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from agent.contract import InterpretResult
from config import Settings, get_settings
from models.effects import AddPointsOp, EffectProgram
from models.ws_messages import CreateCardMsg, StartMsg


def ready_card_result() -> InterpretResult:
    """Return a small, executable interpretation for setup-flow tests."""
    return InterpretResult(
        program=EffectProgram(ops=[AddPointsOp(target="self", amount=1)]),
        verdict="ok",
    )


def drive_to_playing(room, player_ids, cards_each: int = 5) -> None:
    """Drive a room through the two-step start flow to ``phase="playing"``.

    The start flow is: lobby -> (StartMsg) -> setup, where each non-spectator
    authors ``cards_each`` cards. Each card is drafted in the background and
    the room auto-starts after every required draft is executable.
    Only the ``player_ids`` passed in author cards (pass real, non-spectator
    ids). The first id acts as the host that sends both StartMsgs.
    """

    async def scenario() -> None:
        await room.handle_action(player_ids[0], StartMsg())
        for pid in player_ids:
            for i in range(cards_each):
                await room.handle_action(
                    pid,
                    CreateCardMsg(title=f"{pid}-card-{i}", description="gain 1 point"),
                )
        await room.wait_for_card_drafts()

    with patch("agent.runtime.run_agent", return_value=ready_card_result()):
        asyncio.run(scenario())


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the repo-root ``.env`` and reset the Settings cache.

    Disables pydantic-settings' ``.env`` file loading for the duration of each
    test (restored automatically by monkeypatch) so ``Settings()`` resolves from
    process env + declared defaults only. Also clears the ``get_settings`` cache
    before and after so a value set in one test never bleeds into the next.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    # Triage ships on by default, but it fires background LLM calls on any card
    # failure/no-op; keep it off unless a test opts in, so unrelated tests stay
    # hermetic (no network, no wish-file writes).
    monkeypatch.setenv("TRIAGE_AGENT_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
