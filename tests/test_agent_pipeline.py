"""Hermetic tests for the three-agent LangGraph pipeline (bead 47b.5).

No network, no real LLM: scripted GenericFakeChatModel subclasses (with a no-op
``bind_tools``) drive :func:`agent.pipeline.run_pipeline`. One fake serves all
three stages — its message queue is consumed in stage order (intent JSON, plan
JSON, coder JSON), the same pattern tests/test_agent_skeleton.py uses.
"""

from __future__ import annotations

import json
import time

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from agent import pipeline
from agent.contract import CardIntent, InterpretResult, MechanicsPlan
from agent.pipeline import STAGE_TOOL_NAMES, build_interpret_graph, run_pipeline
from agent.stage_runner import REPAIR_INSTRUCTION
from models.game_state import GameState, Player

# ---------------------------------------------------------------------------
# Scripted fakes
# ---------------------------------------------------------------------------


class ToolAwareFake(GenericFakeChatModel):
    """A scripted fake chat model that also supports ``bind_tools`` (no-op)."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003 — mirror base signature
        return self


class RecordingFake(GenericFakeChatModel):
    """Replays queued messages while recording each call's system prompt and
    the tool-name lists handed to ``bind_tools``."""

    system_prompts: list = []
    bound_tool_names: list = []

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        self.bound_tool_names.append(sorted(getattr(t, "name", str(t)) for t in tools))
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        system = next((m for m in messages if getattr(m, "type", None) == "system"), None)
        self.system_prompts.append(str(getattr(system, "content", "")))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class RepairCountingFake(GenericFakeChatModel):
    """Replays queued messages while counting repair calls (spotted by the
    REPAIR_INSTRUCTION marker the stage runner appends)."""

    repair_calls: int = 0

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        last = messages[-1] if messages else None
        if REPAIR_INSTRUCTION in str(getattr(last, "content", "")):
            self.repair_calls += 1
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class CountingFake(GenericFakeChatModel):
    """Replays queued messages while counting every model call."""

    calls: int = 0

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        self.calls += 1
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


# ---------------------------------------------------------------------------
# Stage payloads
# ---------------------------------------------------------------------------

INTENT_PAYLOAD = {
    "summary": "Give the actor 5 points.",
    "effects": ["add 5 points to the actor"],
    "targets": "the actor",
    "persistence": "immediate",
    "resolved_references": [],
    "ambiguity": "clear",
    "complexity": "standard",
    "comment": "Wow, +5 points. Groundbreaking.",
    "persona_action": "none",
}
INTENT_JSON = json.dumps(INTENT_PAYLOAD)

PLAN_JSON = json.dumps(
    {
        "strategy": "Compose a single add_points op.",
        "steps": [{"kind": "ops", "description": "add 5 points to the actor", "engine_ops": ["add_points self 5"]}],
        "trigger": None,
        "scope": "player",
        "feasible": True,
        "infeasible_reason": "",
    }
)

CODER_JSON = json.dumps(
    {
        "program": {"ops": [{"op": "add_points", "target": "self", "amount": 5}], "requires_choice": False},
        "snippet": None,
        "verdict": "ok",
    }
)

BAD_SNIPPET_JSON = json.dumps(
    {
        "snippet": {"code": 'def apply(state, ctx):\n    state.draw("self", 2)\n', "explanation": "draw"},
        "verdict": "ok",
    }
)

REPAIRED_SNIPPET_JSON = json.dumps(
    {
        "snippet": {"code": 'def apply(state, ctx):\n    state.draw_cards("self", 2)\n', "explanation": "draw"},
        "verdict": "ok",
    }
)


def _messages(*payloads: str):
    return iter([AIMessage(content=p) for p in payloads])


_LANGSMITH_ENV_KEYS = (
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _isolate_langsmith_env():
    """run_pipeline writes LANGSMITH_* env vars directly (by design). Snapshot
    and restore them around every test so nothing leaks into other modules."""
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
# Happy path + artifact threading
# ---------------------------------------------------------------------------


def test_happy_path_merges_coder_effect_with_intent_voice():
    fake = RecordingFake(messages=_messages(INTENT_JSON, PLAN_JSON, CODER_JSON), system_prompts=[])

    result = run_pipeline("Gain 5 points", "You gain 5 points.", model=fake)

    assert isinstance(result, InterpretResult)
    assert result.verdict == "ok"
    assert result.agent_error is False
    assert result.program is not None
    assert result.program.ops[0].op == "add_points"
    assert result.program.ops[0].amount == 5
    # The persona spoke in the intent stage; the coder JSON has no comment key.
    assert result.comment == "Wow, +5 points. Groundbreaking."
    assert result.persona_action == "none"

    # Artifacts thread forward: the planner prompt renders the intent, the
    # coder prompt renders both the intent and the plan.
    assert len(fake.system_prompts) == 3
    assert "Give the actor 5 points." in fake.system_prompts[1]
    assert "Give the actor 5 points." in fake.system_prompts[2]
    assert "Compose a single add_points op." in fake.system_prompts[2]


# ---------------------------------------------------------------------------
# Stage failures degrade, never raise
# ---------------------------------------------------------------------------


def test_intent_garbage_returns_bounded_fallback():
    fake = ToolAwareFake(messages=_messages("this is not json at all"))

    result = run_pipeline("Card", "desc", model=fake)

    assert isinstance(result, InterpretResult)
    assert result.verdict == "invalid"
    assert result.agent_error is True
    assert result.comment


def test_intent_timeout_returns_bounded_fallback():
    class HangingFake(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
            time.sleep(1.0)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    fake = HangingFake(messages=_messages("{}", "{}"))
    result = run_pipeline("Card", "desc", model=fake, timeout=0.3, forced_call_timeout=0.05)
    assert result.verdict == "invalid"
    assert result.agent_error is True


def test_planner_garbage_falls_back_to_stub_plan():
    fake = RecordingFake(
        messages=_messages(INTENT_JSON, "planner exploded, no json here", CODER_JSON), system_prompts=[]
    )

    result = run_pipeline("Gain 5 points", "You gain 5 points.", model=fake)

    assert result.verdict == "ok"
    assert result.program is not None
    assert result.comment == "Wow, +5 points. Groundbreaking."
    # The coder still ran, against a stub plan whose strategy is the intent summary.
    assert len(fake.system_prompts) == 3
    assert "strategy: Give the actor 5 points." in fake.system_prompts[2]


def test_infeasible_plan_finalizes_with_custom_note_and_intent_comment():
    infeasible = json.dumps(
        {
            "strategy": "Cannot be done.",
            "steps": [],
            "feasible": False,
            "infeasible_reason": "needs a webcam feed",
        }
    )
    fake = CountingFake(messages=_messages(INTENT_JSON, infeasible))

    result = run_pipeline("Impossible card", "desc", model=fake)

    assert result.verdict == "invalid"
    assert result.agent_error is False
    assert result.program is not None
    op = result.program.ops[0]
    assert op.op == "custom_note"
    assert "Give the actor 5 points." in op.note
    assert "needs a webcam feed" in op.note
    assert result.comment == "Wow, +5 points. Groundbreaking."
    assert result.persona_action == "none"
    assert fake.calls == 2  # the coder never ran


# ---------------------------------------------------------------------------
# validate_repair: one repair call, then strip
# ---------------------------------------------------------------------------


def test_invalid_snippet_gets_exactly_one_repair_call():
    fake = RepairCountingFake(messages=_messages(INTENT_JSON, PLAN_JSON, BAD_SNIPPET_JSON, REPAIRED_SNIPPET_JSON))

    result = run_pipeline("Draw", "Draw two cards.", model=fake)

    assert fake.repair_calls == 1
    assert result.verdict == "ok"
    assert result.snippet is not None
    assert "draw_cards" in result.snippet.code
    assert result.comment == "Wow, +5 points. Groundbreaking."


def test_double_repair_failure_strips_effect_but_keeps_intent_comment():
    fake = RepairCountingFake(messages=_messages(INTENT_JSON, PLAN_JSON, BAD_SNIPPET_JSON, BAD_SNIPPET_JSON))

    result = run_pipeline("Draw", "Draw two cards.", model=fake)

    assert fake.repair_calls == 1
    assert result.verdict == "invalid"
    assert result.plan is None
    assert result.program is None
    assert result.snippet is None
    assert result.comment == "Wow, +5 points. Groundbreaking."
    assert result.persona_action == "none"


def test_coder_garbage_falls_back_but_keeps_intent_voice():
    fake = CountingFake(messages=_messages(INTENT_JSON, PLAN_JSON, "no json from the coder"))

    result = run_pipeline("Card", "desc", model=fake)

    assert result.verdict == "invalid"
    assert result.agent_error is True
    assert result.comment == "Wow, +5 points. Groundbreaking."
    assert result.persona_action == "none"


# ---------------------------------------------------------------------------
# Persona short-circuits
# ---------------------------------------------------------------------------


def test_undecipherable_do_nothing_skips_planner_and_coder():
    payload = dict(INTENT_PAYLOAD, ambiguity="undecipherable", persona_action="do_nothing", comment="Even I blinked.")
    fake = CountingFake(messages=_messages(json.dumps(payload)))

    result = run_pipeline("???", "scribbles", model=fake)

    assert fake.calls == 1  # planner and coder never ran
    assert result.verdict == "invalid"
    assert result.agent_error is False
    assert result.program is None
    assert result.snippet is None
    assert result.comment == "Even I blinked."
    assert result.persona_action == "do_nothing"


def test_punish_author_reaches_coder_with_a_real_docking_effect():
    payload = dict(
        INTENT_PAYLOAD,
        summary="Attempt a sandbox escape.",
        persona_action="punish_author",
        comment="Cute. The house always wins.",
    )
    dock = json.dumps(
        {
            "program": {"ops": [{"op": "subtract_points", "target": "id:p2", "amount": 5}], "requires_choice": False},
            "verdict": "ok",
        }
    )
    fake = RecordingFake(messages=_messages(json.dumps(payload), PLAN_JSON, dock), system_prompts=[])

    result = run_pipeline("Escape", "import os", actor_id="p2", creator_id="p2", model=fake)

    assert result.verdict == "ok"
    assert result.program is not None
    assert result.program.ops[0].op == "subtract_points"
    assert result.comment == "Cute. The house always wins."
    assert result.persona_action == "punish_author"
    # The planner and coder worked against the rewritten point-docking intent,
    # not the abusive card's own summary.
    assert "subtract points from the card's author" in fake.system_prompts[1]
    assert "Attempt a sandbox escape." not in fake.system_prompts[1]
    assert "subtract points from the card's author" in fake.system_prompts[2]


# ---------------------------------------------------------------------------
# Per-stage tools
# ---------------------------------------------------------------------------


def test_stage_tool_assembly_matches_configured_sets():
    state = GameState(room_code="TEST", players=[Player(id="p1", name="Alice")], phase="playing")
    pipeline_state = {"game_state": state, "actor_id": "p1", "creator_id": "p1", "allow_persistent_tools": True}

    for stage, expected in STAGE_TOOL_NAMES.items():
        names = [getattr(t, "name", "") for t in pipeline._stage_tools(stage, pipeline_state)]
        assert set(names) <= set(expected), f"{stage} bound unexpected tools: {names}"
    planner = {t.name for t in pipeline._stage_tools("planner", pipeline_state)}
    coder = {t.name for t in pipeline._stage_tools("coder", pipeline_state)}
    intent = {t.name for t in pipeline._stage_tools("intent", pipeline_state)}
    assert {"read_engine_methods", "read_game_state", "read_game_history"} <= planner
    assert {"dry_run_effect", "read_engine_methods", "remember_decision"} <= coder
    assert "recall_decisions" in intent
    assert "dry_run_effect" not in intent
    assert "web_search" not in coder


def test_stage_tools_respect_persistent_gating_and_missing_state():
    gated = {"game_state": None, "allow_persistent_tools": False}
    for stage in STAGE_TOOL_NAMES:
        names = {t.name for t in pipeline._stage_tools(stage, gated)}
        assert not names & {"recall_decisions", "remember_decision"}
        assert not names & {"read_game_state", "read_game_history", "dry_run_effect"}


def test_each_stage_binds_its_own_toolset(monkeypatch):
    from langchain_core.tools import tool

    def _stub(name):
        @tool(name)
        def _t() -> str:
            """Stage tool stub."""
            return name

        return _t

    monkeypatch.setattr(pipeline, "_stage_tools", lambda stage, state: [_stub(n) for n in STAGE_TOOL_NAMES[stage]])
    fake = RecordingFake(messages=_messages(INTENT_JSON, PLAN_JSON, CODER_JSON), system_prompts=[], bound_tool_names=[])

    result = run_pipeline("Card", "desc", model=fake)

    assert result.verdict == "ok"
    assert fake.bound_tool_names == [sorted(STAGE_TOOL_NAMES[s]) for s in ("intent", "planner", "coder")]


def test_explicit_tools_list_is_bound_to_all_three_stages():
    from langchain_core.tools import tool

    @tool
    def only_tool() -> str:
        """The caller's single explicit tool."""
        return "only"

    fake = RecordingFake(messages=_messages(INTENT_JSON, PLAN_JSON, CODER_JSON), system_prompts=[], bound_tool_names=[])

    result = run_pipeline("Card", "desc", model=fake, tools=[only_tool])

    assert result.verdict == "ok"
    assert fake.bound_tool_names == [["only_tool"], ["only_tool"], ["only_tool"]]


