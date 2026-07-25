# Spec: behavioral eval suites + per-turn context diagnostics (#528 + #531)

## Summary

One combined arc closing two related eval-harness issues:

- **#528** — a new cross-cutting eval axis: behavioral suites grouped by *failure mode*
  (retrieval / routing / answer quality / workflow discipline) rather than by tool/feature,
  plus axis-tag metadata and an aggregate pass-rate-by-axis scorecard.
- **#531** — surface per-turn context diagnostics in eval result bundles so a failing eval
  can be diagnosed (retrieval-miss vs routing-miss vs answer-miss vs context-bloat) with
  evidence instead of re-running under `LOG_LEVEL=DEBUG`.

They combine because #531's "aggregate report groups by axis tag" acceptance item depends on
#528's axis tagging, and the diagnostics block is what turns an axis label into an actionable
diagnosis.

## Motivation

- Per-tool eval coverage answers "does the tool work." Behavioral suites answer "does the agent
  make good *decisions* about when/how to use tools." Different failure modes, different fixes.
- When an eval fails today, there's no evidence in the bundle to distinguish a retrieval miss
  (right file never retrieved), a routing miss (retrieved but not read), an answer miss (read but
  answered wrong), or context bloat (over budget on schemas/retrieved context). You re-run with
  debug logging and guess. #531 puts that evidence in the bundle.
- The diagnostics reuse the existing context-sidecar infrastructure (`context.json` written on
  every turn-exit) — no recomputation, no duplication.

## Part A — #528: axis tagging + behavioral suites

### A1. Axis-tag metadata

