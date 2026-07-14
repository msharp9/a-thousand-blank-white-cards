"""Tests for the revamped eval harness: instrumentation, runner, and store.

All offline — run_agent and the judge are never called for real. The live
end-to-end run is exercised from scripts/evals.ipynb, not here.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from evals.instrumentation import RunMetrics, UsageCallback, cost_usd


# --------------------------------------------------------------------------- #
# Instrumentation
# --------------------------------------------------------------------------- #
class TestUsageCallback:
    def test_counts_tool_starts_by_name(self) -> None:
        cb = UsageCallback()
        cb.on_tool_start({"name": "card_rag"}, "q")
        cb.on_tool_start({"name": "card_rag"}, "q2")
        cb.on_tool_start({"name": "web_search"}, "q3")
        snap = cb.snapshot()
        assert snap.tool_calls == 3
        assert snap.per_tool == {"card_rag": 2, "web_search": 1}

    def test_sums_usage_metadata_across_llm_calls(self) -> None:
        cb = UsageCallback()
        for _ in range(2):
            gen = SimpleNamespace(
                message=SimpleNamespace(
                    usage_metadata={
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "total_tokens": 150,
                    }
                )
            )
            response = SimpleNamespace(generations=[[gen]], llm_output=None)
            cb.on_llm_end(response)
        snap = cb.snapshot()
        assert snap.llm_calls == 2
        assert snap.prompt_tokens == 200
        assert snap.completion_tokens == 100
        assert snap.total_tokens == 300

    def test_absent_usage_reports_none_not_crash(self) -> None:
        cb = UsageCallback()
        cb.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=None)]], llm_output=None))
        snap = cb.snapshot()
        assert snap.llm_calls == 1
        assert snap.total_tokens is None
        assert snap.prompt_tokens is None

    def test_classic_llm_output_shape(self) -> None:
        cb = UsageCallback()
        response = SimpleNamespace(
            generations=[[SimpleNamespace(message=None)]],
            llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        )
        cb.on_llm_end(response)
        snap = cb.snapshot()
        assert snap.prompt_tokens == 10
        assert snap.completion_tokens == 5


class TestCost:
    def test_known_model(self) -> None:
        m = RunMetrics(prompt_tokens=1_000_000, completion_tokens=0)
        assert cost_usd(m, "us.anthropic.claude-sonnet-5") == pytest.approx(3.00)

    def test_bedrock_prefix_is_stripped(self) -> None:
        m = RunMetrics(prompt_tokens=1_000_000, completion_tokens=0)
        assert cost_usd(m, "bedrock/us.anthropic.claude-sonnet-5") == pytest.approx(3.00)

    def test_unknown_model_uses_default(self) -> None:
        m = RunMetrics(prompt_tokens=1_000_000, completion_tokens=0)
        assert cost_usd(m, "totally-unknown") == pytest.approx(0.50)

    def test_no_usage_is_none(self) -> None:
        assert cost_usd(RunMetrics(), "us.anthropic.claude-sonnet-5") is None

    def test_override_price_table(self) -> None:
        m = RunMetrics(prompt_tokens=0, completion_tokens=1_000_000)
        assert cost_usd(m, "x", {"x": {"input": 0.0, "output": 10.0}}) == pytest.approx(10.0)


# --------------------------------------------------------------------------- #
# Runner: dataset loading + tool filtering
# --------------------------------------------------------------------------- #
class TestLoadCards:
    def test_seed_normalises_canonical_key(self) -> None:
        from evals.runner import load_cards

        cards = load_cards("seed", sample_size=3)
        assert len(cards) == 3
        assert all("human_canonical" in c for c in cards)

    def test_unknown_benchmark_raises(self) -> None:
        from evals.runner import load_cards

        with pytest.raises(ValueError, match="Unknown benchmark"):
            load_cards("nope")


class TestToolFiltering:
    def test_available_tool_names_lists_the_toolbox(self) -> None:
        from evals.runner import available_tool_names

        names = available_tool_names(allow_persistent_tools=False)
        assert "dry_run_effect" in names
        assert "read_game_state" in names
        assert names == sorted(names)

    def test_enabled_tools_filters_by_name(self) -> None:
        from evals.game_fixtures import EVAL_ACTOR_ID, EVAL_CARD_ID, EVAL_CREATOR_ID, build_eval_state
        from evals.runner import EvalConfig, _build_tools

        cfg = EvalConfig(enabled_tools=frozenset({"card_rag"}))
        tools = _build_tools(cfg, build_eval_state(), EVAL_ACTOR_ID, EVAL_CREATOR_ID, EVAL_CARD_ID)
        assert [t.name for t in tools] == ["card_rag"]

    def test_none_means_full_toolbox(self) -> None:
        from evals.game_fixtures import EVAL_ACTOR_ID, EVAL_CARD_ID, EVAL_CREATOR_ID, build_eval_state
        from evals.runner import EvalConfig, _build_tools

        cfg = EvalConfig(enabled_tools=None)
        tools = _build_tools(cfg, build_eval_state(), EVAL_ACTOR_ID, EVAL_CREATOR_ID, EVAL_CARD_ID)
        assert len(tools) > 1


# --------------------------------------------------------------------------- #
# Runner: end-to-end with a stubbed agent (no LLM), deterministic scorers only
# --------------------------------------------------------------------------- #
def _stub_run_agent(monkeypatch, verdict="ok"):
    """Patch run_agent to return a fixed InterpretResult and record kwargs."""
    from agent.contract import InterpretResult
    from models.effects import AddPointsOp, EffectProgram

    captured: dict = {}

    def fake_run_agent(title, description, state=None, actor_id=None, **kwargs):
        captured["title"] = title
        captured["state"] = state
        captured["actor_id"] = actor_id
        captured.update(kwargs)
        # exercise the callback so tool/usage metrics are populated
        cb = kwargs["config"]["callbacks"][0]
        cb.on_tool_start({"name": "card_rag"}, "q")
        return InterpretResult(
            program=EffectProgram(ops=[AddPointsOp(op="add_points", target="self", amount=5)]),
            verdict=verdict,
            comment="stub",
        )

    import agent.runtime as runtime

    monkeypatch.setattr(runtime, "run_agent", fake_run_agent)
    return captured


class TestRunBenchmark:
    def test_threads_production_parity_inputs(self, monkeypatch) -> None:
        captured = _stub_run_agent(monkeypatch)
        from evals.game_fixtures import EVAL_ACTOR_ID, EVAL_CARD_ID, EVAL_CREATOR_ID
        from evals.runner import EvalConfig, run_benchmark

        cfg = EvalConfig(benchmark="eval", sample_size=2, use_judge=False, max_tool_calls=7)
        run = run_benchmark(cfg, timestamp="t", progress=False)

        assert captured["state"] is not None  # full parity: live state threaded
        assert captured["actor_id"] == EVAL_ACTOR_ID
        assert captured["creator_id"] == EVAL_CREATOR_ID
        assert captured["card_id"] == EVAL_CARD_ID
        assert captured["max_tool_calls"] == 7
        assert len(run.rows) == 2
        assert all(r.metrics.tool_calls == 1 for r in run.rows)
        assert all(r.scores["executability"] == 1.0 for r in run.rows)

    def test_n_samples_multiplies_rows_and_reports_consistency(self, monkeypatch) -> None:
        _stub_run_agent(monkeypatch)
        from evals.runner import EvalConfig, run_benchmark

        cfg = EvalConfig(benchmark="eval", sample_size=2, n_samples=3, use_judge=False)
        run = run_benchmark(cfg, timestamp="t", progress=False)
        assert len(run.rows) == 6
        agg = run.aggregate()
        assert agg["cases"] == 6
        assert agg["unique_cards"] == 2
        assert "consistency" in agg

    def test_concurrency_preserves_row_order_and_results(self, monkeypatch) -> None:
        _stub_run_agent(monkeypatch)
        from evals.runner import EvalConfig, run_benchmark

        serial = run_benchmark(
            EvalConfig(benchmark="eval", sample_size=4, use_judge=False), timestamp="t", progress=False
        )
        threaded = run_benchmark(
            EvalConfig(benchmark="eval", sample_size=4, use_judge=False, concurrency=4),
            timestamp="t",
            progress=False,
        )
        assert [r.card_id for r in threaded.rows] == [r.card_id for r in serial.rows]
        assert [(r.card_id, r.sample_index) for r in threaded.rows] == [
            (r.card_id, r.sample_index) for r in serial.rows
        ]
        assert all(r.scores["executability"] == 1.0 for r in threaded.rows)

    def test_tracing_off_by_default_for_agent_and_scorers(self, monkeypatch) -> None:
        _stub_run_agent(monkeypatch)
        import agent.runtime as runtime
        from langsmith.run_helpers import get_tracing_context

        from evals.eval_core import Score, create_scorer
        from evals.runner import EvalConfig, _run_one, load_cards

        seen: dict = {}
        inner = runtime.run_agent

        def spy(*args, **kwargs):
            seen["agent_enabled"] = get_tracing_context()["enabled"]
            return inner(*args, **kwargs)

        def probe(context):
            seen["scorer_enabled"] = get_tracing_context()["enabled"]
            return Score(score=1.0)

        monkeypatch.setattr(runtime, "run_agent", spy)
        card = load_cards("eval", sample_size=1)[0]
        _run_one(EvalConfig(benchmark="eval"), card, 0, [create_scorer("probe", "records ctx", probe)])
        assert seen["agent_enabled"] is False
        assert seen["scorer_enabled"] is False

    def test_tracing_true_inherits_ambient_context(self, monkeypatch) -> None:
        _stub_run_agent(monkeypatch)
        import agent.runtime as runtime
        from langsmith.run_helpers import get_tracing_context

        from evals.runner import EvalConfig, _run_one, load_cards

        seen: dict = {}
        inner = runtime.run_agent

        def spy(*args, **kwargs):
            seen["enabled"] = get_tracing_context()["enabled"]
            return inner(*args, **kwargs)

        monkeypatch.setattr(runtime, "run_agent", spy)
        card = load_cards("eval", sample_size=1)[0]
        _run_one(EvalConfig(benchmark="eval", tracing=True), card, 0, [])
        assert seen["enabled"] is None  # ambient — not forced on or off

    def test_config_dict_records_tracing(self) -> None:
        from evals.runner import EvalConfig

        assert EvalConfig().to_dict()["tracing"] is False
        assert EvalConfig(tracing=True).to_dict()["tracing"] is True

    def test_scorer_failure_is_recorded_not_fatal(self, monkeypatch) -> None:
        _stub_run_agent(monkeypatch)
        from evals.eval_core import create_scorer
        from evals.runner import EvalConfig, _run_one, load_cards

        def explode(context):
            raise RuntimeError("scorer bug")

        broken = create_scorer("broken", "always raises", explode)
        card = load_cards("eval", sample_size=1)[0]
        row = _run_one(EvalConfig(benchmark="eval"), card, 0, [broken])
        assert "broken" not in row.scores
        assert row.score_meta["broken"]["error"] == "scorer bug"

    def test_aggregate_has_target_metrics(self, monkeypatch) -> None:
        _stub_run_agent(monkeypatch)
        from evals.runner import EvalConfig, run_benchmark

        cfg = EvalConfig(benchmark="eval", sample_size=2, use_judge=False)
        agg = run_benchmark(cfg, timestamp="t", progress=False).aggregate()
        for key in (
            "mean_tool_calls",
            "per_tool_calls",
            "p95_latency_ms",
            "executability",
            "did_something",
            "invalid_rate",
            "agent_error_rate",
        ):
            assert key in agg
        assert agg["agent_error_rate"] == 0.0

    def test_agent_error_rate_counts_runtime_fallbacks(self, monkeypatch) -> None:
        from agent.contract import InterpretResult

        import agent.runtime as runtime

        monkeypatch.setattr(
            runtime,
            "run_agent",
            lambda *args, **kwargs: InterpretResult(verdict="invalid", comment="boom", agent_error=True),
        )
        from evals.runner import EvalConfig, run_benchmark

        cfg = EvalConfig(benchmark="eval", sample_size=2, use_judge=False)
        agg = run_benchmark(cfg, timestamp="t", progress=False).aggregate()
        assert agg["agent_error_rate"] == 1.0


# --------------------------------------------------------------------------- #
# Store round-trip
# --------------------------------------------------------------------------- #
class TestStore:
    def test_save_and_load_roundtrip(self, monkeypatch, tmp_path) -> None:
        _stub_run_agent(monkeypatch)
        import evals.store as store
        from evals.runner import EvalConfig, run_benchmark

        monkeypatch.setattr(store, "runs_dir", lambda: tmp_path)
        run = run_benchmark(
            EvalConfig(benchmark="eval", sample_size=2, use_judge=False),
            timestamp="20260714-000000",
            progress=False,
        )
        path = store.save_run(run)  # timestamp defaults from the run itself
        assert path.exists()

        loaded = store.load_runs()
        assert len(loaded) == 1
        payload = loaded[0]
        assert payload["timestamp"] == "20260714-000000"
        assert payload["summary"]["cases"] == 2
        assert store._slug(payload["summary"]["model"]) in path.name  # filename carries the resolved model
        assert len(payload["rows"]) == 2
        # payload is plain JSON-serialisable
        json.dumps(payload)

    def test_save_run_requires_some_timestamp(self, monkeypatch, tmp_path) -> None:
        _stub_run_agent(monkeypatch)
        import evals.store as store
        from evals.runner import EvalConfig, run_benchmark

        monkeypatch.setattr(store, "runs_dir", lambda: tmp_path)
        run = run_benchmark(EvalConfig(benchmark="eval", sample_size=1, use_judge=False), progress=False)
        with pytest.raises(ValueError, match="timestamp"):
            store.save_run(run)


# --------------------------------------------------------------------------- #
# Viz smoke: charts render without error on synthetic summaries
# --------------------------------------------------------------------------- #
def _fake_summary(label: str, **overrides) -> dict:
    base = {
        "label": label,
        "benchmark": "eval",
        "model": "gpt-5.4-mini",
        "cases": 5,
        "n_samples": 1,
        "intent_match": 0.8,
        "target_accuracy": 0.7,
        "persistence_accuracy": 0.6,
        "magnitude_sign": 0.9,
        "sandbox_behavior": 0.5,
        "executability": 0.85,
        "did_something": 0.9,
        "mean_tool_calls": 2.3,
        "mean_cost_usd": 0.0012,
        "total_cost_usd": 0.006,
        "p50_latency_ms": 800.0,
        "p95_latency_ms": 2100.0,
        "invalid_rate": 0.1,
        "per_tool_calls": {"card_rag": 12, "web_search": 3},
    }
    base.update(overrides)
    return base


class TestViz:
    """Chart smoke tests need matplotlib (the `evals` dependency group); skip
    cleanly in a dev-only environment rather than fail on import."""

    def test_all_charts_and_tables_render(self) -> None:
        matplotlib = pytest.importorskip("matplotlib")
        matplotlib.use("Agg")

        from evals import viz

        summaries = [
            _fake_summary("baseline"),
            _fake_summary("no-rag", intent_match=0.7, per_tool_calls={"web_search": 5}),
        ]
        viz.plot_quality(summaries)
        viz.plot_efficiency(summaries)
        viz.plot_tool_usage(summaries)
        viz.plot_cost_vs_quality(summaries)
        table = viz.summary_table(summaries)
        assert list(table["label"]) == ["baseline", "no-rag"]

    def test_tool_usage_handles_no_tools(self) -> None:
        matplotlib = pytest.importorskip("matplotlib")
        matplotlib.use("Agg")

        from evals import viz

        viz.plot_tool_usage([_fake_summary("x", per_tool_calls={})])

    def test_worst_cards_sorts_ascending(self) -> None:
        from evals import viz

        payload = {
            "rows": [
                {"card_id": "a", "title": "A", "verdict": "ok", "scores": {"executability": 1.0}, "score_meta": {}},
                {
                    "card_id": "b",
                    "title": "B",
                    "verdict": "invalid",
                    "scores": {"executability": 0.0},
                    "score_meta": {},
                },
            ]
        }
        df = viz.worst_cards(payload, metric="executability", n=5)
        assert list(df["card_id"]) == ["b", "a"]
