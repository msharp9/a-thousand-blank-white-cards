"""Eval core — adapted from aiec1 lib/eval_core.py (no pandas).

Public API:
    run_eval(name, *, data, task, scorers) -> EvalRunReport
    EvalRunReport.summary() -> dict
    EvalRunReport.case_table() -> list[dict]
    compare_eval_reports(*reports) -> list[dict]
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import fmean
from time import perf_counter
from typing import Any, TypeAlias


@dataclass(frozen=True, slots=True)
class EvalItem:
    id: str
    input: Any
    expected: Any
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Every evaluation item needs a stable id.")


@dataclass(frozen=True, slots=True)
class Score:
    score: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"Score must be 0..1, got {self.score}")


@dataclass(frozen=True, slots=True)
class ScorerContext:
    item: EvalItem
    output: Any

    @property
    def input(self) -> Any:
        return self.item.input

    @property
    def expected(self) -> Any:
        return self.item.expected


ScorerFunction: TypeAlias = Callable[[ScorerContext], "Score | float"]


@dataclass(frozen=True, slots=True)
class Scorer:
    name: str
    description: str
    scorer: ScorerFunction

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("A scorer needs a name.")

    def evaluate(self, context: ScorerContext) -> Score:
        result = self.scorer(context)
        if isinstance(result, Score):
            return result
        if isinstance(result, int | float):
            return Score(score=float(result))
        raise TypeError(f"Scorer {self.name!r} must return Score or a number.")


def create_scorer(name: str, description: str, scorer: ScorerFunction) -> Scorer:
    return Scorer(name=name, description=description, scorer=scorer)


Task: TypeAlias = Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class EvalRunRow:
    item: EvalItem
    output: Any
    scores: Mapping[str, Score]
    task_latency_ms: float
    scoring_latency_ms: float

    def score(self, scorer_name: str) -> Score:
        try:
            return self.scores[scorer_name]
        except KeyError as e:
            raise KeyError(f"No score named {scorer_name!r}.") from e


@dataclass(frozen=True, slots=True)
class EvalRunReport:
    name: str
    scorers: tuple[Scorer, ...]
    rows: tuple[EvalRunRow, ...]

    def summary(self) -> dict[str, float | int | str]:
        if not self.rows:
            raise ValueError("Cannot summarize an empty report.")
        return {
            "evaluation": self.name,
            "cases": len(self.rows),
            **{s.name: fmean(row.score(s.name).score for row in self.rows) for s in self.scorers},
            "mean_task_latency_ms": fmean(r.task_latency_ms for r in self.rows),
            "mean_scoring_latency_ms": fmean(r.scoring_latency_ms for r in self.rows),
        }

    def case_table(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.rows:
            table_row: dict[str, Any] = {
                "case_id": row.item.id,
                "input": row.item.input,
                "expected": row.item.expected,
                "output": row.output,
                "task_latency_ms": row.task_latency_ms,
            }
            for s in self.scorers:
                result = row.score(s.name)
                table_row[s.name] = result.score
                table_row[f"{s.name}_meta"] = dict(result.metadata)
            rows.append(table_row)
        return rows


def run_eval(name: str, *, data: Sequence[EvalItem], task: Task, scorers: Sequence[Scorer]) -> EvalRunReport:
    if not name.strip():
        raise ValueError("Evaluation needs a name.")
    if not data:
        raise ValueError("Evaluation needs at least one item.")
    if not scorers:
        raise ValueError("Evaluation needs at least one scorer.")
    names = [s.name for s in scorers]
    if len(set(names)) != len(names):
        raise ValueError("Scorer names must be unique.")
    rows: list[EvalRunRow] = []
    for item in data:
        t0 = perf_counter()
        output = task(item.input)
        task_ms = (perf_counter() - t0) * 1_000
        t1 = perf_counter()
        ctx = ScorerContext(item=item, output=output)
        scores = {s.name: s.evaluate(ctx) for s in scorers}
        score_ms = (perf_counter() - t1) * 1_000
        rows.append(
            EvalRunRow(item=item, output=output, scores=scores, task_latency_ms=task_ms, scoring_latency_ms=score_ms)
        )
    return EvalRunReport(name=name, scorers=tuple(scorers), rows=tuple(rows))


def compare_eval_reports(*reports: EvalRunReport) -> list[dict[str, Any]]:
    if len(reports) < 2:
        raise ValueError("Compare at least two reports.")
    ref_ids = tuple(r.item.id for r in reports[0].rows)
    ref_scorers = tuple(s.name for s in reports[0].scorers)
    for rpt in reports[1:]:
        if tuple(r.item.id for r in rpt.rows) != ref_ids:
            raise ValueError("All compared reports must use the same ordered data.")
        if tuple(s.name for s in rpt.scorers) != ref_scorers:
            raise ValueError("All compared reports must use the same ordered scorers.")
    summaries = [rpt.summary() for rpt in reports]
    return sorted(summaries, key=lambda d: tuple(d.get(n, 0) for n in ref_scorers), reverse=True)