# ---------------------------------------------------------------------------
# Budgets and deadlines
# ---------------------------------------------------------------------------


def test_stage_budgets_scale_with_caller_knobs():
    budgets = pipeline._stage_budgets(54.0, 12, 7.0)
    assert budgets["intent"]["timeout"] == pytest.approx(12.0)
    assert budgets["planner"]["timeout"] == pytest.approx(18.0)
    assert budgets["coder"]["timeout"] == pytest.approx(24.0)
    assert budgets["intent"]["max_steps"] == 3
    assert budgets["planner"]["max_steps"] == 4
    assert budgets["coder"]["max_steps"] == 5
    assert budgets["forced_call_timeout"] == 7.0

    defaults = pipeline._stage_budgets(None, None, 30.0)
    assert defaults["intent"] == {"timeout": 120.0, "max_steps": 6}
    assert defaults["planner"] == {"timeout": 180.0, "max_steps": 8}
    assert defaults["coder"] == {"timeout": 240.0, "max_steps": 10}


def test_trivial_complexity_shrinks_downstream_caps(monkeypatch):
    seen: list[tuple[str, int]] = []
    trivial_intent = CardIntent.model_validate(dict(INTENT_PAYLOAD, complexity="trivial"))
    plan = MechanicsPlan(strategy="add points")
    draft = InterpretResult(verdict="ok")

    def fake_run_stage(system_prompt, user_content, tools, model, output_model, *, max_steps, **kwargs):
        seen.append((output_model.__name__, max_steps))
        return {"CardIntent": trivial_intent, "MechanicsPlan": plan, "InterpretResult": draft}[output_model.__name__]

    monkeypatch.setattr(pipeline, "run_stage", fake_run_stage)

    result = run_pipeline("Gain 1 point", "desc")

    assert result.verdict == "ok"
    assert seen == [
        ("CardIntent", pipeline.INTENT_MAX_STEPS),
        ("MechanicsPlan", pipeline.TRIVIAL_PLANNER_MAX_STEPS),
        ("InterpretResult", pipeline.TRIVIAL_CODER_MAX_STEPS),
    ]


