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

```bash
# Stage 0: free signals + page calibration over the full archive.
uv run python -m decafclaw.survey stage0

# Deterministic dev set (stratified by conversation length and recency).
uv run python -m decafclaw.survey devset --limit 200

# Stage 2: score aux and active arms on the dev set.
uv run python -m decafclaw.survey score --devset evals/suggested_prompt/devset.jsonl \
  --arms aux,active --aux-model vertex-gemini-flash

# Bound the spend before spending it.
uv run python -m decafclaw.survey score --devset evals/suggested_prompt/devset.jsonl --dry-run
```

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

The dev set itself is checked in at `evals/suggested_prompt/devset.jsonl` and
regenerated by the `devset` command.

## Limitations

- The local dev corpus is not the production corpus. Its archives predate
  provenance persistence, so Stage 0's real-provenance path is not exercised
  locally; the `retrieval_proxy` label makes that explicit.
- The vault index reflects *current* page content, not the content at archive
  time. Old turns are scored against a newer vault.
- The page proxy measures where a suggestion points, not whether the action is
  right. A prompt can be correct and point nowhere.
- The operator spot check is a human judgment step. An agent-generated review
  is recorded as such, never presented as a human one.

## Results

See the findings section below (filled after a survey run) for the measured
ceiling, aux-vs-active delta, failure taxonomy, and go/no-go recommendation.
