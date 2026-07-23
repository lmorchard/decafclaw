# Instrumentation-first Implementation Plan

**Goal:** Add data-collection instrumentation for three subsystems (prompt-cache tokens, tool usage, reflection cost/effectiveness) that **changes no behavior** — only observes.

**Approach:** Each concern is an append-only JSONL sidecar under `workspace/`, fed by EventBus enrichment/subscribers, fail-open, metadata-only (no args/returns/prompt bodies). A shared `TelemetryConfig` groups enables/paths through the normal defaults→config.json→env chain. Reporting via `make` targets. Cache tokens ride the existing diagnostics sidecar + popover.

**Tech stack:** Python 3, dataclasses, existing `EventBus` (`events.py`), existing provider usage dicts, existing diagnostics sidecar (`context_composer.py`), pytest.

**Confirmed decisions (2026-07-23):**
- #310: enrich `tool_start`/`tool_end` events with `conv_id` + `duration_ms` (+ `input_bytes`) at the publish site in `tool_execution.py`; subscriber consumes `tool_end` only (no start/end pairing).
- #409: emit a reflection-metrics row **only for judge-eligible turns** (reflection enabled, non-child, produced a final response). Buckets: `passed_first` / `passed_after_retry` / `loop_exhausted` / `errored` / `skipped_empty`. Child-agent and reflection-disabled turns emit nothing.
- `end_turn` dropped from #310 outcome set — not cheaply observable on `tool_end`. Outcomes: `success` / `error` / `cancelled`, inferred from the `result_text` prefix.
- Telemetry defaults **enabled** (this is meant to run ~a week to collect data). No rotation this session — append-only; retention is a follow-up.

---

## Phase 1: #480 Phase 1 — prompt-cache token surfacing

Parse the cached-token counts providers already return but drop, thread them onto `TokenUsage`, surface in the diagnostics sidecar + existing popover, log per turn. Pure enrichment; no control-flow change.

**Files:**
- Modify: `src/decafclaw/llm/providers/vertex.py` — add `cached_tokens` to both usage-parse sites (streaming `_VertexStreamState.process_chunk` ~262-266; non-streaming `_parse_usage` ~558-567) from `usageMetadata.cachedContentTokenCount`.
- Modify: `src/decafclaw/llm/providers/openai_compat.py` — add normalized `cached_tokens` from `usage.prompt_tokens_details.cached_tokens` at both sites (non-streaming ~104-123; streaming `process_chunk` ~294-296).
- Modify: `src/decafclaw/context.py` — `TokenUsage` gains `total_cached_prompt: int = 0`, `last_cached_prompt: int = 0`.
- Modify: `src/decafclaw/agent.py` — at the usage merge chokepoint (~598-608) read `usage.get("cached_tokens", 0)`, accumulate onto `ctx.tokens`, add a `log.debug` "cached=%s / prompt=%s".
- Modify: `src/decafclaw/context_composer.py` — `build_diagnostics` (~1201-1213) adds `cached_prompt_tokens` (from `ComposerState`) and a `cache_hit_rate`; `record_actuals` + `ComposerState` carry the cached figure.
- Modify: `src/decafclaw/web/static/components/context-inspector.js` — one `<dt>Cached</dt><dd>` row next to Actual (~158-163).
- Test: `tests/test_provider_cache_tokens.py` (new) — usage parsing for vertex (stream + non-stream) and openai_compat (stream + non-stream) with fixture responses carrying/omitting cached counts; `tests/test_token_usage.py` or extend existing agent-usage test — accumulation onto `TokenUsage`.
- Docs: `docs/context-composer.md` — cache-token fields in diagnostics.

**Key changes:**
- Providers normalize to a common `cached_tokens` key so the merge point stays provider-agnostic (`usage.get("cached_tokens", 0)`), mirroring the existing `prompt_tokens`/`completion_tokens` contract.