New **top-level** key `tests:` on each eval case (sibling of `name` / `input` / `expect`, **not**
under `setup:` — it's a classification, not a config override). Accepts a single string or a list
of strings drawn from a canonical set:

```
retrieval | routing | answer_quality | workflow_discipline
```

- A case may declare more than one axis (list form) when it genuinely exercises multiple.
- **Unknown axis → hard error** at load time, consistent with #663's strict-key stance
  (`setup.*` unknown keys now raise rather than silently no-op). A typo'd axis must not
  silently vanish from the scorecard.
- **Untagged cases** are allowed — they fall into an `untagged` bucket in the aggregate. This
  means the existing suites (`vault.yaml`, `conversation.yaml`, …) do **not** all need retagging
  in this PR; only the new behavioral suites are tagged. Retagging the rest is a follow-up.

### A2. Seven behavioral suites

Flat `evals/*.yaml` (matches today's layout; no loader change). Each suite is small, adversarial,
with distractors and similar-but-wrong filenames so a lazy/greedy strategy fails.

| Suite file | Axis | What it probes |
|---|---|---|
| `vault_answering.yaml` | `retrieval` | find the right page, avoid the similar-but-wrong page, admit absence when the page doesn't exist |
| `tool_routing.yaml` | `routing` | vault vs workspace vs conversation_search picked correctly for the question shape |
| `source_grounding.yaml` | `answer_quality` | answers cite files or hedge; no fabricated content |
| `context_pressure.yaml` | `answer_quality` | still answers correctly after noisy history + large tool output |
| `clarification.yaml` | `workflow_discipline` | ask one useful question only when ambiguity actually matters; otherwise proceed |
| `abort_recovery.yaml` | `workflow_discipline` | no stale-intent resurrection after cancel/error (unblocked — #517 is Done) |
| `over_ceremony.yaml` | `workflow_discipline` | a simple ask doesn't trigger checklist / project escalation |

**Target: ~30 cases** (low end of #528's 30–50 acceptance), roughly 4–5 per suite. Each suite is
authored and validated with a **real model run** as its own plan phase. The count is a knob: if
validation cost/time bites, individual suites can ship leaner (min 2/suite) and grow later — but
the arc aims to satisfy #528's acceptance floor.

Assertion discipline (per CLAUDE.md eval conventions):
- Every case bounded by `max_tool_calls` and `max_tool_errors`.
- `expect_no_tool` / tight `max_tool_calls` cases set
  `setup.config_overrides: {reflection.enabled: false}` (reflection's judge can invoke unexpected
  tools on retries).
- Prefer `expect_tool` / `expect_no_tool` / `expect_tool_count_by_name` +
  `response_contains(_all)` over loose text matching.

### A3. Aggregate pass-rate-by-axis

`run_eval` computes and attaches to `results["summary"]`:

```json
"by_axis": {
  "retrieval":            {"total": 5, "passed": 4, "failed": 1, "pass_rate": 0.8},
  "routing":              {"total": 5, "passed": 5, "failed": 0, "pass_rate": 1.0},
  "answer_quality":       {"total": 9, "passed": 7, "failed": 2, "pass_rate": 0.78},
  "workflow_discipline":  {"total": 11,"passed": 9, "failed": 2, "pass_rate": 0.82},
  "untagged":             {"total": 40,"passed": 38,"failed": 2, "pass_rate": 0.95}
}
```

- A multi-axis case counts once toward each of its axes (totals across axes may exceed the test
  count — documented).
- `make eval` prints a per-axis scorecard table at the end of the run, after the summary line.
- **Deferred:** per-axis trend in `evals/history.jsonl` (the by-axis aggregate is in-bundle only
  for now) — a #351-style follow-up.

## Part B — #531: per-turn context diagnostics

### B1. Diagnostics block

A `diagnostics` dict attached to **each turn entry** in `all_responses` **and** to the
**top-level result** (for single-turn tests the top-level block is that turn; for multi-turn it's
the last turn). Built by a new helper that merges two sources:

**From the context sidecar** — read via `read_context_sidecar(config, ctx.conv_id)` immediately
after each `run_agent_turn` (the sidecar is written on turn-exit by `agent.py:_write_diagnostics`
→ `composer.build_diagnostics()`). Zero recomputation:

- `tokens_by_section` — from `sidecar["sources"]`: `{source: tokens_estimated}` per section
  (system / history / tools / retrieved-context / …).
- `active_tools` / `deferred_tools` — from the `"tools"` source entry
  (`items_included` = active, `items_truncated` = deferred).
- `retrieved_candidates` — from `sidecar["memory_candidates"]`: file_path + composite/similarity/
  recency/importance scores (names + scores, as the issue asks).
- `total_tokens_estimated`, `total_tokens_actual`, `context_window_size`, `compaction_threshold`
  — passed through from the sidecar.

**Derived from the turn's history slice** (the runner already collects it via
`_collect_tool_calls` on `history[pre_turn_history_len:]`):

- `tool_calls` — list of `{name}` in call order + `count`. **No per-tool-call durations** — they
  are not in the archive; capturing them would need a `tool_status` event subscriber. Deferred
  (documented in the block as absent, and in the spec below).
- `files_read` — file paths from read-shaped tool calls this turn (`vault_read`, workspace read
  tools). Derived from parsed tool-call args.
- `files_cited` — heuristic: the union of (`files_read` ∪ `retrieved_candidates` paths) whose
  basename or path appears as a substring in the final response text, **plus** any `[[PageName]]`
  wiki-mentions extracted from the response. Documented as heuristic.

The four diagnosis modes from #531 fall out of these fields:
- **Retrieval miss** — right file absent from `retrieved_candidates`.
- **Routing miss** — right file in `retrieved_candidates` but absent from `files_read`.
- **Answer miss** — right file in `files_read` but assertion still failed.
- **Context bloat** — `tokens_by_section` over budget on tools / retrieved-context.

### B2. `--verbose` console output

Under each test in `--verbose` mode, print a compact diagnostics summary: token split by section,
top-N retrieved candidates with composite scores, files read, files cited, and tool-call names.
Keeps the common diagnosis loop out of the JSON.

## Structure and boundaries

New module **`src/decafclaw/eval/diagnostics.py`** — pure, LLM-free functions:

- `build_turn_diagnostics(sidecar: dict | None, turn_slice: list[dict], response: str) -> dict`
  — merges sidecar + derived fields into the block. Sidecar `None` (missing file) → degrade to the
  derived-only fields; never raise.
- `detect_files_read(tool_calls: list[tuple[str, dict]]) -> list[str]`
- `detect_files_cited(response: str, known_paths: list[str]) -> list[str]` — substring + `[[wiki]]`.
- `aggregate_by_axis(test_results: list[dict], cases: list[dict]) -> dict` — pass-rate per axis.
- `parse_axes(case: dict) -> list[str]` — validate `tests:` against the canonical set; raise on
  unknown.

`runner.py` calls these; it does **not** grow more inline logic than the wiring. This keeps the
new behavior testable without a live model and without a full eval run.

## Testing

- **Unit tests (`tests/`)** — deterministic, no LLM. Cover: `parse_axes` (string form, list form,
  unknown → raises); `detect_files_read`; `detect_files_cited` (path substring hit, wiki-mention,
  no false-positive on unrelated text); `build_turn_diagnostics` (full sidecar, `None` sidecar
  degrade path, tool_calls + files merge); `aggregate_by_axis` (single-axis, multi-axis
  double-count, untagged bucket, pass-rate arithmetic). Find and extend the existing eval-harness
  test file(s) (e.g. `tests/test_eval_setup_overrides.py`).
- **Behavioral suites (LLM-visible)** — validated by real runs (`make eval evals/<suite>.yaml`),
  per suite, during their authoring phase. Not unit-tested (they exercise model behavior).
- No new `tool_choice` cases required unless a tool description changes (this arc doesn't sharpen
  any). Run `make check` + `make test` green before each commit.

## Docs (same PR)

- `docs/eval-loop.md` — axis-tag metadata (`tests:` key + canonical set), the seven suites, the
  `by_axis` aggregate + scorecard output, the per-turn `diagnostics` block shape, `--verbose`
  additions.
- `docs/context-composer.md` — only if the sidecar/diagnostics contract changes (it does not;
  we consume the existing shape read-only). Likely a one-line cross-reference from eval-loop.
- Dev-session docs (`spec.md`, `plan.md`, `notes.md`) committed with the PR.

## Explicitly deferred (with a trail)

- **Per-tool-call durations** — not in the archive; would need a `tool_status` subscriber on the
  eval bus. Diagnostics block carries names + count + the per-turn wall-clock we already have.
- **Per-axis history trend** — `by_axis` is in-bundle only; extending `evals/history.jsonl` with
  per-axis pass rates is a #351-style follow-up.
- **Retagging existing suites** with axis tags — only the new behavioral suites are tagged now;
  existing ones bucket as `untagged`.

## Acceptance (combined)

- [ ] `tests:` axis metadata parsed + validated (unknown → error); untagged allowed.
- [ ] Seven behavioral suites exist, ~30 cases total, each bounded and passing on a real run.
- [ ] `results["summary"]["by_axis"]` present; `make eval` prints a per-axis scorecard.
- [ ] Each eval result (per-turn + top-level) carries a `diagnostics` block sourced from the
      context sidecar + derived file/tool fields; missing sidecar degrades gracefully.
- [ ] `--verbose` prints the compact diagnostics summary.
- [ ] Diagnostics reuse the sidecar code; no duplication of `build_diagnostics`.
- [ ] Unit tests cover the deterministic helpers; `make check` + `make test` green.
- [ ] `docs/eval-loop.md` updated in the same PR.
