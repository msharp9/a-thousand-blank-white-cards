"""Bead sit: rerun the haiku/eval_hard max_tool_calls sweep + tool ablations, n_samples=3."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evals.runner import EvalConfig, run_benchmark
from evals.store import save_run

HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

CONFIGS = [
    EvalConfig(
        benchmark="eval_hard",
        model_name=HAIKU,
        max_tool_calls=cap,
        n_samples=3,
        concurrency=8,
        label=f"haiku - eval_hard - maxtools={cap} - n3",
    )
    for cap in (6, 12, 18, 24)
] + [
    EvalConfig(
        benchmark="eval_hard",
        model_name=HAIKU,
        max_tool_calls=12,
        n_samples=3,
        concurrency=8,
        enabled_tools=frozenset({"dry_run_effect", "read_game_state"}),
        label="haiku - eval_hard - maxtools=12 - dry_run_effect+read_game_state - n3",
    ),
    EvalConfig(
        benchmark="eval_hard",
        model_name=HAIKU,
        max_tool_calls=12,
        n_samples=3,
        concurrency=8,
        enabled_tools=frozenset({"dry_run_effect", "read_engine_methods", "read_game_state"}),
        label="haiku - eval_hard - maxtools=12 - dry_run_effect+read_engine_methods+read_game_state - n3",
    ),
]

for cfg in CONFIGS:
    print(f"=== RUN: {cfg.label}", flush=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result = run_benchmark(cfg, timestamp=stamp, progress=True)
    path = save_run(result)
    print(f"=== SAVED: {path}", flush=True)

print("ALL DONE")
