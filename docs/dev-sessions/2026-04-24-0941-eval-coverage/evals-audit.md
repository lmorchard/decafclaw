# Eval coverage audit — 2026-04-24

Point-in-time audit of the existing eval harness and YAML suite, produced by the `eval-coverage` dev session. Driven by [#240](https://github.com/lmorchard/decafclaw/issues/240).

This doc freezes a snapshot of audit findings + the issue split it drives. For evolving guidance on the harness itself, see [../../eval-loop.md](../../eval-loop.md), updated inline on the same branch.

## Executive summary

- **29 existing tests across 6 YAML files.** Full run against the default model: **25/29 pass (86.2%)**.
- **Apparent pass rate is misleading.** `memory-semantic.yaml` (7/7 pass) is silently broken: its `allowed_tools` list references removed tool names (`memory_search`, `memory_recent`, `memory_save`, `think`) but the tests pass anyway because proactive memory retrieval injects seeded memories directly into context. The file claims to test semantic search; it is not.
- **4 runtime failures** — all category "real behavior issue" (not bit-rot). 2 in memory.yaml (agent doesn't reach for memory on under-specified prompts) + 2 in project-skill.yaml (project-skill step-parsing regressions). None indicate harness bugs.
- **Doc rot fixed inline** on this branch: `docs/eval-loop.md` had stale field names and documented an `expect_tool` assertion that doesn't exist in the runner.
- **Two real harness bugs fixed inline**: `reflect.py` multi-turn input extraction (broken for every multi-turn failure reflection) + added a `make eval` target.
- **Coverage gaps are large.** 15-bullet scope in #240 vs. existing 6 files = ~10 new eval files needed.
- **Harness gaps are real and several are load-bearing.** The most important: no `expect_tool` assertion (runner claims but doesn't implement), no multi-model matrix, no pass-rate trend tracking, no post-turn workspace state assertion.

This audit drives a split of #240 into **16 per-system issues** (filed as part of Step 10) + **1 standalone bug** surfaced by the run + **9 deferred issues** that are either P3 or blocked on harness work (noted below for future filing).

## 1. Harness capabilities

Current runner (`src/decafclaw/eval/runner.py`, `__main__.py`):

**Schema:**
- Single turn: `{name, input, expect, setup?, allowed_tools?}`
- Multi turn: `{name, turns: [{input, expect}], setup?, allowed_tools?}`
- `setup` fields: `skills` (pre-activate), `memories` (journal seed + semantic index), `workspace_files` (sandboxed path writes), `embeddings_fixture` (copy-in), `auto_confirm` (default true).
- `expect` fields: `response_contains` (str/list/`re:`; **OR semantics** on list), `response_not_contains` (str/list; AND semantics), `max_tool_calls`, `max_tool_errors`.
- `allowed_tools`: list of tool names; unlisted calls return an error.

**Execution:**
- Per-test isolation via `tempfile.TemporaryDirectory()` as `config.agent.data_home`.
- User-invokable commands (`/foo`, `!foo`) dispatched before `run_agent_turn` — works in evals.
- Concurrency default 4 (asyncio semaphore). Override via `--concurrency`.
- Failure reflection via a judge model; results saved to `evals/results/{timestamp}-{model}/reflections/{slug}.md`.

**What the harness does NOT support today:**
- `expect_tool` / `expect_no_tool` / `expect_tool_count_by_name` / `expect_tool_args` — tool-name-level assertions. (Docs claimed, runner lacked.)
- `response_contains_all` — no AND semantics on list-form response assertions.
- Multi-model matrix — `--model` takes one; no combined pass-rate report across models.
- Pass-rate trend tracking — no aggregation across runs.
- Post-turn workspace state assertion — can't assert "file X exists with content Y after the turn".
- Conversation history seeding — can't preload an archive for `conversation_search` / `conversation_compact` tests.
- Scheduled / heartbeat mode simulation.
- Cancel / stop mid-turn probe.
- Effort-level switching probe.
- Context-budget / deferred-tool-fetch probe.
- Claude Code sandbox / mock.

## 2. Per-file verdict

Merged from static review and the runtime pass/fail pass (`evals/results/2026-04-24-1015-default/`).

| File | Tests | Pass | Fail | Real issues |
|------|-------|------|------|-------------|
| `ingest.yaml` | 1 | 1 | 0 | None |
| `memory.yaml` | 8 | 6 | 2 | Unbounded tests; OR-vs-AND assertion quality; 2 real behavior failures |
| `memory-multi-turn.yaml` | 4 | 4 | 0 | No `max_tool_calls` bounds anywhere; OR-vs-AND on several tests |
| `memory-semantic.yaml` | 7 | 7 | 0 | **Silently broken** — `allowed_tools` uses removed tool names; passes because proactive retrieval bypasses the intended path |
| `postmortem.yaml` | 1 | 1 | 0 | None |
| `project-skill.yaml` | 8 | 6 | 2 | 2 real failures due to project-skill tool-dispatch / step-parsing regressions |

### Observations by file

**`ingest.yaml`** — Clean. Only one test (workspace-file path). URL/attachment paths explicitly left out per a file comment. Could grow.

**`memory.yaml`** — Tool references are all current. Assertion-quality issues: list-form `response_contains` is OR not AND (5 tests potentially laxer than their names suggest). Test #7 is named "uses think tool for complex question" — `think` doesn't exist; name is stale. Missing bounds on several tests.

**`memory-multi-turn.yaml`** — Clean tool references. No `max_tool_calls` bounds on any test — a runaway agent loop would not fail these.

**`memory-semantic.yaml`** — The big finding. All 7 tests pass but they test nothing. The `allowed_tools` allowlist references `memory_search`/`memory_recent`/`memory_save`/`think` — tools that don't exist in the current codebase. The tests pass because the agent doesn't call any of those; it just echoes seeded memories that proactive retrieval injects into context. This is worse than failing outright — it's silent coverage loss.

**`postmortem.yaml`** — Clean. Single-turn test using `/postmortem` command dispatch. Regex assertion matches the SKILL.md-defined five-section structure.

**`project-skill.yaml`** — Well-designed (gold standard after #17's iteration). Two runtime failures surface real project-skill tool-dispatch issues (not eval bugs): a self-contradicting "did you mean" error message (`unknown tool 'foo'. Did you mean: foo`) and brittle step-parsing that doesn't tolerate some model output formats.

## 3. Coverage gaps (new eval files needed)

Per the spec's "one eval file ≈ one issue ≈ one PR" model. Derived from #240's 15-bullet scope list:

| Proposed file | Covers | Size | Harness deps | Priority |
|---------------|--------|------|--------------|----------|
| `vault.yaml` | All 11 vault tools (read/write/search/journal/backlinks/list/delete/rename/section tools). Also rewrites/replaces `memory-semantic.yaml`. | M | None | **P1** |
| `workspace-tools.yaml` | 12 workspace tools (read/write/edit/search/glob/list/move/delete/diff/insert/replace_lines/append) | M | Post-turn workspace assertion helps | **P1** |
| `shell.yaml` | shell, shell_patterns, shell_background_* (4) | M | None (uses `auto_confirm`) | P2 |
| `conversation.yaml` | conversation_search, conversation_compact | M | **Conversation history seed (harness)** | P2 |
| `delegate.yaml` | delegate_task | S | None | P2 |
| `tool-deferral.yaml` | tool_search + deferred loading + context budget | S | **`expect_tool` assertion (harness)** | P2 |
| `checklist.yaml` | checklist_* (4 always-loaded tools; replaces deprecated todo tools per #234) | S | None | P2 |
| `commands.yaml` | `/command` / `!command` dispatch layer | S | None | P2 |
| `health.yaml` | `!health` + `health_status` tool | XS | None | P2 |
| (tighten) `memory.yaml` + `memory-multi-turn.yaml` | Add `max_tool_calls` bounds; rename / regex-ify OR-vs-AND tests; drop stale "think tool" name | S | None | P2 |
| `consolidation.yaml` (dream + garden) | dream, garden consolidation skills | L | **Scheduled mode sim (harness)** | P3 (blocked) |
| `claude-code.yaml` | claude_code_* (7) | L | **Subprocess sandbox (harness)** | P3 (blocked) |
| `effort-switching.yaml` | Effort level switching behavior | L | **Effort probe (harness)** | P3 (blocked) |
| `cancel.yaml` | Stop / cancel mid-turn | L | **Cancel probe (harness)** | P3 (blocked) |

See [notes.md](./notes.md) for minimal-viable test sets per file.

## 4. Harness gaps

| ID | Gap | Size | Priority |
|----|-----|------|----------|
| H1+H2 | `expect_tool` / `expect_no_tool` / `expect_tool_count_by_name` assertions. Load-bearing — docs already claim, many future evals need. | S | **P1** |
| H4 | Multi-model matrix runner (single invocation → combined report across configured models). | M | P2 |
| H5 | Pass-rate trend tracking (per-run summary committed to git). | S | P2 |
| H6 | Post-turn workspace state assertion (`expect.workspace_files: {path: content}`). | S | P2 |
| H7 | Conversation archive seeding in `setup` (for conversation_search / compact evals). | S | P2 |
| H14+H16 | Misc harness quality — judge prompt should interpolate non-`response_contains` assertions; add `response_contains_all` for AND semantics on lists. | XS | P2 |
| H8 | Scheduled / heartbeat mode simulation (for dream/garden evals). | M | P3 (deferred) |
| H9 | Cancel probe (simulated mid-turn stop). | L | P3 (deferred) |
| H10 | Effort-level switching probe. | M | P3 (deferred) |
| H11 | Claude Code sandbox / mock (safe subprocess testing). | L | P3 (deferred) |
| H12 | Context-budget / deferred-tool probe. Overlaps with H1; may ride on `expect_tool_fetch_history`. | S | P2 |
| H3 | `expect_tool_args` assertion. Extension of H1; brittle, defer. | M | P3 (deferred) |

Already inline-fixed on this branch: **H13** (reflect.py multi-turn input extraction bug), **H15** (`make eval` Makefile target), and all of `docs/eval-loop.md` field-name rot.

## 5. Prioritized issue list (filed)

**16 issues filed** as part of Step 10 of this session, plus 1 standalone bug caught by the audit. Subsequent triage closed 3 as redundant with existing unit-test coverage and narrowed 4 more to LLM-behavior-only scope — see notes column.

### Eval-file issues (7 open + 3 closed)

| # | Title | Pri | Size | Notes |
|---|-------|-----|------|-------|
| #339 | Eval coverage: vault skill (replaces broken memory-semantic.yaml) | P1 | M | **Narrowed**: tool implementation already covered by 51+ unit tests; eval scope is now LLM tool-choice + fixing the silently-broken `memory-semantic.yaml`. |
| #340 | Eval coverage: workspace tools (tool-selection only) | P2 | S | **Narrowed & demoted** from P1/M. 78 unit tests cover tool behavior; eval scope reduced to "does the agent pick the right workspace_* tool for the request?" |
| #341 | Eval coverage: shell tools (approval-flow focus) | P2 | S | **Narrowed**. 39 unit tests cover execution; eval scope reduced to auto_confirm / denial recovery / allowlist bypass. |
| #342 | Eval coverage: conversation tools (post-compaction recall) | P2 | S | **Narrowed**. 28 unit tests cover search/compaction mechanics; eval scope reduced to "does LLM-driven summarization preserve salient facts?". Blocked on #353. |
| #343 | Eval coverage: delegate_task (delegation-decision only) | P2 | S | **Narrowed**. 10 unit tests cover the fork mechanism; eval scope reduced to "does the agent decide to delegate at appropriate times?". |
| #344 | Eval coverage: tool deferral (tool_search + context budget) | P2 | S | Unchanged — tool-deferral behavior is genuinely LLM-driven and not unit-testable. Blocked on #349. |
| #348 | Tighten existing memory evals (bounds, assertion-quality, stale names) | P2 | S | Unchanged — this is existing-YAML cleanup. |
| ~~#345~~ | ~~Eval coverage: checklist tools~~ | — | — | **Closed as redundant** — `test_checklist.py` + `test_checklist_tools.py` (21 tests) cover the state machine; remaining LLM-behavior question is too thin to justify a dedicated file. |
| ~~#346~~ | ~~Eval coverage: user-invokable commands~~ | — | — | **Closed as redundant** — `test_commands.py` (27 tests) covers the dispatch layer; all proposed test cases are deterministic code paths, not LLM behavior. |
| ~~#347~~ | ~~Eval coverage: health skill~~ | — | — | **Closed as redundant** — `test_health.py` (9 tests) covers every section; no LLM-specific behavior. |

### Harness issues (6)

| # | Title | Pri | Size | Notes |
|---|-------|-----|------|-------|
| #349 | Eval harness: `expect_tool` / `expect_no_tool` / `expect_tool_count_by_name` | P1 | S | Foundation for tool-deferral evals + strengthens most others. |
| #350 | Eval harness: multi-model matrix runner + combined report | P2 | M | Per #240's "run against multiple models" ask. |
| #351 | Eval harness: pass-rate trend tracking across runs | P2 | S | Lets us detect regressions over time. |
| #352 | Eval harness: post-turn workspace-state assertion | P2 | S | Strengthens workspace/vault/ingest evals. |
| #353 | Eval harness: `setup.conversation_history` to seed archives | P2 | S | Unblocks #342. |
| #354 | Eval harness: misc quality (judge-prompt assertion coverage + `response_contains_all`) | P2 | XS | Bundles H14 + H16. |

### Standalone (surfaced by audit, not #240 child)

| # | Title | Pri | Size | Notes |
|---|-------|-----|------|-------|
| #355 | Tool registry: did-you-mean error suggests the same unknown tool name | P2 | XS | Real tool-dispatch bug caught during the eval run. |

### Deferred issues (9) — noted here, not filed yet

Either blocked on harness work that itself is P3, or low-priority extensions. File when the blocker is ready:

- Eval coverage: dream + garden consolidation (blocked on scheduled-mode harness, H8)
- Eval coverage: Claude Code subagent (blocked on sandbox/mock harness, H11)
- Eval coverage: effort-level switching (blocked on effort-probe harness, H10)
- Eval coverage: cancel / stop behavior (blocked on cancel-probe harness, H9)
- Eval harness: scheduled / heartbeat mode simulation (H8)
- Eval harness: Claude Code sandbox / mock (H11)
- Eval harness: effort-level switching probe (H10)
- Eval harness: cancel probe (H9)
- Eval harness: `expect_tool_args` assertion (H3, extends `expect_tool`)

### Standalone (1) — not linked to #240

| Title | Pri | Size | Notes |
|-------|-----|------|-------|
| Tool registry: "did you mean" error suggests the same unknown tool name | P2 | XS | Self-contradicting error: `unknown tool 'project_advance'. Did you mean: project_advance, ...`. Surfaced during the eval run (project-skill failures). Real tool-dispatch bug, not eval-harness. |

## 6. Non-goals of this audit

- **Fixing failing tests.** The 4 runtime failures surface real behavior gaps (memory retrieval triggers, project-skill step-parsing). Those belong in the respective per-system issues, not this audit.
- **Writing new eval YAML files.** That's what the spun-out issues are for.
- **Building the harness features.** Spun out.
- **Fixing tool descriptions / SKILL.md wording.** Out of scope. The audit lists opportunities; execution belongs with the per-skill evals.

## 7. Execution state at audit time

- Branch: `eval-coverage` (worktree at `.claude/worktrees/eval-coverage`).
- Commits on this branch at audit time: spec scaffolding + inline doc/bug fixes (3 files: `docs/eval-loop.md`, `src/decafclaw/eval/reflect.py`, `Makefile`). Audit doc + full plan/notes follow in the next commit.
- Eval run bundle: `evals/results/2026-04-24-1015-default/`.
- Default model in use: resolves to `default` in config (likely `vertex-gemini-flash` per recent history; not explicitly introspected during this run).