def test_zero_timeout_degrades_to_fallback_without_model_calls():
    fake = CountingFake(messages=_messages(INTENT_JSON, PLAN_JSON, CODER_JSON))

    result = run_pipeline("Card", "desc", model=fake, timeout=0.0)

    assert fake.calls == 0
    assert result.verdict == "invalid"
    assert result.agent_error is True


def test_deadline_expiry_between_nodes_degrades_gracefully():
    """An intent already in hand but no time left: planner and coder behave as
    failed stages and finalize still keeps the intent's voice."""
    intent = CardIntent.model_validate(INTENT_PAYLOAD)
    fake = CountingFake(messages=_messages(PLAN_JSON, CODER_JSON))

    out = build_interpret_graph().invoke(
        {
            "title": "Card",
            "description": "desc",
            "model": fake,
            "intent": intent,
            "deadline": time.monotonic() - 1.0,
            "stage_errors": [],
        }
    )

    result = out["result"]
    assert fake.calls == 0
    assert result.verdict == "invalid"
    assert result.agent_error is True
    assert result.comment == "Wow, +5 points. Groundbreaking."
    assert result.persona_action == "none"
    assert out["stage_errors"]


# ---------------------------------------------------------------------------
# Never-raise guarantee + image-rejection retry
# ---------------------------------------------------------------------------


