"""Bead gu0: does the lean toolbox still beat the full toolbox on the SEED
benchmark (where card-RAG is actually exercised), or only on eval_hard?"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evals.runner import EvalConfig, run_benchmark
from evals.store import save_run

HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
LEAN = frozenset({"dry_run_effect", "read_engine_methods", "read_game_state"})

CONFIGS = [
    EvalConfig(benchmark="seed", model_name=HAIKU, max_tool_calls=12, n_samples=3,
               sample_size=69, concurrency=8,
               label="gu0 - seed - maxtools=12 - FULL - n3"),
    EvalConfig(benchmark="seed", model_name=HAIKU, max_tool_calls=12, n_samples=3,
               sample_size=69, concurrency=8, enabled_tools=LEAN,
               label="gu0 - seed - maxtools=12 - dry_run+engine_methods+game_state - n3"),
]

for cfg in CONFIGS:
    print(f"=== RUN: {cfg.label}", flush=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result = run_benchmark(cfg, timestamp=stamp, progress=True)
    path = save_run(result)
    print(f"=== SAVED: {path}", flush=True)

print("ALL DONE")
