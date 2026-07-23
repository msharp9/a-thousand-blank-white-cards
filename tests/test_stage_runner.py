"""Hermetic tests for the generic stage runner (bead 47b.3).

No network, no real LLM: scripted fakes drive :func:`agent.stage_runner.run_stage`
with a toy pydantic output model to prove the machinery is not tied to
InterpretResult (that tie is covered by tests/test_agent_skeleton.py, the
zero-behavior-change regression harness for run_agent).
"""

from __future__ import annotations

import time

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import BaseModel

from agent.stage_runner import FORCED_FINAL_INSTRUCTION, run_stage


class ToyResult(BaseModel):
    """A minimal output contract unrelated to InterpretResult."""

    answer: str
    score: int = 0


class ToolAwareFake(GenericFakeChatModel):
    """A scripted fake chat model that also supports ``bind_tools`` (no-op)."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003 — mirror base signature
        return self


class LoopingFake(GenericFakeChatModel):
    """Always emits another tool call, so the stage never finishes on its own."""

    tool_name: str = "noop_tool"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        msg = AIMessage(content="", tool_calls=[{"name": self.tool_name, "args": {}, "id": "loop-call"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


class LoopingThenAnsweringFake(GenericFakeChatModel):
    """Loops on tool calls UNTIL it sees the forced-final-answer instruction."""

    tool_name: str = "noop_tool"
    final_payload: str = '{"answer": "forced", "score": 7}'

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        last = messages[-1] if messages else None
        if last is not None and FORCED_FINAL_INSTRUCTION in str(getattr(last, "content", "")):
            msg = AIMessage(content=self.final_payload)
        else:
            msg = AIMessage(content="", tool_calls=[{"name": self.tool_name, "args": {}, "id": "loop-call"}])
        return ChatResult(generations=[ChatGeneration(message=msg)])


class AlwaysHangingFake(GenericFakeChatModel):
    """Sleeps past every timeout, for both the stream and the forced call."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        time.sleep(1.0)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="{}"))])


@tool
def noop_tool() -> str:
    """A stub tool that returns a fixed string. Used to exercise tool routing."""
    return "noop-tool-ran"


def _run(model, *, tools=None, timeout=10.0, max_steps=8, forced_call_timeout=5.0):
    return run_stage(
        "You are a stage. Answer with the JSON contract.",
        "Do the stage work and produce the JSON result.",
        tools,
        model,
        ToyResult,
        timeout=timeout,
        max_steps=max_steps,
        forced_call_timeout=forced_call_timeout,
    )


def test_run_stage_parses_into_toy_model():
    fake = ToolAwareFake(messages=iter([AIMessage(content='{"answer": "hi", "score": 2}')]))
    result = _run(fake)
    assert isinstance(result, ToyResult)
    assert result.answer == "hi"
    assert result.score == 2


def test_run_stage_detects_answer_object_by_output_model_fields():
    payload = 'Some prose {"unrelated": 1} then the answer: {"answer": "found"} done.'
    fake = ToolAwareFake(messages=iter([AIMessage(content=payload)]))
    result = _run(fake)
    assert result == ToyResult(answer="found")


def test_run_stage_non_json_final_message_returns_none():
    fake = ToolAwareFake(messages=iter([AIMessage(content="this is not json at all")]))
    assert _run(fake) is None


def test_run_stage_schema_mismatch_returns_none():
    fake = ToolAwareFake(messages=iter([AIMessage(content='{"answer": "ok", "score": "not-a-number"}')]))
    assert _run(fake) is None


def test_run_stage_routes_through_a_bound_tool():
    calls: list[str] = []

    @tool
    def counting_tool() -> str:
        """A stub tool that records that it was invoked."""
        calls.append("hit")
        return "counting-tool-ran"

    fake = ToolAwareFake(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[{"name": "counting_tool", "args": {}, "id": "c1"}]),
                AIMessage(content='{"answer": "with tool"}'),
            ]
        )
    )
    result = _run(fake, tools=[counting_tool])
    assert calls
    assert result == ToyResult(answer="with tool")


def test_run_stage_timeout_returns_none():
    fake = AlwaysHangingFake(messages=iter([]))
    assert _run(fake, timeout=0.1, forced_call_timeout=0.1) is None


def test_run_stage_recursion_cap_forces_final_answer():
    fake = LoopingThenAnsweringFake(messages=iter([]))
    result = _run(fake, tools=[noop_tool], max_steps=4)
    assert result == ToyResult(answer="forced", score=7)


def test_run_stage_recursion_cap_forced_call_also_fails_returns_none():
    fake = LoopingFake(messages=iter([]))
    assert _run(fake, tools=[noop_tool], max_steps=4) is None