def test_run_pipeline_never_raises_even_when_graph_explodes(monkeypatch):
    class BoomGraph:
        def invoke(self, state):  # noqa: ANN001
            raise RuntimeError("graph exploded")

    monkeypatch.setattr(pipeline, "_compiled_graph", lambda: BoomGraph())

    result = run_pipeline("Card", "desc")

    assert isinstance(result, InterpretResult)
    assert result.verdict == "invalid"
    assert result.agent_error is True


def test_intent_image_rejection_retries_text_only(monkeypatch):
    from config import Settings

    monkeypatch.setattr("agent.pipeline.get_settings", lambda: Settings(_env_file=None, vision_enabled=True))

    class ImageRejectingFake(GenericFakeChatModel):
        rejections: int = 0

        def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
            human = next((m for m in messages if getattr(m, "type", None) == "human"), None)
            if isinstance(getattr(human, "content", None), list):
                self.rejections += 1
                raise ValueError("this model does not support image input")
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    fake = ImageRejectingFake(messages=_messages(INTENT_JSON, PLAN_JSON, CODER_JSON))

    result = run_pipeline("Card", "desc", card_art="data:image/png;base64,AAAA", model=fake)

    assert fake.rejections == 1
    assert result.verdict == "ok"
    assert result.comment == "Wow, +5 points. Groundbreaking."


