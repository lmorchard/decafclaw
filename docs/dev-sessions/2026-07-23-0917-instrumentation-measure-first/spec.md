# Spec — Instrumentation first: measure before building

**Session slug:** `instrumentation-measure-first`
**Date:** 2026-07-23
**Issues:** #310 (tool usage telemetry), #409 (reflection cost/effectiveness telemetry), #480 Phase 1 (prompt-cache measurement)

## Thesis

Three of the most-argued subsystems — reflection, the tool registry, and prompt
handling — are being reasoned about from *impression*, not data. The backlog
triage (2026-07-23) surfaced this as the single strongest cross-cutting signal:
speculative follow-ups pile up (the reflection-judge epic #591→#589/#529/#530,
the tool-scoring framework #274 and its satellites, prompt-prefix churn) while
the cheap instrumentation that would tell us whether any of it is worth building
sits unstarted.

This session builds that instrumentation and **changes no behavior**. The
deliverable is data-collection surfaces plus a way to read them. Decisions about
what to do with the data are explicitly out of scope and become follow-up issues
once a week of real numbers exists.

All three are non-LLM-visible plumbing, so per project convention: **unit tests
required, no evals.**

## Shared design decisions

These three issues independently reach for the same shape (an event-bus
subscriber → append-only JSONL sidecar → on-demand report). Decide once, apply
consistently:

- **Storage: append-only JSONL under `workspace/`**, one file per concern
  (`workspace/tool_usage.jsonl`, `workspace/reflection/metrics.jsonl`). Matches
  the "files on disk, human-readable, crash-recoverable" convention. No SQLite in
  this session — add an on-demand index later only if query ergonomics demand it.
- **Config: a new `TelemetryConfig` dataclass** (in `config_types.py`) grouping
  the enables/paths, resolved through the normal defaults→config.json→env chain.
  Fields: `tool_usage_enabled`, `tool_usage_path`, `reflection_metrics_enabled`,
  `reflection_metrics_path`. (Cache measurement rides existing diagnostics; no new
  file — see #480 below.)
- **Producers are EventBus subscribers, fail-open.** A telemetry write that
  raises must never break a turn — `except Exception as exc: log.debug(...)`,
  never bare `except: pass`.
- **No privacy-sensitive payloads.** Tool args/returns, reflection response
  bodies, and prompt contents are NOT logged — only metadata (names, sizes,
  counts, token totals, fingerprints).
- **Reporting: `make` targets** producing ranked markdown/text to stdout, plus
  (where cheap) a read-only `/api/*` endpoint the web UI can hang a view off
  later. No new UI in this session beyond what already exists.

## Scope by issue

### #310 — Tool usage telemetry (M)

Subscribe to `tool_start` / `tool_end` (published in `agent.py`) and persist one
record per call: `timestamp, conversation_id, tool_name, source (core/skill/mcp
server), outcome (success/error/end_turn), duration_ms, input_bytes,
output_bytes`. Optionally record failed tool *lookups* (model called a tool we
don't expose) to catch deferred-catalog gaps.

- New `src/decafclaw/tool_telemetry.py` — subscriber + JSONL writer.
- `make tool-usage-report` — ranked report: calls/tool, unique convs/tool, error
  rate, last-called. Flags unused / low-usage tools as consolidation candidates.
- Feeds the parked tool-audit work (#307) and gives #303/#526 real-world
  ambiguity ground truth.

### #409 — Reflection telemetry (M)

Record per turn where reflection runs: `outcome bucket
(passed_first / passed_after_retry / loop_exhausted / errored / skipped+reason)`,
`retry_count`, `judge token cost` (prompt+completion, summed across rounds),
`response delta on retry` (char-length change + token-overlap ratio between first
and final response), `critique fingerprint` (first ~120 chars, to spot repeating
rejection patterns).

- New `src/decafclaw/reflection_metrics.py` — rolling JSONL writer + simple
  aggregation.
- Emit from `reflection.py` (structured result) + `agent.py` (per-turn outcome
  and token cost from `ctx.tokens`).
- `make reflection-stats` (and/or `/api/reflection/stats`) — last-N-hours
  pass-rate, mean retries, mean tokens, loop-exhausted rate.
- Answers the four questions in #409: pass-first fraction (pure overhead),
  loop-exhausted fraction (waste + bad UX), whether successful retries meaningfully
  differ (genuine value), token cost per active hour.

### #480 Phase 1 only — Prompt-cache measurement (S)

Parse and surface cached-token counts already returned by providers but
currently dropped:

- Vertex: pull `usageMetadata.cachedContentTokenCount` into the usage dict
  (`vertex.py` `_VertexStreamState.usage`, ~262-266).
- OpenAI-compat: pull `prompt_tokens_details.cached_tokens`
  (`openai_compat.py`).
- Surface on `TokenUsage` and in the per-conversation diagnostics sidecar
  (`workspace/conversations/{conv_id}/context.json`) so the existing UI popover
  can show cache hit rate. Debug log line per turn: cached / total prompt tokens.

**Phases 2 (reorder volatile prompt injections) and 3 (Anthropic `cache_control`
breakpoints) are explicitly out of scope** — they only happen if Phase 1 data
shows the cache is being shredded by our prefix churn (skill loading, dynamic
tool sets, `context_cleanup` stubbing, compaction rewrites).

## Out of scope (this session)

- Any behavior change driven by the data (gating/disabling reflection, tool
  consolidation, prompt reordering, cache-control hints). Each becomes a
  follow-up issue once data exists.
- The reflection judge prompt itself (#591 epic territory).
- SQLite storage / long-window query tooling.
- New web UI beyond reusing the existing diagnostics popover for cache stats.

## Validation

- **#310:** run a session exercising several tools; confirm records land in
  `tool_usage.jsonl` and `make tool-usage-report` ranks them; confirm a
  never-called tool shows as unused. Unit test the subscriber + report aggregation.
- **#409:** unit-test each outcome bucket (pass-first, passed-after-retry,
  loop-exhausted, errored, skipped-with-reason) with the delta/overlap
  computation; confirm fail-open on a writer error.
- **#480:** open a long conversation, confirm cached-token counts appear in the
  diagnostics sidecar and rise monotonically as the prefix stabilizes. Unit-test
  the usage-parsing for both providers with fixture responses.

## Sequencing

Independent enough to parallelize, but a sensible order:

1. **#480 Phase 1** (S) — smallest, touches only provider usage parsing +
   existing diagnostics; no new subsystem.
2. **#310** (M) — establishes the `tool_telemetry.py` subscriber + `TelemetryConfig`
   + report-target pattern that #409 mirrors.
3. **#409** (M) — reuses the pattern; slightly more logic in the delta/overlap +
   token accounting.

## Docs to update (same PRs)

- `docs/reflection.md` — reflection metrics + how to read them (#409).
- New tool-telemetry doc or a section in `docs/tools.md` (#310).
- `docs/context-composer.md` — cache-token surfacing in diagnostics (#480).
- `docs/config.md` — new `TelemetryConfig` group.
- CLAUDE.md key-files list if new modules land.

## Success criterion

After this session ships and runs for ~a week of normal use, we can answer with
numbers — not impressions — whether reflection earns its keep, which tools are
load-bearing, and whether prompt-prefix churn is quietly defeating provider
caches. Those answers drive the next round of issues.
