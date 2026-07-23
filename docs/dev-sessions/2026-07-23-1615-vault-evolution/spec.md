# Spec: Self-improving vault — frontmatter generation & importance tuning (#197)

## Problem

Vault pages carry optional YAML frontmatter (`summary`, `keywords`, `tags`,
`importance`) that feeds two retrieval mechanisms:

- **Composite scoring** — `composite = 0.5·similarity + 0.3·recency + 0.2·importance`
  (`RelevanceConfig`, consumed in `context_composer._score_candidates`).
- **Composite embeddings** — `frontmatter.build_composite_text` prepends
  `summary`/`keywords`/`tags` to the body before embedding.

Both are fully wired. The problem: **nothing ever populates the frontmatter.**
Dream and garden write bare markdown bodies, so:

- Every page scores a constant `importance = 0.5` (the default). The 0.2
  importance weight contributes a flat 0.1 to every candidate and does nothing
  to ranking — it is dead weight.
- The composite embedding is almost always just the bare body; the metadata
  channel is unused.

This issue makes the vault self-improving: dream generates frontmatter on
create/revise, garden tunes `importance` from real signals, and we measure
whether retrieval actually gets better.

## Current-state map (verified against the tree)

**Already implemented — do not rebuild:**

- `frontmatter.py` — `parse_frontmatter`, `serialize_frontmatter` (round-trip
  writer exists), `get_frontmatter_field` (typed coercion; `importance`
  clamped `[0,1]`, default `0.5`), `build_composite_text`.
- Importance in scoring — `context_composer._score_candidates`; enrichment in
  `memory_context._enrich_results` (only for `source_type in {page, user}`,
  not journal). Default `0.5` in three places.
- Composite embeddings — `embeddings._iter_vault_pages` already calls
  `build_composite_text`. Reindex path: `reindex_vault` / `make reindex`.
- One-hop outbound wiki-link graph expansion — `memory_context._expand_graph_links`.
- Per-turn `memory_candidates` diagnostics (injected candidates only) in the
  `context.json` sidecar — `context_composer.build_diagnostics`.
- On-demand `vault_backlinks` tool — brute-force `rglob` scan, no index.

**Genuinely missing — the #197 work:**

- Generation of `summary`/`keywords`/`tags`/`importance` (dream/garden write
  bare bodies).
- Tuning of `importance` (nothing sets it; term is effectively constant).
- Per-page retrieval-frequency telemetry and any record of *dropped*
  candidates — the blockers for measuring improvement.
- A persistent inbound-link/backlink index (only brute-force scanning exists).

## Goals

1. Dream generates frontmatter on page create/revise, respecting manual values.
2. Garden recomputes `importance` from real signals on a defined, testable formula.
3. One-time backfill of existing pages + reindex.
4. Persistent backlink index (feeds garden; replaces brute-force scan).
5. We can **measure** whether retrieval quality improved — proxy telemetry +
   an LLM-judge eval.

**Non-goals (this arc):**

- Micro-evolution on journal append (deferred → follow-up issue).
- Retrieval-time backlink boosting (stretch; not required for the arc).
- Changing the composite-score weights themselves.

## Design decisions (approved)

- **D1 — `vault_update_frontmatter` tool, not pure-prompt YAML.** A structured
  write primitive in `skills/vault/tools.py` (always-loaded) guarantees valid
  YAML and enforces the respect-manual-values invariant in code. Reused by
  dream, garden, and the backfill CLI. Chosen over having dream write raw YAML
  into page content (fragile; would re-implement the merge rule in prose).
- **D2 — deterministic `importance` formula, not LLM judgment.** Garden's
  importance tuning is a testable computation over telemetry + backlinks +
  reference frequency, so results are reproducible and measurable. The LLM only
  reviews outliers. Dream's *initial* importance is necessarily LLM judgment
  (no telemetry exists for a brand-new page); garden's formula corrects it over
  time.

## Measurement approach (approved: telemetry proxy + LLM-judge eval)

- **Proxy telemetry** (Phase 0): a retrieval-event stream records, per turn,
  every scored candidate + its include/drop verdict. `make retrieval-report`
  aggregates per-page retrieval frequency, include/drop ratio, importance
  distribution/variance, frontmatter coverage %, and orphan count. This is both
  the proxy metric and the retrieval-frequency *input* garden's formula needs —
  so the measurement infra is a feature prerequisite, not overhead.