# ---------------------------------------------------------------------------
# Per-stage model selection (Settings overrides)
# ---------------------------------------------------------------------------


def test_stage_model_settings_build_per_stage_models(monkeypatch):
    from config import get_settings

    monkeypatch.setenv("INTENT_AGENT_MODEL", "intent-mini")
    monkeypatch.setenv("PLANNER_AGENT_MODEL", "planner-mid")
    monkeypatch.setenv("CODER_AGENT_MODEL", "coder-max")
    get_settings.cache_clear()

    scripts = {"intent-mini": INTENT_JSON, "planner-mid": PLAN_JSON, "coder-max": CODER_JSON}
    built: list = []

    def fake_get_chat_model(model_name=None, **kwargs):  # noqa: ANN001, ANN003
        built.append(model_name)
        return ToolAwareFake(messages=_messages(scripts[model_name]))

    monkeypatch.setattr(pipeline, "get_chat_model", fake_get_chat_model)

    result = run_pipeline("Gain 5 points", "You gain 5 points.")

    assert built == ["intent-mini", "planner-mid", "coder-max"]
    assert result.verdict == "ok"
    assert result.comment == "Wow, +5 points. Groundbreaking."


def test_explicit_model_wins_over_stage_model_settings(monkeypatch):
    from config import get_settings

    monkeypatch.setenv("INTENT_AGENT_MODEL", "intent-mini")
    get_settings.cache_clear()

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("get_chat_model must not be called when model= is explicit")

    monkeypatch.setattr(pipeline, "get_chat_model", boom)
    fake = ToolAwareFake(messages=_messages(INTENT_JSON, PLAN_JSON, CODER_JSON))

    result = run_pipeline("Gain 5 points", "You gain 5 points.", model=fake)

    assert result.verdict == "ok"


