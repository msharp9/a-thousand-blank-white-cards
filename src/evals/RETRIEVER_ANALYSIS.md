# Retriever & Improvement Analysis (Phase 6)

This document is the written justification, methodology, and results analysis for the
Phase 6 retrieval and quality-improvement work on the *1000 Blank White Cards* (TBWC)
card-interpretation agent. It covers two experiments:

1. Swapping the baseline dense retriever for an **advanced multi-query retriever**.
2. One additional targeted improvement — **few-shot exemplar injection** in `emit_ops`.

Both experiments are evaluated with the Phase 5 eval harness over the 35-card real
testset. The A/B driver scripts already exist (`evals.retriever_ab`,
`evals.improvement_ab`). `improvement_ab` drives the single tool-calling agent
(`agent.runtime.run_agent`) and the LLM judge, so it requires a live
`OPENAI_API_KEY`. `retriever_ab` was rewired to compare the two retrievers directly
with deterministic structural scorers (no agent, no judge) — see §2.

> **⚠️ Results in this document are ILLUSTRATIVE PLACEHOLDERS.** Every number in the
> tables below is a hand-authored estimate used to show the *shape* of the expected
> result, not a measured value. They must be regenerated with a live `OPENAI_API_KEY`
> using the exact commands in [How to regenerate](#4-how-to-regenerate) before this
> analysis is submitted as final.

---

## 1. Advanced Retriever Justification

**Choice: multi-query retrieval** (`MultiQueryCardRetriever` /
`advanced_retriever()` in [`src/agent/rag/retrievers.py`](../rag/retrievers.py)).

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
so callers select between them by constructing one or the other
(`dense_retriever()` vs `advanced_retriever()`). Paraphrase generation is also
**non-fatal**: if the LLM call or JSON parse fails, it logs a warning and falls back
to just the original query, so the advanced retriever degrades gracefully to the
dense baseline rather than erroring.

---

## 2. Retriever A/B — Methodology & Results

### Methodology

The A/B driver is [`src/evals/retriever_ab.py`](./retriever_ab.py). Since the legacy
graph (and its `retriever_mode` config knob) was retired, the driver now compares the
two retrievers **directly** rather than through a downstream interpreter — the truest,
fully deterministic test of retrieval quality. It:

- Loads the **35-card hand-annotated gold testset** from
  [`data/eval/eval_cards.json`](../../../data/eval/eval_cards.json) via the Phase 5
  harness (`load_eval_items`).
- For each card, retrieves the top-k exemplars with `dense_retriever()` and again with
  `advanced_retriever()` (from [`src/agent/rag/retrievers.py`](../rag/retrievers.py)).
- Scores every run with three **deterministic, structural** retrieval-quality scorers
  (no LLM judge):
  - **recall_nonempty** — did the retriever return at least one exemplar?
  - **timing_match** — does any retrieved exemplar's canonical `timing` match the gold
    card's expected timing?
  - **target_match** — does any retrieved exemplar's canonical `target` match expected?
- Reports each scorer's mean plus `mean_task_latency_ms` (wall-clock per card),
  and a best-first ranking via `compare_eval_reports`.

Because retrieval quality is measured structurally against the gold labels, this A/B
needs no LLM judge and no OpenAI key (unless `advanced_retriever`'s live paraphraser
is exercised against a real store).

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
| recall_nonempty | 1.00 | 1.00 | +0.00 |
| timing_match | 0.71 | 0.80 | +0.09 |
| target_match | 0.66 | 0.74 | +0.08 |
| mean_task_latency_ms | 120 | 1600 | +1480 |

*Expected shape:* the advanced retriever should surface a broader, more diverse set of
exemplars, so `timing_match`/`target_match` (does *any* retrieved exemplar share the
gold card's canonical timing/target?) rise, at the cost of higher per-card latency
from the extra paraphrase LLM call and additional vector lookups. `recall_nonempty`
stays saturated because both retrievers always return something for a non-empty store.

---

## 3. "One Other Improvement" — Few-shot Exemplar Priming

### Justification

The second improvement targets generation rather than retrieval. The single
tool-calling agent ([`src/agent/runtime.py`](../agent/runtime.py)) still has to choose
*which ops and field names* to emit. With TBWC's short, idiosyncratic card text, the
model tends to **invent op shapes and field names that don't exist** in the canonical
vocabulary, or map an effect onto a plausible-but-wrong op. Its structured
`InterpretResult` contract catches malformed output; it does not catch a semantically
wrong-but-well-typed program.

The fix: **prime the agent with the top-3 retrieved exemplars and their canonical
effects** by prepending them to the card description before interpretation. Each
exemplar shows a real card's title, description, and its *known-good* canonical
effect, so the model mirrors concrete, in-vocabulary op patterns instead of inventing
them. (In the retired graph this was a `few_shot_exemplars` toggle on the `emit_ops`
node; with the single agent it is done at the entry point by enriching the description,
which is the cleanest before/after axis available through `run_agent`'s public API.)

Note the two improvements **compound**: better retrieval (§1) yields better
exemplars, which makes the priming here more effective.

### Methodology

The driver is [`src/evals/improvement_ab.py`](./improvement_ab.py). It runs the
same 35-card testset twice through `agent.runtime.run_agent`, toggling only whether
the top-3 exemplars (from `dense_retriever()`) are prepended to the description:

- `before_no_fewshot` → bare card description
- `after_fewshot` → description primed with retrieved exemplars

Scoring uses the same `ALL_SCORERS` and `gpt-5.4-mini` judge as the harness.

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

*Expected shape:* exemplar priming should lift `dsl_validity` the most — its whole
purpose is to stop the model inventing invalid op shapes — with a secondary lift to
`intent_match`. Latency should barely move, since it adds prompt tokens plus one
retrieval call.

---

## 4. How to Regenerate

`improvement_ab` drives the live single agent **and** an LLM judge, so an OpenAI key
is mandatory for it; `retriever_ab` only needs a key if the advanced retriever's live
paraphraser runs against a real store. From the repository root:

```bash
# Required for improvement_ab: OpenAI key powers the single agent, the multi-query
# paraphraser, and the gpt-5.4-mini judge.
export OPENAI_API_KEY=sk-...

# Optional: enable LangSmith tracing for per-run inspection of judge calls.
# export LANGSMITH_API_KEY=ls-...
# export LANGSMITH_TRACING=true
# export LANGSMITH_PROJECT=tbwc-phase6

# Experiment 1 — retriever A/B (dense vs advanced multi-query)
uv run python -m evals.retriever_ab

# Experiment 2 — few-shot before/after
uv run python -m evals.improvement_ab
```

Useful flags (both scripts):

- `--data PATH` — use a different testset (defaults to `data/eval/eval_cards.json`).
- `--limit N` — evaluate only the first `N` cards (handy for a quick smoke run that
  keeps API cost/latency down).

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
