"""Shared pytest fixtures.

The whole suite must be hermetic with respect to the developer's ``.env``.
``tbwc.config.Settings`` loads the repo-root ``.env`` (pydantic-settings
``env_file=".env"``), so a local ``.env`` — e.g. ``LLM_PROVIDER=ollama`` plus
``OPENAI_CHAT_MODEL`` / ``OPENAI_EMBEDDING_MODEL`` overrides — would otherwise
leak into tests and override the values individual tests set, producing failures
that only reproduce on machines configured for the local Ollama backend.

Env vars set via ``monkeypatch.setenv`` still win (they take precedence over the
``.env`` file), so tests that want a specific provider/model keep working; we
just stop the *file* from bleeding in. See bd a-thousand-blank-white-cards-9n4.
"""

from __future__ import annotations

import asyncio

import pytest

from tbwc.config import Settings, get_settings
from tbwc.models.ws_messages import CreateCardMsg, StartMsg


def drive_to_playing(room, player_ids, cards_each: int = 5) -> None:
    """Drive a room through the two-step start flow to ``phase="playing"``.

    The new start flow is: lobby -> (StartMsg) -> setup, where each
    non-spectator authors ``cards_each`` cards, then -> (StartMsg) -> playing.
    Only the ``player_ids`` passed in author cards (pass real, non-spectator
    ids). The first id acts as the host that sends both StartMsgs.
    """
    # lobby -> setup
    asyncio.run(room.handle_action(player_ids[0], StartMsg()))
    # each player authors the required number of cards during setup
    for pid in player_ids:
        for i in range(cards_each):
            asyncio.run(
                room.handle_action(
                    pid,
                    CreateCardMsg(title=f"{pid}-card-{i}", description="gain 1 point"),
                )
            )
    # host starts again -> playing
    asyncio.run(room.handle_action(player_ids[0], StartMsg()))


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the repo-root ``.env`` and reset the Settings cache.

    Disables pydantic-settings' ``.env`` file loading for the duration of each
    test (restored automatically by monkeypatch) so ``Settings()`` resolves from
    process env + declared defaults only. Also clears the ``get_settings`` cache
    before and after so a value set in one test never bleeds into the next.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