```python
# vertex.py — both sites
"cached_tokens": (chunk_usage or meta).get("cachedContentTokenCount", 0),

# openai_compat.py — both sites, after obtaining `usage`
if usage is not None:
    details = usage.get("prompt_tokens_details") or {}
    usage["cached_tokens"] = details.get("cached_tokens", 0)

# context.py
@dataclass
class TokenUsage:
    total_prompt: int = 0
    total_completion: int = 0
    last_prompt: int = 0
    total_cached_prompt: int = 0
    last_cached_prompt: int = 0
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (new provider-cache + token tests) — 3021 passed
- [x] `make check` passes (incl. `check-js` for the popover edit)

**Verification — manual:**
- [x] Fixture-level: fabricated vertex/openai responses with a cached count parse into `usage["cached_tokens"]`; absent → 0, never KeyError. (unit-tested)
- [ ] Diagnostics sidecar for a real conversation shows `cached_prompt_tokens`; popover renders the Cached row. (deferred to post-merge live test)

---

## Phase 2: #310 — tool usage telemetry + report

Introduce the shared `TelemetryConfig`, enrich tool lifecycle events with the missing fields, add a fail-open subscriber that appends one JSONL record per tool call, and a `make` report that ranks tools and flags unused ones.

**Files:**
- Modify: `src/decafclaw/config_types.py` — new `TelemetryConfig` dataclass (full, both concerns).
- Modify: `src/decafclaw/config.py` — import, top-level `Config.telemetry` field (~186), `load_sub_config(TelemetryConfig, file_data.get("telemetry", {}), "TELEMETRY")` (~395), constructor arg (~536).
- Modify: `src/decafclaw/tool_execution.py` — capture `perf_counter()` before `execute_tool`; in the `finally` `tool_end` publish add `conv_id=call_ctx.conv_id`, `duration_ms`, `input_bytes=len(json-serialized fn_args)`. Also add `conv_id` to the `tool_start` publish for symmetry.
- Create: `src/decafclaw/tool_telemetry.py` — `classify_source(name, config) -> (source, detail)`; `make_tool_telemetry_subscriber(config)` factory returning `async def handle(event)`; a JSONL append writer (fail-open); a `build_report(config)` aggregator; `main()` for `python -m`.
- Modify: `src/decafclaw/runner.py` — subscribe the telemetry handler on `app_ctx.event_bus` near `init_notification_channels` (~87-95), guarded by `config.telemetry.tool_usage_enabled`.
- Modify: `Makefile` — `tool-usage-report` target → `python -m decafclaw.tool_telemetry`.
- Test: `tests/test_tool_telemetry.py` (new) — subscriber writes correct record from a `tool_end` event; `classify_source` for core/skill/mcp; outcome inference (`success`/`error`/`cancelled` from prefix); report aggregation (calls/tool, unique convs/tool, error rate, last-called); unused-tool detection; fail-open when the path is unwritable.
- Docs: `docs/tools.md` — new "Tool usage telemetry" section (record shape, report, privacy stance).

**Key changes:**

```python
# config_types.py
@dataclass
class TelemetryConfig:
    """Instrumentation sidecars (#310 tool usage, #409 reflection metrics).
    Append-only JSONL under workspace/, metadata only — no tool args/returns,
    reflection bodies, or prompt contents. Fail-open producers."""
    tool_usage_enabled: bool = True
    tool_usage_path: str = "tool_usage.jsonl"            # workspace-relative
    reflection_metrics_enabled: bool = True
    reflection_metrics_path: str = "reflection/metrics.jsonl"  # workspace-relative

# tool_telemetry record (one per tool_end)
{"timestamp", "conv_id", "tool", "source", "source_detail",
 "outcome", "duration_ms", "input_bytes", "output_bytes"}
