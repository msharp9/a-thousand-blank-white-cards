#!/usr/bin/env python3
"""Verify LangSmith tracing is configured for the interpretation graph.

Usage:
    OPENAI_API_KEY=... LANGSMITH_API_KEY=... LANGSMITH_TRACING=true \\
        uv run python ops/verify_langsmith.py

Checks required env vars, runs the compiled graph on one sample card (which — with
tracing enabled — emits per-node spans to LangSmith), and prints where to look.
"""

from __future__ import annotations

import os

REQUIRED_ENV = ["OPENAI_API_KEY", "LANGSMITH_API_KEY"]
# Modern LANGSMITH_TRACING first; LANGCHAIN_TRACING_V2 is the legacy alias the SDK
# still honors, so accept either.
TRACING_ENV = ["LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"]

SAMPLE_CARD = {"title": "Gain 5 Points", "description": "You feel great. Gain 5 points immediately."}


def check_env() -> list[str]:
    """Return a list of missing/misconfigured env var messages (empty = all good)."""
    problems: list[str] = []
    for var in REQUIRED_ENV:
        if not os.environ.get(var):
            problems.append(f"missing required env var: {var}")
    if not any(os.environ.get(v, "").lower() in ("1", "true", "yes") for v in TRACING_ENV):
        problems.append("tracing not enabled: set LANGSMITH_TRACING=true (or legacy LANGCHAIN_TRACING_V2=true)")
    return problems


def run_sample() -> dict:
    """Run the graph on the sample card. Requires a real OPENAI_API_KEY."""
    from tbwc.agent.graph import interpret_card

    return interpret_card(SAMPLE_CARD["title"], SAMPLE_CARD["description"])


def main() -> int:
    problems = check_env()
    if problems:
        print("LangSmith verification: CONFIG PROBLEMS")
        for p in problems:
            print(f"  - {p}")
        print("\nFix the above, then re-run. See docs for LANGSMITH_* setup.")
        return 1

    project = os.environ.get("LANGSMITH_PROJECT") or os.environ.get("LANGCHAIN_PROJECT") or "default"
    print(f"Env OK. Running sample interpretation (project={project})...")
    result = run_sample()
    print(f"Interpretation verdict: {result.get('verdict')}")
    print(
        "\nNow open https://smith.langchain.com -> project "
        f"'{project}' -> Traces and confirm a new run with child spans: "
        "reason -> retrieve -> route_search -> classify -> emit_ops/gen_snippet -> judge."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
