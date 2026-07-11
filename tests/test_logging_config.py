"""Tests for logging_config.configure_logging."""

from __future__ import annotations

import logging

from logging_config import _TBWC_HANDLER_NAME, configure_logging, log_game_state


def _tbwc_handlers() -> list[logging.Handler]:
    root = logging.getLogger()
    return [h for h in root.handlers if getattr(h, "name", None) == _TBWC_HANDLER_NAME]


def test_configure_logging_runs_and_sets_level() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger("tbwc").level == logging.DEBUG
    assert len(_tbwc_handlers()) == 1


def test_configure_logging_is_idempotent() -> None:
    configure_logging()
    configure_logging()
    configure_logging()
    # Repeat calls swap the handler rather than stacking duplicates.
    assert len(_tbwc_handlers()) == 1


def test_configure_logging_default_level_from_settings() -> None:
    configure_logging()  # no explicit level -> settings.log_level (default INFO)
    assert logging.getLogger("tbwc").level == logging.INFO


def test_log_record_is_formatted(caplog) -> None:
    configure_logging("INFO")
    handler = _tbwc_handlers()[0]
    record = logging.LogRecord(
        name="tbwc.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    formatted = handler.format(record)
    assert "INFO" in formatted
    assert "tbwc.test" in formatted
    assert "hello world" in formatted


def test_log_game_state_helper_does_not_raise() -> None:
    class _FakeState:
        room_code = "ABCDEF"
        phase = "lobby"
        turn_index = 0
        players: list = []

    # Just ensure the opt-in helper runs cleanly.
    log_game_state(_FakeState())
