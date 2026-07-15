"""Deterministic, hermetic tests for the agent<->engine seam cluster (C6/C7/C9).

No network, no real LLM. Covers the two context tools (read_game_state factory +
read_engine_methods introspection) and their wiring into run_agent, plus the
persona/comment threading. The fake-chat-model approach mirrors
tests/test_agent_skeleton.py.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from agent.contract import InterpretResult
from agent.runtime import _assemble_tools, run_agent
from agent.tools.read_engine_methods import get_read_engine_methods_tool, read_engine_methods
from agent.tools.read_game_state import make_read_game_state_tool
from models.game_state import GameState, Player


class ToolAwareFake(GenericFakeChatModel):
    """A scripted fake chat model that also supports ``bind_tools`` (no-op)."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self


# ---------------------------------------------------------------------------
# langsmith env isolation (run_agent writes LANGSMITH_* env vars by design)
# ---------------------------------------------------------------------------
_LANGSMITH_ENV_KEYS = (
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _isolate_langsmith_env():
    import os

    saved = {k: os.environ.get(k) for k in _LANGSMITH_ENV_KEYS}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _sample_state() -> GameState:
    return GameState(
        room_code="ABCD",
        players=[
            Player(id="p1", name="Alice", score=3, hand=["c1"]),
            Player(id="p2", name="Bob", score=9, hand=[]),
        ],
        deck=["x", "y"],
        phase="playing",
    )


# ---------------------------------------------------------------------------
# read_game_state — GameState object input
# ---------------------------------------------------------------------------


def test_read_game_state_from_gamestate_object():
    tool = make_read_game_state_tool(_sample_state(), actor_id="p1", creator_id="p1")
    assert tool.name == "read_game_state"
    out = tool.invoke({})
    # Player names and scores are present.
    assert "Alice" in out
    assert "Bob" in out
    assert "3 points" in out
    assert "9 points" in out
    # The actor is marked.
    assert "ACTOR" in out
    # actor == author -> punish_author is surfaced as reachable.
    assert "actor == author" in out
    assert "punish_author" in out


def test_read_game_state_from_dict_snapshot():
    tool = make_read_game_state_tool(_sample_state().model_dump(), actor_id="p1", creator_id="p1")
    out = tool.invoke({})
    assert "Alice" in out
    assert "Bob" in out
    assert "ACTOR" in out
    assert "actor == author" in out


def test_read_game_state_actor_not_author():
    tool = make_read_game_state_tool(_sample_state(), actor_id="p1", creator_id="p2")
    out = tool.invoke({})
    # Not the author -> do_nothing branch flagged, punish_author NOT asserted.
    assert "NOT the actor" in out
    assert "do_nothing" in out


def test_read_game_state_authorship_from_state_cards():
    """When creator_id is not passed, authorship is looked up in state.cards by card_id."""
    state = _sample_state()
    state.cards["c1"] = {"id": "c1", "title": "T", "creator_id": "p1"}
    tool = make_read_game_state_tool(state, actor_id="p1", card_id="c1")
    out = tool.invoke({})
    assert "actor == author" in out

    # Card authored by someone else.
    state.cards["c1"] = {"id": "c1", "title": "T", "creator_id": "p2"}
    tool2 = make_read_game_state_tool(state, actor_id="p1", card_id="c1")
    assert "NOT the actor" in tool2.invoke({})


def test_read_game_state_degrades_on_malformed_snapshot():
    # players is a non-iterable int -> iterating raises inside -> caught, no raise.
    tool = make_read_game_state_tool({"players": 999, "phase": "playing"}, actor_id="p1")
    out = tool.invoke({})
    assert out == "game state unavailable"


def test_read_game_state_none_snapshot():
    tool = make_read_game_state_tool(None, actor_id="p1")
    out = tool.invoke({})
    assert "not provided" in out


# ---------------------------------------------------------------------------
# read_engine_methods — introspection
# ---------------------------------------------------------------------------


def test_read_engine_methods_lists_real_ops():
    out = read_engine_methods.invoke({})
    # A representative sample of ops, introspected from models.effects.Op.
    for op_name in ("add_points", "subtract_points", "steal_points", "custom_note", "set_win_condition"):
        assert op_name in out
    assert "state.steal_points(from_target:" in out
    assert "to_target:" in out


def test_read_engine_methods_is_introspected_not_hardcoded():
    # Every current Op literal must appear in the output, proving it is derived
    # from the union rather than a stale hardcoded list.
    from typing import get_args

    from models.effects import Op

    union = get_args(Op)[0]
    out = read_engine_methods.invoke({})
    for model in get_args(union):
        literal = model.model_fields["op"].default
        assert literal in out, f"op {literal!r} missing from introspected reference"


def test_read_engine_methods_lists_targets_and_sandbox_boundary():
    out = read_engine_methods.invoke({})
    # Target literals.
    assert "self" in out
    assert "all_others" in out
    assert "player_with_most_points" in out
    assert "resolve_card" not in out
    assert "GameEngine" in out
    assert "state.draw is invalid" in out
    assert "state.draw_cards" in out
    assert "state.reject_play" in out


def test_get_read_engine_methods_tool_returns_named_tool():
    tool = get_read_engine_methods_tool()
    assert tool.name == "read_engine_methods"


# ---------------------------------------------------------------------------
# _assemble_tools — wiring
# ---------------------------------------------------------------------------


def test_assemble_tools_includes_read_game_state_when_state_provided():
    tools = _assemble_tools(_sample_state(), "p1", "p1", None)
    names = {t.name for t in tools}
    assert "read_game_state" in names
    assert "read_game_history" in names
    assert "dry_run_effect" in names
    # read_engine_methods rides in via get_default_tools.
    assert "read_engine_methods" in names


def test_assemble_tools_excludes_read_game_state_when_state_none():
    tools = _assemble_tools(None, None, None, None)
    names = {t.name for t in tools}
    assert "read_game_state" not in names
    assert "read_game_history" not in names
    assert "dry_run_effect" not in names
    assert "read_engine_methods" in names


def test_assemble_tools_read_only_mode_excludes_persistent_writers():
    tools = _assemble_tools(
        _sample_state(),
        "p1",
        "p1",
        None,
        allow_persistent_tools=False,
    )
    names = {tool.name for tool in tools}
    assert "remember_decision" not in names
    assert "recall_decisions" not in names
    assert "dry_run_effect" in names


def test_assemble_tools_appends_extra_tools():
    from langchain_core.tools import tool

    @tool
    def extra_tool() -> str:
        """An extra test tool."""
        return "extra"

    tools = _assemble_tools(None, None, None, [extra_tool])
    names = {t.name for t in tools}
    assert "extra_tool" in names


def test_run_agent_explicit_tools_replace_assembled_toolbox(monkeypatch):
    """An explicit ``tools`` list is bound verbatim — the assembled production
    toolbox must NOT be added alongside it (the eval runner's enabled_tools
    filter depends on this)."""
    import agent.runtime as rt

    captured: dict[str, list] = {}
    real_build = rt.build_agent

    def spy(tools=None, model=None, *, system_prompt=None):  # noqa: ANN001
        captured["tools"] = list(tools or [])
        return real_build(tools=tools, model=model, system_prompt=system_prompt)

    monkeypatch.setattr(rt, "build_agent", spy)

    fake = ToolAwareFake(messages=iter([AIMessage(content='{"verdict": "ok", "comment": "Fine."}')]))
    result = rt.run_agent("C", "d", state=_sample_state(), actor_id="p1", model=fake, tools=[])

    assert result.verdict == "ok"
    assert captured["tools"] == []


# ---------------------------------------------------------------------------
# run_agent — routes a tool_call through read_game_state
# ---------------------------------------------------------------------------


def test_run_agent_routes_through_read_game_state():
    """A fake model emits a tool_call to read_game_state, then a final result.

    The tool is bound because state is provided; its output flows back into the
    conversation and the run completes with a structured result.
    """
    final = '{"verdict": "ok", "comment": "Alice is losing, delightful.", "persona_action": "none"}'
    fake = ToolAwareFake(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[{"name": "read_game_state", "args": {}, "id": "s1"}]),
                AIMessage(content=final),
            ]
        )
    )

    result = run_agent(
        "Some card",
        "does something",
        state=_sample_state(),
        actor_id="p1",
        creator_id="p1",
        model=fake,
    )

    assert isinstance(result, InterpretResult)
    assert result.verdict == "ok"
    assert result.comment  # comment always present


