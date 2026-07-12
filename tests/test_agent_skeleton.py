"""Deterministic, hermetic tests for the single tool-calling agent skeleton (C1).

No network, no real LLM: every test drives the agent with a scripted fake chat
model. The fake subclasses GenericFakeChatModel and adds a no-op ``bind_tools`` so
LangChain's ``create_agent`` can bind tools to it (the base fake raises
NotImplementedError on bind_tools).
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from agent.contract import InterpretResult
from agent.persona import PERSONA_ACTIONS, build_system_prompt
from agent.runtime import build_agent, run_agent


class ToolAwareFake(GenericFakeChatModel):
    """A scripted fake chat model that also supports ``bind_tools`` (no-op)."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003 — mirror base signature
        return self


class LoopingFake(GenericFakeChatModel):
    """A fake model that ALWAYS emits another tool call, so the agent never stops.

    Unconditional: even the forced tools-disabled final-answer call gets an
    (empty-content) tool-call message back, so the forced call also fails to
    parse and the runtime must degrade to ``_fallback_result``.
    """

    tool_name: str = "noop_tool"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        msg = AIMessage(
            content="",
            tool_calls=[{"name": self.tool_name, "args": {}, "id": "loop-call"}],
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])


class LoopingThenAnsweringFake(GenericFakeChatModel):
    """Loops on tool calls UNTIL it sees the forced-final-answer instruction.

    Models a budget-exhausted agent whose forced final call succeeds: the fake
    inspects the last message for the "Budget exhausted" marker that
    ``agent.runtime`` appends only for the tools-disabled forced call.
    """

    tool_name: str = "noop_tool"
    final_payload: str = '{"verdict": "ok", "comment": "Forced answer.", "persona_action": "none"}'

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        last = messages[-1] if messages else None
        if last is not None and "Budget exhausted" in str(getattr(last, "content", "")):
            msg = AIMessage(content=self.final_payload)
        else:
            msg = AIMessage(content="", tool_calls=[{"name": self.tool_name, "args": {}, "id": "loop-call"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


@tool
def noop_tool() -> str:
    """A stub tool that returns a fixed string. Used to exercise tool routing."""
    return "noop-tool-ran"


_LANGSMITH_ENV_KEYS = (
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _isolate_langsmith_env():
    """run_agent writes LANGSMITH_* env vars directly (by design). Snapshot and
    restore them around every test so nothing leaks into other test modules."""
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


# ---------------------------------------------------------------------------
# persona.py
# ---------------------------------------------------------------------------


def test_persona_actions_match_contract():
    """Every persona_action the contract allows is documented in persona.py."""
    contract_actions = {"none", "do_nothing", "punish_author", "chaos_monkey", "random_solution"}
    assert set(PERSONA_ACTIONS) == contract_actions


def test_build_system_prompt_contains_key_sections():
    prompt = build_system_prompt(
        title="Gain 5 points",
        description="You gain 5 points.",
        actor_id="p1",
        creator_id="p1",
    )
    assert "Gain 5 points" in prompt
    assert "You gain 5 points." in prompt
    # persona branches and comment requirement are present
    assert "do_nothing" in prompt
    assert "punish_author" in prompt
    assert "comment" in prompt
    # actor IS author -> the authorship note reflects that
    assert "IS the author" in prompt


def test_build_system_prompt_renders_state():
    state = {
        "phase": "playing",
        "players": [
            {"id": "p1", "name": "Alice", "score": 3},
            {"id": "p2", "name": "Bob", "score": 9},
        ],
    }
    prompt = build_system_prompt("T", "D", state=state, actor_id="p1")
    assert "Alice" in prompt
    assert "Bob" in prompt
    assert "the current player" in prompt


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_agent_happy_path_returns_structured_result():
    payload = (
        '{"program": {"ops": [{"op": "add_points", "target": "self", "amount": 5}], '
        '"requires_choice": false}, "snippet": null, "verdict": "ok", '
        '"comment": "Wow, +5 points. Groundbreaking.", "persona_action": "none"}'
    )
    fake = ToolAwareFake(messages=iter([AIMessage(content=payload)]))

    result = run_agent("Gain 5 points", "You gain 5 points.", model=fake)

    assert isinstance(result, InterpretResult)
    assert result.verdict == "ok"
    assert result.persona_action == "none"
    assert result.program is not None
    assert result.program.ops[0].op == "add_points"
    assert result.program.ops[0].amount == 5
    # A comment is ALWAYS present and, on the happy path, non-empty.
    assert result.comment
    assert isinstance(result.comment, str)


# ---------------------------------------------------------------------------
# Tool routing
# ---------------------------------------------------------------------------


def test_run_agent_routes_through_a_bound_tool():
    calls: list[str] = []

    @tool
    def counting_tool() -> str:
        """A stub tool that records that it was invoked."""
        calls.append("hit")
        return "counting-tool-ran"

    final = '{"verdict": "ok", "comment": "Fine.", "persona_action": "none"}'
    fake = ToolAwareFake(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[{"name": "counting_tool", "args": {}, "id": "c1"}]),
                AIMessage(content=final),
            ]
        )
    )

    result = run_agent("Card", "desc", model=fake, tools=[counting_tool])

    assert len(calls) >= 1  # the tool actually ran
    assert result.verdict == "ok"


def test_build_agent_binds_passed_tools():
    """build_agent should construct without error when handed a tool list."""
    fake = ToolAwareFake(messages=iter([AIMessage(content="{}")]))
    agent = build_agent(tools=[noop_tool], model=fake)
    assert agent is not None


# ---------------------------------------------------------------------------
# Cap / timeout -> bounded fallback
# ---------------------------------------------------------------------------


def test_run_agent_recursion_cap_forces_final_answer():
    """Hitting the recursion cap makes one forced tools-disabled call; its parsed
    JSON is returned instead of the deterministic give-up fallback."""
    fake = LoopingThenAnsweringFake(messages=iter([]))
    result = run_agent(
        "Loop card",
        "desc",
        model=fake,
        tools=[noop_tool],
        max_tool_calls=4,  # small cap
        timeout=10.0,
    )
    assert isinstance(result, InterpretResult)
    assert result.verdict == "ok"
    assert result.comment == "Forced answer."
    assert result.persona_action == "none"


def test_run_agent_recursion_cap_forced_call_also_fails_returns_fallback():
    """When the forced call ALSO can't produce a parseable answer (still looping),
    the runtime degrades to the deterministic bounded fallback."""
    fake = LoopingFake(messages=iter([]))
    result = run_agent(
        "Loop card",
        "desc",
        model=fake,
        tools=[noop_tool],
        max_tool_calls=4,  # small cap
        timeout=10.0,
    )
    assert isinstance(result, InterpretResult)
    assert result.verdict == "invalid"
    # Bounded fallback still carries a comment and a sensible persona_action.
    assert result.comment
    assert result.persona_action == "do_nothing"


def test_run_agent_timeout_forces_final_answer():
    class HangingThenAnsweringFake(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
            last = messages[-1] if messages else None
            if last is not None and "Budget exhausted" in str(getattr(last, "content", "")):
                payload = '{"verdict": "ok", "comment": "Forced answer after timeout.", "persona_action": "none"}'
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content=payload))])
            import time

            time.sleep(1.0)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="{}"))])

    fake = HangingThenAnsweringFake(messages=iter([]))
    result = run_agent("Slow card", "desc", model=fake, timeout=0.2)
    assert result.verdict == "ok"
    assert result.comment == "Forced answer after timeout."


