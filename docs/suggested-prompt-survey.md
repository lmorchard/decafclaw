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
injected. When present, that is the ground truth page set. When absent (many
turns predate provenance persistence), the harness uses the pages retrieved by
the **real next message** and records the source as `retrieval_proxy`.

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
and streamed to `model-cache.jsonl`; pass the same `--out` directory to reuse
that cache after an interruption, or the default timestamped directory will
start fresh.

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

## Results (2026-11-22, production corpus)

Corpus: `DATA_HOME=/home/lmorchard/.decafclaw` on the production VM — 5,748
conversations, 266 with at least one settled boundary, 3,172 boundaries. The
dev set is 200 cases sampled from those boundaries, stratified by conversation
length and recency across 159 distinct conversations. It is generated on the
production host and stays out of git (see [Outputs](#outputs)).

Page retrieval used the production warm index (`embeddings-index`, 3,707
vault entries). Stage 0 calibrated a deterministic 300-boundary sample.

### Stage 0 — free signals

| signal | value |
|---|---|
| assistant offered a choice | 570 / 3,172 boundaries (18.0%) |
| next message picked one | 21 (3.7% when offered, 0.7% of all) |
| — checkbox / option / inline | 0 / 5 / 16 |
| whole-message repeat (not a choice) | 5 (0.2%) |
| final user message is stop-like | 0 / 279 multi-turn conversations (0.0%) |

The easy recitation band is small on production too. The stop metric is the
notable zero: users do not say "thanks, that's all" at the end of these
conversations. They simply stop. A forward-only suggestion has no textual
"finished" signal to learn from.

### Stage 0 — vault-direction calibration

The 300-boundary sample contains 22 turns with real provenance and 278 with
`retrieval_proxy`. Top-k = 5; analytic random floor = 0.0007.

| predictor | mean Jaccard | median | p90 |
|---|---|---|---|
| reuse previous user message | 0.055 | 0.000 | 0.125 |
| reuse assistant's last reply | 0.040 | 0.000 | 0.125 |
| global most-linked pages | 0.001 | 0.000 | 0.000 |
| retrieval of the real next message (oracle) | 0.934 | 1.000 | 1.000 |

The oracle is below 1.0 because for provenance turns the archived page set and
the retrieval result for the same message differ; that is the metric's own
noise band, not a scoring bug.

### Stage 2 — baseline predictor

Run A is the issue's as-configured comparison: aux = `vertex-gemini-3.8-flash`,
active = each conversation's archived model. 167 of 200 archived active models
were already the same flash model, so the arms collapse for most cases. Run B
is a controlled comparison against `claude-haiku-4.5`.

| arm | exact | token J ≥ 0.6 | abstained | direction mean |
|---|---|---|---|---|
| A: aux flash | 0/200 (0.0%) | 1/200 (0.5%) | 86 (43.0%) | 0.040 |
| A: active (mostly flash) | 0/200 (0.0%) | 1/200 (0.5%) | 78 (39.0%) | 0.040 |
| B: aux flash | 0/200 (0.0%) | 1/200 (0.5%) | 82 (41.0%) | 0.041 |
| B: active haiku 4.5 | 0/200 (0.0%) | 0/200 (0.0%) | 0 (0.0%) | 0.029 |

The predictor produced no exact match in 200 production turns and at most one
token-set hit. The stronger arm is not better: haiku never abstains, writes
longer messages, and lands on unrelated text 178/200 times. The cheap arm is
the better behavior. The direction score for both is below the
reuse-previous-message floor (0.055), so the suggestions point at a less useful
part of the vault than a trivial repeat.

On the dev subset used by the scorer, calibration was: previous user 0.055,
last assistant 0.040, global connected 0.002, oracle 0.952.

### Failure taxonomy (run B, aux flash arm)

| outcome | cases |
|---|---|
| exact hit | 0 |
| near-miss wording (token J ≥ 0.3) | 4 |
| missed an offered option | 2 |
| hallucinated specific (new path/URL/number) | 4 |
| abstained | 82 |
| unrelated | 108 |

The haiku arm's taxonomy is the cautionary version: zero abstentions, 178
unrelated, 7 generic filler, 5 too long, 4 hallucinated specifics. Abstention
in the flash arm is functioning as intended (it is safe), but at 41–43% it is
too frequent for a useful ghost. Lowering it is not obviously the fix: the
operator review below shows the non-abstentions are mostly wrong, and the
always-answer model is worse.

### Operator spot check

The 40-row sheet is generated at `evals/results/.../spot-check.md` and is
git-ignored with the dev set. The assistant operator reviewed the run A sheet:

| judgment | cases |
|---|---|
| useful (same intent, would have saved typing) | 5 / 40 |
| plausible but wrong (related, would mislead) | 8 / 40 |
| off (wrong or contrary to the user's intent) | 7 / 40 |
| abstained (safe, but missed a recoverable answer) | 20 / 40 |

The useful suggestions were mostly terse continuations: a paraphrase of a
commit-and-push confirmation, a one-word "another one?" follow-up, a reworded
"start drafting" instruction, and a "go ahead" approval. The dangerous class is
real too: a question about intake sources got a confident "publish it"
suggestion, and a discussion turn got an unrelated next step. These are
paraphrases, not quotes; the raw sheet stays local.

The sheet oversamples abstentions by design (it picks a spread of outcomes),
so its 50% abstention share is not the population rate. Among the 20
non-abstentions, 5 were useful, 8 were plausible but wrong, and 7 were off.
This is an agent review, not a human one. A human should re-read the sheet
before treating the counts as final.

### Spend

| run | unique calls | prompt tokens | completion tokens | priced cost |
|---|---|---|---|---|
| A (as configured) | 233 | 230k | 1.3k | ~$0.04 + unpriced flash/local calls |
| B (flash vs haiku) | 400 | 210k haiku + 201k flash | 5.7k | ~$0.23 + unpriced flash |
| total | 633 | — | — | **< $0.40** |

The dry run bounded the plan at 233 and 400 calls respectively; a conservative
$5/M input, $15/M output bound for the 633 calls is under $2, and the priced
actual is under $0.40. The hard cap (`--max-calls`, default 1200) was never
approached. Embeddings and Stage 0 are vertex-embedding calls with negligible
cost.

## Recommendation

**No-go for a general ghost-text next-prompt feature.**

- Production exact reuse is 0/200 and near-exact is 1/200. This is far below
  the 20% gate the issue proposed, not a marginal miss.
- The model does not beat "reuse the previous user message" on vault
  direction; it points somewhere less useful.
- 41–43% abstention plus a non-abstention mix that is useful only 25% of the
  time means the ghost would either be absent or wrong most of the time.
- Forcing an answer makes it worse: haiku with no abstention was unrelated
  89% of the time and invented specifics in 4 cases.

**Do not start the sibling UI issue.** The measurement the issue asked for
now exists, and it says the expensive part is not justified.

**If the idea is revisited,** the only band with signal is short
recitation/continuation and explicit option picks. That band is 0.7% of all
production boundaries and 3.7% of offered-choice turns. A feature limited to
it would be rarely visible, and the suppression rules from the taxonomy
(abstain on novel instructions, never invent paths/URLs, never echo the
assistant's question) are prerequisites, not polish.

**Re-run the harness after prompt changes or corpus shifts.** It is cheap:
two minutes for Stage 0, a few minutes and well under a dollar for a 200-case
score. The dev set, raw scores, and spot-check sheet are local and git-ignored
because they contain private conversation text.
