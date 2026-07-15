"""Tests for evals.analysis — frame building and failure-mode bucketing."""

from __future__ import annotations

from evals import analysis


def _row(card_id, scores, verdict="ok", output=None, score_meta=None, **extra):
    return {
        "card_id": card_id,
        "title": f"Card {card_id}",
        "sample_index": 0,
        "verdict": verdict,
        "output": {"agent_error": False, "resolution_plan": {"steps": [1]}, **(output or {})},
        "metrics": {"tool_calls": 3, "per_tool": {"dry_run_effect": 2, "card_rag": 1}, "total_tokens": 1000},
        "latency_ms": 5000.0,
        "cost_usd": 0.01,
        "scores": scores,
        "score_meta": score_meta or {},
        **extra,
    }


PERFECT = dict.fromkeys(analysis.ALL_METRICS, 1.0)


def _payload(label="m1 - eval", model="us.anthropic.claude-sonnet-5", rows=None, **config):
    return {
        "timestamp": "20260714-120000",
        "config": {"benchmark": "eval", "max_tool_calls": None, "enabled_tools": None, **config},
        "scorer_names": analysis.ALL_METRICS,
        "summary": {"benchmark": "eval", "label": label, "model": model, "cases": len(rows or []), **PERFECT},
        "rows": rows or [],
    }


def test_short_model_strips_provider_and_version():
    assert analysis.short_model("us.anthropic.claude-sonnet-5") == "sonnet-5"
    assert analysis.short_model("us.anthropic.claude-haiku-4-5-20251001-v1:0") == "haiku-4-5"
    assert analysis.short_model("google.gemma-4-31b") == "gemma-4-31b"
    assert analysis.short_model(None) == "default"


def test_quality_score_is_mean_of_quality_metrics():
    scores = dict.fromkeys(analysis.QUALITY_METRICS, 0.5)
    scores["dsl_validity"] = 0.0  # not a quality metric — must not drag the mean
    assert analysis.quality_score(scores) == 0.5
    assert analysis.quality_score({}) is None


def test_failure_buckets_clean_row_is_empty():
    assert analysis.failure_buckets(_row("c1", PERFECT)) == []


def test_failure_buckets_no_plan_suppresses_judge_noise():
    row = _row(
        "c2",
        dict.fromkeys(analysis.ALL_METRICS, 0.0),
        verdict="ok",
        output={"resolution_plan": None},
        score_meta={"dsl_validity": {"reason": "no resolution_plan in output"}},
    )
    buckets = analysis.failure_buckets(row)
    assert "no_plan" in buckets
    assert not any(b.startswith("wrong_") for b in buckets)


def test_failure_buckets_judge_and_ordering():
    scores = {**PERFECT, "target_accuracy": 0.0, "magnitude_sign": 0.0}
    row = _row("c3", scores, output={"agent_error": True})
    assert analysis.failure_buckets(row) == ["agent_error", "wrong_target", "wrong_magnitude"]


def test_failure_buckets_mechanical_reasons():
    row = _row(
        "c4",
        {**PERFECT, "executability": 0.0, "did_something": 0.0, "sandbox_behavior": 0.0},
        score_meta={
            "executability": {"reason": "CardTarget 'chosen_card' requires ctx.chosen_card_id"},
            "did_something": {"reason": "no mechanical ops emitted (no-op)"},
            "sandbox_behavior": {"reason": "execution failed: Snippet raised: 'victim'"},
        },
    )
    buckets = analysis.failure_buckets(row)
    assert buckets == ["unresolved_choice", "no_op", "snippet_crash"]


def test_frames_and_baseline_flag():
    payloads = [
        _payload(rows=[_row("c1", PERFECT)]),
        _payload(label="capped", rows=[_row("c1", PERFECT)], max_tool_calls=12),
    ]
    runs = analysis.runs_frame(payloads)
    rows = analysis.rows_frame(payloads)
    assert list(runs.is_baseline) == [True, False]
    assert len(rows) == 2
    assert rows.quality.tolist() == [1.0, 1.0]
    assert rows.model_short.unique().tolist() == ["sonnet-5"]


def test_bucket_counts_and_card_difficulty():
    bad = _row(
        "c9",
        dict.fromkeys(analysis.ALL_METRICS, 0.0),
        verdict="invalid",
        output={"resolution_plan": None},
        score_meta={"dsl_validity": {"reason": "no resolution_plan in output"}},
    )
    rows = analysis.rows_frame([_payload(rows=[_row("c1", PERFECT), bad])])
    counts = analysis.bucket_counts(rows)
    assert counts.loc["declared_invalid", "count"] == 1
    difficulty = analysis.card_difficulty(rows)
    assert difficulty.index[0] == "c9"  # worst first
    assert difficulty.loc["c9", "fail_rate"] == 1.0
    assert difficulty.loc["c9", "models_failed"] == ["sonnet-5"]
    assert difficulty.loc["c1", "fail_rate"] == 0.0
