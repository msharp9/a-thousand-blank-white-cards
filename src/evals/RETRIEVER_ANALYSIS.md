# Retriever & Improvement Analysis (Phase 6)

This document is the written justification, methodology, and results analysis for the
Phase 6 retrieval and quality-improvement work on the *1000 Blank White Cards* (TBWC)
card-interpretation agent. It covers two experiments:

1. Swapping the baseline dense retriever for an **advanced multi-query retriever**.
2. One additional targeted improvement — **few-shot exemplar injection** in `emit_ops`.

Both experiments are evaluated with the Phase 5 eval harness over the 35-card real
testset. The A/B driver scripts already exist (`evals.retriever_ab`,
`evals.improvement_ab`), but running them end-to-end requires a live
`OPENAI_API_KEY` (both to drive the agent graph and to run the LLM judge).

> **⚠️ Results in this document are ILLUSTRATIVE PLACEHOLDERS.** Every number in the
> tables below is a hand-authored estimate used to show the *shape* of the expected
> result, not a measured value. They must be regenerated with a live `OPENAI_API_KEY`
> using the exact commands in [How to regenerate](#4-how-to-regenerate) before this
> analysis is submitted as final.

---

## 1. Advanced Retriever Justification

**Choice: multi-query retrieval** (`MultiQueryCardRetriever` /
`advanced_retriever()` in [`src/rag/retrievers.py`](../rag/retrievers.py)).

### Why the baseline dense retriever is a poor fit for TBWC cards

The retriever's job is to surface *exemplar* cards — cards whose canonical effect
programs illustrate how to translate a new, unseen card into the engine DSL. Those
exemplars then feed downstream: they become few-shot guidance for `emit_ops`
(see §3) and context for classification.

Card text in TBWC is uniquely hostile to single-embedding retrieval:

- **Terse and colloquial.** Cards are hand-written by players. A card might read
  "everybody drinks" or "steal a point, jerk" — a few words of slang, in-jokes, and
  imperative shorthand rather than well-formed rules text.
- **Same effect, wildly different phrasing.** "Give a player 5 points", "someone
  gets +5", "award 5 to anyone you like", and "5 pts to a friend" are the *same
  effect program* (an `add_points` op targeting a chosen player) expressed four
  ways. Their surface embeddings can be far apart.
- **Structure matters more than words.** What we actually want to match on is the
  *effect structure* — the ops, their targets, and their timing — not the vocabulary.
  A single embedding of the raw description biases retrieval toward
  lexically/near-identical exemplars and systematically misses cards that share the
  effect structure but phrase it differently.

The practical failure mode of the dense baseline is **narrow recall**: for a given
query it tends to return a tight cluster of near-duplicate exemplars, leaving whole
regions of the "matching effect program" space unretrieved. When those missed
exemplars would have been the most instructive ones, the downstream generator has to
invent op shapes instead of mirroring a known-good pattern.

### How multi-query retrieval addresses this

`MultiQueryCardRetriever` prompts an LLM to generate `n` (default 3) short, distinct
paraphrases of the card description, each emphasizing a different aspect of the
card's intent. It then:

1. Runs the **original query plus each paraphrase** through the same base dense
   retriever (so the improvement is purely additive over the baseline, not a
   different index).
2. Retrieves `k` results per query.
3. Returns the **deduplicated union**, keyed by `card_id` (falling back to `title`),
   preserving first-seen order.

Because each paraphrase lands in a slightly different neighborhood of the embedding
space, the union covers a broader, more diverse set of exemplars — including cards
that express the *same effect structure* in different words but were invisible to the
single original embedding. This directly targets the narrow-recall failure mode
above: we trade a small amount of latency (one extra LLM call to paraphrase, plus
`n` additional vector lookups) for materially higher recall of structurally-relevant
exemplars.

The implementation is deliberately **interchangeable** with the plain dense
retriever: both satisfy the `Retriever = Callable[[str, int], list[dict]]` interface,
so the agent graph selects between them purely via the `retriever_mode` config key
(`"dense"` vs `"advanced"`) with no graph changes. Paraphrase generation is also
**non-fatal**: if the LLM call or JSON parse fails, it logs a warning and falls back
to just the original query, so the advanced retriever degrades gracefully to the
dense baseline rather than erroring.

---

## 2. Retriever A/B — Methodology & Results

### Methodology

The A/B driver is [`src/evals/retriever_ab.py`](./retriever_ab.py). It:

- Loads the **35-card hand-annotated gold testset** from
  [`data/eval/eval_cards.json`](../../../data/eval/eval_cards.json) via the Phase 5
  harness (`load_eval_items`).
- Runs the **same agent graph** twice — once with `retriever_mode="dense"`, once with
  `retriever_mode="advanced"` — invoking `graph.invoke` per card and normalizing the
  output with `_normalise_graph_output`.
- Scores every run with the full scorer set `ALL_SCORERS`
  ([`src/evals/scorers.py`](./scorers.py)):
  - **intent_match** — LLM judge: does the generated effect do what the card says?
  - **dsl_validity** — structural check: is `effect_program` a non-empty, Pydantic-valid
    `EffectProgram`? (This is the only non-judge, deterministic scorer.)
  - **target_accuracy** — LLM judge: is the effect's target/placement correct?
  - **timing_accuracy** — LLM judge: is the timing (immediate/persistent/triggered)
    correct?
- Reports each scorer's mean plus `mean_task_latency_ms` (wall-clock per card),
  and a best-first ranking via `compare_eval_reports`.

The three judge-based scorers share a single `gpt-5.4-mini` judge
([`src/evals/judge.py`](./judge.py)) that returns a structured `Verdict`; only
`dsl_validity` is measured structurally.

Run it with (see §4 for env setup):

```bash
uv run python -m evals.retriever_ab
```

### Results

> **⚠️ Illustrative values — regenerate with the command above using a live
> `OPENAI_API_KEY` before submission.** The numbers below are hand-authored estimates
> showing the expected direction of change, not measurements.

| Metric | dense | advanced | delta |
| --- | ---: | ---: | ---: |
| intent_match | 0.71 | 0.78 | +0.07 |
| dsl_validity | 0.83 | 0.86 | +0.03 |
| target_accuracy | 0.74 | 0.79 | +0.05 |
| timing_accuracy | 0.80 | 0.82 | +0.02 |
| mean_task_latency_ms | 3200 | 4600 | +1400 |

*Expected shape:* the advanced retriever should nudge the judge-based dimensions
(especially `intent_match`) upward by surfacing more structurally-relevant
exemplars, at the cost of higher per-card latency from the extra paraphrase LLM call
and additional vector lookups.

---

## 3. "One Other Improvement" — Few-shot Exemplars in `emit_ops`

### Justification

The second improvement targets the generation step rather than retrieval.
`emit_ops` ([`src/agent/nodes.py`](../agent/nodes.py)) produces the
`EffectProgram` for immediate-mode cards. Even though it uses
`ChatOpenAI.with_structured_output(EffectProgram)` — so the *schema* is enforced by
Pydantic — the model still has to choose *which ops and field names* to emit. With
TBWC's short, idiosyncratic card text, the model tends to **invent op shapes and
field names that don't exist** in the canonical vocabulary, or map an effect onto a
plausible-but-wrong op. Structured output catches malformed JSON; it does not catch a
semantically wrong-but-well-typed program, and repeated schema-repair attempts on
invented shapes hurt both validity and intent fidelity.

The fix: inject the **top-3 retrieved exemplars with their canonical effects** as
few-shot patterns directly into the `emit_ops` prompt. Each exemplar shows a real
card's title, description, and its *known-good* canonical effect, so the model
mirrors concrete, in-vocabulary op patterns instead of inventing them.

This is implemented by `_format_exemplars_fewshot` (formats up to 3 retrieved
exemplars into an "Example N:" block) and gated by the `few_shot_exemplars` config
toggle in `emit_ops` (default `True`; when `False`, no exemplars are injected and the
node behaves like the pre-improvement baseline). The toggle is what makes a clean
before/after A/B possible.

Note the two improvements **compound**: better retrieval (§1) yields better
exemplars, which makes the few-shot injection here more effective. The A/B below
isolates the few-shot effect by holding `retriever_mode` fixed (default `dense`).

### Methodology

The driver is [`src/evals/improvement_ab.py`](./improvement_ab.py). It runs the
same 35-card testset twice against the same graph, toggling only
`few_shot_exemplars`:

- `before_no_fewshot` → `few_shot_exemplars=False`
- `after_fewshot` → `few_shot_exemplars=True`

`retriever_mode` is held constant (default `dense`, overridable via
`--retriever-mode`) so the delta is attributable to few-shot injection alone. Scoring
uses the same `ALL_SCORERS` and judge as §2.

```bash
uv run python -m evals.improvement_ab
```

### Results

> **⚠️ Illustrative values — regenerate with the command above using a live
> `OPENAI_API_KEY` before submission.** Hand-authored estimates showing expected
> direction only.

| Metric | before (no few-shot) | after (few-shot) | delta |
| --- | ---: | ---: | ---: |
| intent_match | 0.70 | 0.77 | +0.07 |
| dsl_validity | 0.79 | 0.91 | +0.12 |
| target_accuracy | 0.73 | 0.78 | +0.05 |
| timing_accuracy | 0.79 | 0.81 | +0.02 |
| mean_task_latency_ms | 3100 | 3300 | +0.200k |

*Expected shape:* few-shot injection should lift `dsl_validity` the most — its whole
purpose is to stop the model inventing invalid op shapes — with a secondary lift to
`intent_match`. Latency should barely move, since it adds prompt tokens but no extra
LLM calls (the exemplars are already retrieved upstream).

---

## 4. How to Regenerate

Both scripts drive the live agent graph **and** an LLM judge, so an OpenAI key is
mandatory. From the repository root:

```bash
# Required: OpenAI key powers the agent graph, the multi-query paraphraser,
# and the gpt-5.4-mini judge.
export OPENAI_API_KEY=sk-...

# Optional: enable LangSmith tracing for per-run inspection of judge calls.
# export LANGSMITH_API_KEY=ls-...
# export LANGSMITH_TRACING=true
# export LANGSMITH_PROJECT=phase6

# Experiment 1 — retriever A/B (dense vs advanced multi-query)
uv run python -m evals.retriever_ab

# Experiment 2 — few-shot before/after
uv run python -m evals.improvement_ab
```

Useful flags (both scripts):

- `--data PATH` — use a different testset (defaults to `data/eval/eval_cards.json`).
- `--limit N` — evaluate only the first `N` cards (handy for a quick smoke run that
  keeps API cost/latency down).
- `--retriever-mode {dense,advanced}` — *(improvement_ab only)* which retriever to hold
  fixed while toggling few-shot; defaults to `dense`.

**Where output goes:** both scripts print to **stdout** — a Markdown comparison table
(rendered by `render_ab_table` / `render_improvement_table`) followed by a best-first
ranking. To capture results, redirect stdout, e.g.:

```bash
uv run python -m evals.retriever_ab   > retriever_ab_results.md
uv run python -m evals.improvement_ab > improvement_ab_results.md
```

Then replace the illustrative tables in §2 and §3 above with the regenerated ones and
delete the caveat banners.

---

## 5. Analysis & Conclusions

**Multi-query retrieval — recall vs latency.** The core hypothesis is that TBWC's
terse, colloquial card text defeats single-embedding retrieval by clustering results
around lexical near-duplicates. Paraphrasing the query into several intent-focused
variants and unioning their results should broaden recall into structurally-relevant
exemplars that the single embedding missed. We therefore expect the advanced
retriever to lift the *judge-based* dimensions — `intent_match` most of all, since
better exemplars most directly improve semantic fidelity — while `dsl_validity` moves
less on its own (validity is bounded by the generator, not the retriever). The clear
cost is latency: one extra LLM call to paraphrase plus `n` additional vector lookups
per card, which is why `mean_task_latency_ms` is expected to rise noticeably. The
trade is favorable if the intent/target gains hold, since correctness matters more
than a ~1s per-card latency increase in an offline interpretation pipeline.

**Few-shot exemplars — validity first.** The few-shot improvement attacks a different
failure mode: a well-typed but wrong (or op-inventing) `EffectProgram`. By showing the
model three real cards paired with their canonical effects, we expect the largest
gain on `dsl_validity` — the metric most tied to emitting real, in-vocabulary op
shapes — with a secondary lift to `intent_match` from pattern mirroring. Latency
should be nearly flat because the exemplars are already retrieved upstream; few-shot
only adds prompt tokens, not round-trips. The two improvements are complementary and
compounding: retrieval determines *which* exemplars exist to show, and few-shot
determines *how effectively* they steer generation — so the strongest configuration
should be `advanced` retrieval with few-shot on.

**On the measurement instrument.** All of the above is measured by the Phase 5 eval
harness, which is the instrument, not the subject, of these experiments. Three of the
four scorers (`intent_match`, `target_accuracy`, `timing_accuracy`) are **judge-based**,
delegating to a single `gpt-5.4-mini` judge that returns a structured `Verdict`; only
`dsl_validity` is a **deterministic structural check** (non-empty, Pydantic-valid
`EffectProgram`). This has two consequences worth stating plainly. First, the
judge-based scores carry LLM-judge variance and should be read as directional signal
over 35 cards, not high-precision measurements — small deltas may be within noise, and
re-running can shift them. Second, `dsl_validity` is the most trustworthy single
number because it is fully deterministic; a real, non-illustrative lift there is the
strongest evidence that few-shot injection works as intended. Final conclusions should
be drawn only from regenerated numbers (§4), with the illustrative tables above
treated strictly as placeholders indicating expected direction.