- **LLM-judge eval** (`evals/retrieval-quality.yaml`): over a fixture vault with
  frontmatter, a judge rates whether the retrieved context is relevant and
  sufficient for representative queries. Run before Phase 2 and after Phase 5
  for a quality delta.

## Architecture — components & boundaries

### `vault_update_frontmatter(ctx, page, fields, overwrite=False)` (Phase 1)

- Home: `skills/vault/tools.py`.
- Reads the page, `parse_frontmatter`, merges `fields`:
  - `overwrite=False` → only set keys currently absent/empty (dream).
  - `overwrite=True` → set/replace given keys (garden importance).
- `serialize_frontmatter` back, write, reindex the page (composite embedding
  refresh). Returns a summary of what changed.
- Field-aware validation: `importance` clamped `[0,1]`; `keywords`/`tags`
  coerced to lists; `summary` to string.

### Retrieval telemetry (Phase 0)

- Composer emits a `retrieval_event` over the EventBus from the scoring path,
  carrying **all** scored candidates (pre-trim) each tagged included/dropped
  with its similarity/recency/importance/composite. Requires exposing the
  pre-trim candidate list (today only the injected set is recorded).
- Subscriber (`retrieval_telemetry.py`, mirroring `tool_telemetry.py`) appends
  to `workspace/telemetry/retrieval.jsonl`. Fail-open.
- `make retrieval-report` reads the JSONL + the vault to print the aggregates.

### Backlink index (Phase 4)

- `backlinks.py` — persistent inbound-link map on disk (JSON, human-readable,
  rebuildable), updated on `vault_changed` events + full rebuild path.
- `vault_backlinks` reads the index instead of `rglob`-scanning.

### Garden importance formula (Phase 5)

- `vault_recompute_importance` (deterministic): for each page,
  `importance = wf·norm(retrieval_freq) + wl·norm(inbound_links) [+ wr·norm(reference_freq)]`,
  clamped `[0,1]`, weights in config. Writes via `vault_update_frontmatter(overwrite=True)`.
  - **Core v1 signals: retrieval frequency (Phase 0) + inbound-link count
    (Phase 4)** — both have clean, cheap sources.
  - **Reference frequency is optional/stretch.** There is no clean per-page
    conversation-reference signal today; the closest is `@[[Page]]` mention
    injections (`vault_references`), which aren't logged per page. Ship v1 with
    the two core signals (`wr = 0`); add reference-frequency only if a cheap
    signal materializes (e.g. logging `vault_references` into the Phase 0
    stream). Do not build conversation-scanning for it in this arc.
- Garden SKILL.md orchestrates: recompute → review outliers → validate
  frontmatter consistency → flag orphans / weak-links.

## Phases (one commit each; commit-per-phase on `197-vault-evolution`)

- **Phase 0** — retrieval telemetry stream + `make retrieval-report`.
- **Phase 1** — `vault_update_frontmatter` tool (+ tests).
- **Phase 2** — dream frontmatter generation (SKILL.md) + eval.
- **Phase 3** — `make backfill-frontmatter` CLI + reindex.
- **Phase 4** — persistent backlink index; rewire `vault_backlinks`.
- **Phase 5** — deterministic `vault_recompute_importance` + garden SKILL.md.
- **Measurement** — `evals/retrieval-quality.yaml`; capture before/after delta.

## Testing

- Unit: `vault_update_frontmatter` (merge, respect-manual, overwrite, clamp,
  reindex-called); telemetry subscriber (include/drop capture, fail-open);
  backlink index (build, incremental update, rebuild); importance formula
  (normalization, weights, clamp).
- Eval: dream generation produces valid frontmatter (tool_choice / behavior
  case); `retrieval-quality.yaml` judge before/after.
- `make check` + `make test` green before each phase commit.

## Success criteria

- Dream-created pages carry generated frontmatter; manual values preserved.
- `importance` varies across pages (no longer constant 0.5); the 0.2 weight
  measurably affects ranking.
- `make retrieval-report` shows a baseline and a post-arc delta.
- `retrieval-quality.yaml` judge score improves (or holds while the proxy shows
  healthier distribution) after Phase 5 vs before Phase 2.

## Risks / open items

- Long-lived branch drift — mitigated by commit-per-phase + frequent rebase.
- Backfill LLM cost/time over a large vault — batch, log progress, resumable.
- Importance formula weights are a guess until telemetry accrues — ship with
  defaults, tune once real `retrieval.jsonl` data exists (a deploy-and-wait
  step, like the instrumentation-first direction already in flight).
- Deployed agent runs over plain HTTP; telemetry is server-side only, no
  secure-context concerns.
