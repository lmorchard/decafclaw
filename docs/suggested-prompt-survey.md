# Suggested-Prompt Survey

Archive-replay measurement harness for a **suggested next prompt** feature
(ghost-text tab completion in the web composer). The harness answers one
question before any UI work: *can we predict the user's next message well
enough to make a suggestion worth showing?*

Issue: [#1738](https://github.com/lmorchard/decafclaw/issues/1738). Sibling UI
issue: [#1739](https://github.com/lmorchard/decafclaw/issues/1739).

This is a survey tool, not the feature. It adds no WebSocket message type, no
post-turn hook, and no composer code.

## What it measures

A **settled boundary** is one archived turn transition: the transcript up to
the assistant's last reply, plus the user message that actually followed.
Synthetic runtime user-role messages are excluded with the same predicate
auto-titling uses (`is_synthetic_user_message`): `widget_response`,
`reflection_retry`, `system_update`, `[reflection]`, `[System Update:`.

A boundary is described by `turn_id` (``{conv_id}#u{N}``, N = 1-based index of
the real user message that followed) and `conv_id`, so every case is traceable
back to its archive.

### Granularities

| granularity | meaning |
|---|---|
| normalized exact | casefolded, punctuation-stripped equality |
| token-set Jaccard ≥ 0.6 | same intent in different words |
| vault direction | page-set overlap between the suggestion's retrieval and the real turn's |
| operator spot check | a 40-row sheet for judging "plausible but wrong" |

The first two are cheap string metrics. The third is the useful one: a prompt
can be worded differently but still point at the right vault page. The fourth
is the only check that catches a confident wrong suggestion.

### Offered-option recapitulation (Stage 0)

The no-model ceiling band: the assistant listed choices and the next user
message picked one. `analyze_offer` counts a message as offering a choice
only when it has a checkbox list, an explicit `Options`/`Next steps` header, or
a choice cue plus a trailing question. Whole-message repetition ("user retries
the same shell command") is reported separately; it is not a choice.

The metric is deliberately conservative. A low rate does not mean the feature
is worthless; it means the easy recitation band is small.

### Vault-direction proxy and calibration

Every archived turn may carry `provenance.vault` with the pages actually
injected. When present, that is the ground truth page set. When absent (the
local dev corpus has none), the harness uses the pages retrieved by the **real
next message** and records the source as `retrieval_proxy`.

The proxy is calibrated against trivial predictors so the score has a known
floor and ceiling:

- `reuse_previous_user` — retrieve the previous user message.
- `reuse_last_assistant` — retrieve the assistant's last reply.
- `global_connected` — the most-linked vault pages (static prior).
- `retrieval_of_actual` — the real next message itself (oracle; 1.0 under
  `retrieval_proxy`).
- analytic random floor — expected Jaccard of two independent K-subsets.

A model score is only interpretable next to these numbers.

### Empty is a valid answer

The predictor is instructed to output `NONE` when there is no obvious next
step. Abstention is a first-class metric, not an error.

## Commands

Run against the corpus you want to measure. The production instance keeps its
data at `DATA_HOME=/home/lmorchard/.decafclaw`; a checkout on that host reads
it with:

```bash
export DATA_HOME=/home/lmorchard/.decafclaw

# Stage 0: free signals + page calibration over the full archive.
uv run python -m decafclaw.survey stage0 --backend embeddings --page-sample 300

# Deterministic dev set (stratified by conversation length and recency).
uv run python -m decafclaw.survey devset --limit 200 \
  --out evals/results/suggested-prompt/prod-devset.jsonl

# Stage 2: score aux and active arms on the dev set.
uv run python -m decafclaw.survey score \
  --devset evals/results/suggested-prompt/prod-devset.jsonl \
  --arms aux,active --aux-model vertex-gemini-3.8-flash

# Bound the spend before spending it.
uv run python -m decafclaw.survey score --devset evals/results/suggested-prompt/prod-devset.jsonl --dry-run
```

Generate the dev set on the corpus host and leave it under `evals/results/`
(git-ignored) or another local path. Do not copy it into the checkout. The
scorer streams each successful model response to `model-cache.jsonl`, so an
interrupted run resumes without paying twice.

`--max-calls` (default 1200) refuses a plan above the cap before any call.
`--aux-model` overrides the aux arm; without it the aux arm follows
`effective_aux_model`, which collapses to the active model when
`auxiliary_model` is unset. Identical `(model, turn_id)` calls are deduplicated
and cached in `model-cache.jsonl`, so a rerun after an interruption costs
nothing for completed cases.

Page retrieval uses the production embedding index when available
(`search_similar_sync`). If the embedding endpoint is unavailable,
`--backend auto` falls back to a local fastembed index built in memory from
`iter_index_entries` (vault pages + journal entries). The chosen backend is
recorded in every summary. `--backend embeddings` refuses to fall back;
`--backend local` forces the offline path.

## Outputs

`evals/results/` is git-ignored. Each score run writes:

- `summary.json` — per-arm rates, direction calibration, spend estimate.
- `scores.jsonl` — one row per case and arm, including the raw response.
- `spot-check.md` — 40-row operator review sheet.
- `model-cache.jsonl` — reusable raw responses keyed by `(model, turn_id)`.

The dev set is written to `evals/suggested_prompt/devset.jsonl` and is
**git-ignored**: it contains raw conversation text from private archives. The
repo is public; only the sampler and this method page are committed. Regenerate
the set on the machine that holds the corpus.

## Limitations

- The dev set is corpus-specific and may contain private conversation text.
  It is generated locally and git-ignored; do not attach it to a PR.
- Archives that predate provenance persistence exercise Stage 0's
  `retrieval_proxy` path instead of the real-provenance path. The summary
  labels which source was used.
- The vault index reflects *current* page content, not the content at archive
  time. Old turns are scored against a newer vault.
- The page proxy measures where a suggestion points, not whether the action is
  right. A prompt can be correct and point nowhere.
- The operator spot check is a human judgment step. An agent-generated review
  is recorded as such, never presented as a human one.

## Results (2026-11-22, local dev corpus)

Corpus: the checkout's `data/decafclaw/workspace` archive — 3,003 conversations,
67 with at least one settled boundary, 320 boundaries. **This is not the
production corpus** (4,227 conversations in the issue); the host running the
survey has no access to the live archive. The dev set is 200 cases sampled
from those 320 boundaries, stratified by conversation length and recency.

### Stage 0 — free signals

| signal | value |
|---|---|
| assistant offered a choice | 63 / 320 boundaries (19.7%) |
| next message picked one | 3 (4.8% when offered, 0.9% of all) |
| — checkbox / listed / inline | 0 / 1 / 2 |
| whole-message repeat (not a choice) | 7 (2.2%) |
| final user message is stop-like | 2 / 2,999 conversations (0.1%) |

The easy recitation band is small. Even when the assistant explicitly offered
choices, the next message usually did not pick one in a form the matcher can
recover.

### Stage 0 — vault-direction calibration

Ground truth was `retrieval_proxy` for all 320 boundaries: local archives
predate provenance persistence. Top-k = 5 over 247 vault entries; analytic
random floor = 0.010.

| predictor | mean Jaccard | median |
|---|---|---|
| reuse previous user message | 0.266 | 0.111 |
| reuse assistant's last reply | 0.094 | 0.000 |
| global most-linked pages | 0.004 | 0.000 |
| retrieval of the real next message (oracle) | 1.000 | 1.000 |

The previous-user-message baseline sets a real floor: on this corpus the
previous message is often the best available clue to the next one.

### Stage 2 — baseline predictor

Two runs on the same 200 cases. Run A is the issue's as-configured comparison:
aux = `vertex-gemini-flash`, active = each conversation's archived model. 182
of 200 archived active models were already flash, so the arms collapse for most
cases. Run B is a controlled comparison: flash vs `claude-haiku-4.5`.

| arm | exact | token J ≥ 0.6 | abstained | direction mean |
|---|---|---|---|---|
| A: aux flash | 4/200 (2.0%) | 5/200 (2.5%) | 15 (7.5%) | 0.110 |
| A: active (mostly flash) | 4/200 (2.0%) | 5/200 (2.5%) | 15 (7.5%) | 0.113 |
| B: aux flash | 7/200 (3.5%) | 10/200 (5.0%) | 11 (5.5%) | 0.135 |
| B: active haiku 4.5 | 0/200 (0.0%) | 0/200 (0.0%) | 4 (2.0%) | 0.099 |

The cheap arm is at least as good as the expensive one on every string
metric, and slightly better on direction. Haiku writes longer, more
role-confused messages (for example, "I need to see what the human might ask
next after requesting research on OpenClaw for a second time."). If this
feature is built, use the aux ladder, not a stronger model.

The direction score sits **below the reuse-previous-message floor** (0.207 on
the dev subset). A trivial predictor points at the right vault neighborhood
more often than the model does.

### Failure taxonomy (run A, flash arm)

| outcome | cases |
|---|---|
| exact hit | 4 |
| near-miss wording (token J ≥ 0.3) | 9 |
| generic filler ("Okay, go ahead.") | 11 |
| hallucinated specific (new path/URL/number) | 5 |
| missed an offered option | 1 |
| too long | 1 |
| abstained | 15 |
| unrelated | 154 |

Unrelated suggestions dominate. The next user message is usually a **new
instruction or a correction**, not a selection from what the assistant just
offered. Generic acknowledgments and invented specifics are the two failure
shapes a UI would need to suppress before a ghost could be safe.

### Operator spot check

The 40-row sheet is generated by `score` at `spot-check.md` (not committed;
the dev set is). The assistant operator reviewed the run A sheet row by row:

| judgment | cases |
|---|---|
| useful (same intent, would have saved typing) | 6 / 40 |
| plausible but wrong (related, would mislead) | 13 / 40 |
| off (wrong or contrary to the user's intent) | 6 / 40 |
| abstained (safe, but missed a recoverable answer) | 15 / 40 |

Only 6 of 40 would have been worth showing; 13 more are the dangerous class —
plausible enough to accept, wrong enough to send the conversation elsewhere.
This is an agent review, not a human one. A human should re-read the sheet
before treating the counts as final.

### Spend

| run | unique calls | prompt tokens | completion tokens | cost |
|---|---|---|---|---|
| A (as configured) | 218 | 178k | 1.7k | ~$0.08 |
| B (flash vs haiku) | 400 | 357k | 5.5k | ~$0.26 |
| total | 618 | 535k | 7.2k | **~$0.34** |

Stage 0 and all page retrieval are local and free. The dry run estimated
$0.23 for run B before the call; the actual was $0.26. The hard cap
(`--max-calls`, default 1200) was never approached.

## Recommendation

**No-go for a general ghost-text next-prompt feature now.**

- Exact and near-exact reuse are 2–5%, an order of magnitude below the 20%
  gate the issue proposed.
- The model does not beat a trivial "reuse the previous message" predictor on
  vault direction; it points somewhere less useful.
- In the operator sample, nearly twice as many suggestions were plausibly
  wrong as useful. A wrong ghost is worse than no ghost.

**Conditional path if the idea is revisited:** the only band with signal is
short recitation/continuation — confirmations ("Approved"), repeated commands
("ls ~", "show memories"), and explicit option picks. That band is ~1% of all
boundaries and ~5% of offered-choice turns on this corpus, so a feature limited
to it would be rarely visible. Suppression rules from the taxonomy (generic
acknowledgment, invented specifics, echoed questions, > 20 words) are a
prerequisite, not a polish step.

**The missing input is the production corpus.** This run used a dev checkout
full of automated smoke tests and repeated debug loops, with no provenance.
Before any UI issue starts, rerun:

```bash
uv run python -m decafclaw.survey stage0
uv run python -m decafclaw.survey devset --limit 200
uv run python -m decafclaw.survey score --aux-model vertex-gemini-flash --dry-run
uv run python -m decafclaw.survey score --aux-model vertex-gemini-flash
```

on the real 4,227-conversation archive. The harness is the deliverable; this
memo is a byproduct from the corpus available here.
