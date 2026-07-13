# Eval Suite — Honest Assessment (bead 82f.5)

This is a *vetting* of the evaluation code under `src/evals/`, not a redesign. It
records what each script actually measures, whether the test set and judge are sound,
what runs and what doesn't, and which numbers (if any) can currently be trusted.

**Bottom line:** the eval *design* is reasonable and the gold test set is real and
well-labelled, but the suite **cannot currently run against the configured LLM
gateway** — three concrete code defects block it (see §4). No end-to-end numbers exist
yet; every figure in [`RETRIEVER_ANALYSIS.md`](RETRIEVER_ANALYSIS.md) is a
self-declared hand-authored placeholder. Fixing the three defects is tracked in bead
**82f.11**; once fixed, the harness produces real scores (a partial run reached the
gateway and only failed on the `temperature` param — see §4.3).

## 1. What the suite is supposed to measure

| Script | Measures | LLM used? |
|---|---|---|
| `evals.harness` | End-to-end interpretation quality: runs `agent.runtime.run_agent` on each gold card, scores the output on 4 dimensions. | Yes — agent + LLM judge |
| `evals.retriever_ab` | Retrieval quality only: dense vs. multi-query retriever, scored structurally (recall_nonempty, timing_match, target_match). | No — deterministic, no judge |
| `evals.improvement_ab` | Before/after: bare agent vs. agent primed with retrieved few-shot exemplars. | Yes — agent + LLM judge |

Scorers (`src/evals/scorers.py`): `dsl_validity` is a pure structural check (does the
output contain a non-empty, Pydantic-valid `EffectProgram`?); `intent_match`,
`target_accuracy`, and `timing_accuracy` are all LLM-as-judge dimensions produced by
`JudgeLLM` (`src/evals/judge.py`), which scores intent / timing / target / trigger /
magnitude-sign / overall on a 0–1 scale against the human-canonical label.

## 2. Is the test set sound?

Yes, with a caveat. `data/eval/eval_cards.json` is **41 hand-annotated gold cards**,
each with a structured `human_canonical` label (timing, target, placement,
trigger_event, ops, magnitude_sign). Spot-checks look correct and consistent with the
engine's op vocabulary (e.g. "Gain 5 Points" → `add_points{target:self, amount:5}`,
`immediate`, `on_play`, `positive`). This is a legitimate gold set for scoring
interpretation.

Caveats:
- **Small (n=35).** Fine for a directional baseline; too small for tight confidence
  intervals or per-category breakdowns.
- **Authored, not photo-derived.** The larger `real_cards.json` (~700 transcribed
  album cards) has `human_canonical: null`, so it is a retrieval/annotation pool, not a
  scored set (see bd memory `eval-corpus-two-files`). The harness correctly scores only
  the gold set.

## 3. Is the judge sound?

Design is reasonable: a structured-output `Verdict` model with per-dimension 0–1
scores and a strict rubric prompt (e.g. "'all players' interpreted as 'self' scores 0
for target_placement_correct"). Scoring each dimension independently is good practice.

Concerns, none blocking on their own:
- **Single-judge, no calibration.** No inter-rater / self-consistency check against the
  human labels, so judge reliability is unquantified. A cheap improvement would be to
  measure judge agreement on the 35 gold cards where the "correct" answer is known.
- **Judge model = graded model family.** The judge routes through the same
  `get_chat_model` factory as the agent. That's convenient but means judge and agent can
  share failure modes. Acceptable for a baseline; worth noting.

## 4. What actually runs — three blocking defects

A real run was attempted against the configured gateway (`.env`: bifrost →
bedrock, `LLM_CHAT_MODEL=us.anthropic.claude-sonnet-5`). Three defects surfaced, each
independently confirmed. All are tracked for fixing in bead **82f.11** (kept out of
this bead, which is assessment-only).

### 4.1 Data-path fragility
`harness.py` computes `DEFAULT_DATA = Path(__file__).resolve().parents[3] /
"data"/"eval"/"eval_cards.json"`. That hard-codes the repo depth. Run from a git
worktree (`.claude/worktrees/<b>/src/evals/harness.py`), `parents[3]` resolves to the
wrong directory → `FileNotFoundError`. Passing `--data` explicitly is a workaround;
`retriever_ab` / `improvement_ab` import the same `DEFAULT_DATA`.

### 4.2 Hardcoded judge model
`JudgeLLM.__init__(model="gpt-5.4-mini")` passes an explicit model that **overrides**
the configured `LLM_CHAT_MODEL`. Against the gateway this 400s:
`could not auto resolve a provider for the request`. The judge should default to the
configured chat model.

### 4.3 Unconditional `temperature`
`agent.llm.get_chat_model` always sends `temperature=0`. The bedrock-routed Claude
model rejects it: `400 — "temperature is deprecated for this model."` **Confirmed:** the
identical gateway call with the configured model and *no* `temperature` returns cleanly
(`"OK"`). So the gateway and credentials are fine; only this param blocks the run.

## 5. Conclusions

- The eval **architecture** (gold set + structural + LLM-judge scorers, plus two A/B
  drivers) is sound and appropriate for this problem. It is *not* "AI slop" in design.
- It is, however, **unrun** against the real gateway: no trustworthy end-to-end numbers
  exist today, and the published tables are explicit placeholders.
- **Do not cite any eval number as measured** until bead 82f.11 lands and the commands
  in `RETRIEVER_ANALYSIS.md#how-to-regenerate` are re-run. WRITEUP.md (bead 82f.1)
  should keep its eval figures as `TODO(82f.11)` until then.
- Recommended follow-ups (beyond 82f.11): add a small judge-calibration check against
  the 35 gold labels, and consider growing the gold set past 35 for firmer numbers.