# ---------------------------------------------------------------------------
# run_agent — persona_action + comment threading
# ---------------------------------------------------------------------------


def test_run_agent_threads_punish_author_persona_and_comment():
    payload = (
        '{"program": {"ops": [{"op": "subtract_points", "target": "self", "amount": 3}], '
        '"requires_choice": false}, "snippet": null, "verdict": "ok", '
        '"comment": "You wrote this garbage AND played it. Minus three.", '
        '"persona_action": "punish_author"}'
    )
    fake = ToolAwareFake(messages=iter([AIMessage(content=payload)]))

    result = run_agent(
        "Nonsense",
        "asdf qwer",
        state=_sample_state(),
        actor_id="p1",
        creator_id="p1",
        model=fake,
    )

    assert result.persona_action == "punish_author"
    assert result.comment
    assert "Minus three" in result.comment


def test_run_agent_comment_always_present_across_paths():
    # Happy path with state.
    ok = ToolAwareFake(messages=iter([AIMessage(content='{"verdict": "ok", "comment": "Fine."}')]))
    r_ok = run_agent("C", "d", state=_sample_state(), actor_id="p1", model=ok)
    assert r_ok.comment

    # Non-JSON path degrades to a comment.
    bad = ToolAwareFake(messages=iter([AIMessage(content="not json here")]))
    r_bad = run_agent("C", "d", model=bad)
    assert r_bad.comment

    # State=None still carries a comment on happy path.
    none_state = ToolAwareFake(messages=iter([AIMessage(content='{"verdict": "ok", "comment": "No board."}')]))
    r_none = run_agent("C", "d", model=none_state)
    assert r_none.comment