```

- `classify_source`: `mcp__` prefix → `("mcp", server)` via `split("__",2)[1]`; `name in config.skill_tool_owners` → `("skill", owner)`; else `("core", "")`.
- Outcome: `result_text` startswith `[error` → `error`; `[cancelled` → `cancelled`; else `success`.
- Report enumerates all known tools (core `TOOL_DEFINITIONS` + discovered skill tools) to flag never-called ones; **MCP unused-detection is best-effort** (MCP tools only enumerable when servers are connected) — report notes this caveat.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (new tool-telemetry tests) — 3030 passed
- [x] `make check` passes
- [x] `make tool-usage-report` runs against a fixture/real log and prints a ranked report (smoke: 0 calls, 48 unused core+skill tools listed)

**Verification — manual:**
- [ ] Exercise several tools in a session; records land in `workspace/tool_usage.jsonl` with sane `duration_ms`/`conv_id`; report ranks them; a never-called tool shows as unused. (deferred to post-merge live test)

---

## Phase 3: #409 — reflection cost/effectiveness telemetry + stats

Capture judge token cost (currently discarded), the first-vs-final response pair, and per-turn outcome bucket; emit one `reflection_turn` event per judge-eligible turn; subscriber appends JSONL; `make reflection-stats` aggregates.

**Files:**
- Modify: `src/decafclaw/reflection.py` — `ReflectionResult` gains `prompt_tokens: int = 0`, `completion_tokens: int = 0`; `evaluate_response` reads `response.get("usage")` and populates them (fail-open — absent usage → 0).
- Modify: `src/decafclaw/agent.py` (`TurnRunner`) —
  - new fields: `reflection_first_response: str | None = None`, `reflection_judge_prompt_tokens: int = 0`, `reflection_judge_completion_tokens: int = 0`, `reflection_exhausted: bool = False`.
  - `_reflection_evaluate`: on round 0 capture `reflection_first_response = <content being judged>`; after each result accumulate judge tokens.
  - `_reflection_skip`: when the exhausted branch fires, set `reflection_exhausted = True` **before** `last_reflection` is nulled.
  - at turn completion in `run()`: for judge-eligible turns compute the bucket + delta + overlap + fingerprint and `await ctx.publish("reflection_turn", ...)` exactly once (covers all finalization exits).
- Create: `src/decafclaw/reflection_metrics.py` — `make_reflection_metrics_subscriber(config)` factory; JSONL append writer (fail-open); `response_delta(first, final) -> (char_delta, overlap_ratio)`; `build_stats(config, hours=...)` aggregator; `main()` for `python -m`.
- Modify: `src/decafclaw/runner.py` — subscribe near the tool-telemetry handler, guarded by `config.telemetry.reflection_metrics_enabled`.
- Modify: `Makefile` — `reflection-stats` target → `python -m decafclaw.reflection_metrics`.
- Test: `tests/test_reflection_metrics.py` (new) — each outcome bucket (`passed_first`, `passed_after_retry`, `loop_exhausted`, `errored`, `skipped_empty`); `response_delta` char-delta + overlap (identical → overlap 1.0, disjoint → 0.0); judge-token capture from a usage-bearing judge response; fail-open on writer error; disabled/child turns emit no row; stats aggregation.
- Docs: `docs/reflection.md` — metrics section + how to read the four #409 questions off the stats.

**Key changes:**

```python
# reflection_metrics.py
def response_delta(first: str, final: str) -> tuple[int, float]:
    char_delta = len(final) - len(first)
    a, b = set(first.split()), set(final.split())
    overlap = len(a & b) / len(a | b) if (a or b) else 1.0
    return char_delta, round(overlap, 4)

# reflection_turn event payload
{"outcome", "retry_count", "judge_prompt_tokens", "judge_completion_tokens",
 "char_delta", "overlap_ratio", "critique_fingerprint"}  # fingerprint = critique[:120]
```

- Bucket at emit time: not eligible (disabled/child) or cancelled → no emit. `reflection_first_response is None` → `skipped_empty`. `last_reflection.error` → `errored`. `reflection_exhausted` → `loop_exhausted`. `retry_count == 0 and passed` → `passed_first`. `retry_count > 0 and passed` → `passed_after_retry`.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (new reflection-metrics tests) — 3048 passed
- [x] `make check` passes
- [x] `make reflection-stats` runs against a fixture/real log and prints aggregate stats (smoke: 0 turns)

**Verification — manual:**
- [x] Integration-tested: pass-first, passed-after-retry (real overlap<1.0 delta + summed judge tokens), loop-exhausted (survives last_reflection nulling), and child-agent no-emit — all via real `run_agent_turn`.
- [ ] Live: run turns in the web UI; confirm buckets + judge tokens land in `workspace/reflection/metrics.jsonl`. (deferred to post-merge live test)

---

## Phase 4: docs + config + key-files sweep

Finalize cross-cutting docs the earlier phases pointed at.

**Files:**
- Modify: `docs/config.md` — new `TelemetryConfig` group (fields, defaults, env prefix `TELEMETRY_`).
- Modify: `CLAUDE.md` — key-files list gains `tool_telemetry.py`, `reflection_metrics.py`; note the telemetry sidecars in the data/config section if warranted.
- Modify: `docs/index.md` — link any new doc/section.
- Verify `docs/reflection.md`, `docs/tools.md`, `docs/context-composer.md` edits from earlier phases read coherently together.

**Verification — automated:**
- [x] `make check` passes
- [x] `make config` shows the telemetry group (nested group → appears, all 4 fields resolve)

**Verification — manual:**
- [x] Docs cross-reference each other (tools.md ↔ reflection.md ↔ config.md ↔ context-composer.md); privacy stance stated per doc; no new index entry needed (sections added to existing pages).

---

## Sequencing & commits

One commit per phase (`Phase N: <name>`). Phases are independent enough to build in order 1→2→3→4; #310 (Phase 2) establishes `TelemetryConfig` that #409 (Phase 3) reuses. Non-LLM-visible plumbing → **unit tests required, no evals** (per spec + CLAUDE.md).