# ---------------------------------------------------------------------------
# run_agent dispatch (interpret_pipeline_enabled flag)
# ---------------------------------------------------------------------------


def _sentinel_interpret(tag: str, seen: dict):
    def _interpret(title, description, state=None, actor_id=None, **kwargs):  # noqa: ANN001, ANN003
        seen["args"] = (title, description, state, actor_id)
        seen["kwargs"] = kwargs
        return InterpretResult(verdict="ok", comment=tag)

    return _interpret


def test_flag_off_run_agent_runs_the_single_agent(monkeypatch):
    from agent import runtime
    from config import get_settings

    monkeypatch.delenv("INTERPRET_PIPELINE_ENABLED", raising=False)
    get_settings.cache_clear()
    seen: dict = {}
    monkeypatch.setattr(runtime, "_run_single_agent", _sentinel_interpret("single", seen))

    result = runtime.run_agent("T", "D", "STATE", "p1", creator_id="p2", card_id="c1", max_tool_calls=3)

    assert result.comment == "single"
    assert seen["args"] == ("T", "D", "STATE", "p1")
    assert seen["kwargs"]["creator_id"] == "p2"
    assert seen["kwargs"]["card_id"] == "c1"
    assert seen["kwargs"]["max_tool_calls"] == 3


def test_flag_on_run_agent_dispatches_to_pipeline_unchanged(monkeypatch):
    from agent import runtime
    from config import get_settings

    monkeypatch.setenv("INTERPRET_PIPELINE_ENABLED", "true")
    get_settings.cache_clear()
    seen: dict = {}
    monkeypatch.setattr(pipeline, "run_pipeline", _sentinel_interpret("pipeline", seen))

    result = runtime.run_agent(
        "T",
        "D",
        "STATE",
        "p1",
        creator_id="p2",
        card_id="c1",
        card_art="data:image/png;base64,AAAA",
        tools=[],
        model="M",
        timeout=9.0,
        max_tool_calls=3,
        forced_call_timeout=1.0,
        allow_persistent_tools=False,
        config={"callbacks": []},
    )

    assert result.comment == "pipeline"
    assert seen["args"] == ("T", "D", "STATE", "p1")
    assert seen["kwargs"] == {
        "creator_id": "p2",
        "card_id": "c1",
        "card_art": "data:image/png;base64,AAAA",
        "tools": [],
        "model": "M",
        "timeout": 9.0,
        "max_tool_calls": 3,
        "forced_call_timeout": 1.0,
        "allow_persistent_tools": False,
        "config": {"callbacks": []},
    }


# ---------------------------------------------------------------------------
# Graph topology
# ---------------------------------------------------------------------------


def test_graph_topology_and_conditional_routing():
    drawable = build_interpret_graph().get_graph()

    assert {"intent", "planner", "coder", "validate_repair", "finalize"} <= set(drawable.nodes)

    edges = {(e.source, e.target): bool(e.conditional) for e in drawable.edges}
    assert edges == {
        ("__start__", "intent"): False,
        ("intent", "planner"): True,
        ("intent", "finalize"): True,
        ("planner", "coder"): True,
        ("planner", "finalize"): True,
        ("coder", "validate_repair"): False,
        ("validate_repair", "finalize"): False,
        ("finalize", "__end__"): False,
    }