def test_run_agent_timeout_forced_call_also_times_out_returns_fallback():
    class AlwaysHangingFake(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
            import time

            time.sleep(1.0)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="{}"))])

    fake = AlwaysHangingFake(messages=iter([]))
    result = run_agent("Slow card", "desc", model=fake, timeout=0.1, forced_call_timeout=0.1)
    assert result.verdict == "invalid"
    assert result.comment


# ---------------------------------------------------------------------------
# Degraded config -> deterministic fallback
# ---------------------------------------------------------------------------


def test_run_agent_model_construction_failure_returns_fallback(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no api key")

    # No model injected -> run_agent calls get_chat_model, which we make explode.
    monkeypatch.setattr("agent.runtime.get_chat_model", boom)
    result = run_agent("Card", "desc")
    assert isinstance(result, InterpretResult)
    assert result.verdict == "invalid"
    assert result.program is not None
    assert result.program.ops[0].op == "custom_note"


def test_run_agent_invoke_error_returns_fallback():
    class ErroringFake(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
            raise ValueError("model blew up")

    fake = ErroringFake(messages=iter([]))
    result = run_agent("Card", "desc", model=fake)
    assert result.verdict == "invalid"
    assert result.comment


def test_run_agent_non_json_final_message_degrades_to_comment():
    fake = ToolAwareFake(messages=iter([AIMessage(content="this is not json at all")]))
    result = run_agent("Card", "desc", model=fake)
    assert result.verdict == "invalid"
    assert "not json" in result.comment


def test_run_agent_parses_fenced_json():
    payload = '```json\n{"verdict": "ok", "comment": "Fenced but fine."}\n```'
    fake = ToolAwareFake(messages=iter([AIMessage(content=payload)]))
    result = run_agent("Card", "desc", model=fake)
    assert result.verdict == "ok"
    assert result.comment == "Fenced but fine."


def test_langsmith_tracing_off_by_default(monkeypatch):
    """When tracing is disabled, run_agent must force the env flag to 'false'."""
    import os

    from config import Settings

    monkeypatch.setattr("agent.runtime.get_settings", lambda: Settings(_env_file=None, langsmith_tracing=False))
    fake = ToolAwareFake(messages=iter([AIMessage(content='{"verdict": "ok", "comment": "hi"}')]))
    run_agent("Card", "desc", model=fake)
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_langsmith_tracing_can_be_enabled(monkeypatch):
    import os

    from config import Settings

    monkeypatch.setattr(
        "agent.runtime.get_settings",
        lambda: Settings(_env_file=None, langsmith_tracing=True, langsmith_api_key="test-key"),
    )
    fake = ToolAwareFake(messages=iter([AIMessage(content='{"verdict": "ok", "comment": "hi"}')]))
    run_agent("Card", "desc", model=fake)
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == "tbwc-dev"
    assert os.environ["LANGSMITH_API_KEY"] == "test-key"
