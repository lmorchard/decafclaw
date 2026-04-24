# Dev session notes: eval coverage audit

## Step 1: Harness capabilities

### CLI (`python -m decafclaw.eval`)

| Flag | Default | Purpose |
|------|---------|---------|
| `path` (positional) | required | YAML file or directory — directory auto-discovers `*.yaml` |
| `--model` | `default_model` or `llm.model` | Override LLM model; looks up `model_configs` first, else treated as raw name |
| `--judge-model` | same as `--model` | Model for failure reflection |
| `--verbose` | off | Print truncated response for each test |
| `--concurrency` | 4 | Max concurrent tests via asyncio semaphore |

Exits non-zero if any test fails (useful for CI).

### Test case schema (single-turn)

Required: `name`, `input`, `expect`.
Optional: `setup`, `allowed_tools`.

### Test case schema (multi-turn)

Replaces `input`/`expect` with `turns: [{input, expect}]`. Each turn gets its own assertions; all must pass. History is shared across turns within a test.

### `setup` fields

| Field | Purpose |
|-------|---------|
| `skills: [names]` | Pre-activate skills before the turn |
| `memories: [{content, tags}]` | Seed journal entries (also indexed for semantic search if strategy == semantic) |
| `workspace_files: {path: content}` | Seed arbitrary files into `config.workspace_path` (sandboxed against `..` escape) |
| `embeddings_fixture: path` | Copy pre-built embeddings.db into workspace |
| `auto_confirm: bool` | Default true. Auto-approve (or deny) all confirmation requests. |

### `expect` assertions

| Field | Type | Semantics |
|-------|------|-----------|
| `response_contains` | str / list[str] / `"re:pattern"` | Any match passes (case-insensitive for non-regex) |
| `response_not_contains` | str / list[str] | All absent (case-insensitive); any match fails |
| `max_tool_calls` | int | Fail if `tool_calls > max` |
| `max_tool_errors` | int | Fail if count of tool messages containing `[error` > max |

Regex is opt-in with the `re:` prefix. Lists use OR semantics for `response_contains`, AND for `response_not_contains`.

### Per-test isolation

Each test runs in `tempfile.TemporaryDirectory()` as `config.agent.data_home` via `dataclasses.replace(agent=replace(..., id="eval"))`. Different tests cannot see each other's workspace.

### Command dispatch

The runner calls `dispatch_command` before `run_agent_turn`, so `/foo` and `!foo` user-invocable skill commands work in evals. `help`/`fork`/`inline`/`unknown`/`error` modes all handled. Fork commands skip the agent loop and treat `cmd.text` as the response for assertion purposes.

### Result bundle

Written to `evals/results/{YYYY-MM-DD-HHMM}-{model}/`:
- `results.json` — full per-test details
- `reflections/{slug}.md` — LLM-generated failure analysis, one per failed test

### Makefile targets

Only `make build-eval-fixtures` (runs `scripts/build-eval-fixtures.py`). **No `make eval` / `make evals` target** — ad hoc invocation via `uv run python -m decafclaw.eval evals/`. Candidate Makefile addition.

### What's missing vs #240 scope

Capabilities #240 implies we need but the runner doesn't currently provide:

- **`expect_tool` assertion** (assert a specific tool WAS called by name). Docs (`docs/eval-loop.md`) claim this exists; runner's `_check_assertions` has no such branch.
- **`expect_no_tool` assertion** (assert a specific tool was NOT called). Same doc claim, same missing impl.
- **Tool-argument inspection** (assert a tool was called with a specific arg value). No hook.
- **Multi-model matrix runner** — `--model` takes one value; no combined pass-rate report across models.
- **Pass-rate trend tracking** — each run writes a standalone bundle; no aggregation across runs.
- **Post-turn workspace state assertion** (assert file X exists or contains Y after the turn). No assertion hook.
- **Cancel / stop-mid-turn probe** — no way to simulate user cancellation.
- **Effort-level switching probe** — no assertion on effort flips.
- **Context-budget / deferred-tool probe** — no way to assert "tool X was fetched via tool_search" or "context size stayed under budget".

### Bugs / doc rot caught already

1. **`docs/eval-loop.md` uses wrong field names.** Doc shows `prompt`/`expect_contains`/`expect_tool` as flat fields; runner uses `input`/`expect: {response_contains}`. Doc's `expect_tool` table row describes an assertion that does not exist in `runner._check_assertions`. (→ Step 7 inline fix + spin-out issue for actually implementing `expect_tool`.)
2. **`reflect.py` multi-turn bug.** Lines 38–42 extract `role`/`content` keys from turns, but our turn schema is `{input, expect: ...}`. For multi-turn failures, `input_text` will always be `[user] ` with no content, so the judge prompt is malformed. (→ Step 7 candidate; trivial fix.)
3. **`reflect.py` ignores non-`response_contains` assertions.** The prompt template only interpolates `expected = expect.get("response_contains", "?")`. If a test fails on `max_tool_calls` or `response_not_contains`, the judge sees `"?"` and can't give useful advice. (→ could inline-fix or note as harness-gap; decide in Step 7.)

---

## Step 2: Static audit

### Tool-name reality check

Current tool inventory (grepped from `src/decafclaw/tools/*.py` + `src/decafclaw/skills/*/tools.py`):

- **Core tools**: `activate_skill`, `checklist_*` (4), `context_stats`, `conversation_compact`, `conversation_search`, `current_time`, `debug_context`, `delegate_task`, `file_share`, `get_attachment`, `health_status`, `heartbeat_trigger`, `http_request`, `list_attachments`, `refresh_skills`, `send_email`, `send_notification`, `shell`, `shell_patterns`, `tool_search`, `wait`, `web_fetch`, `workspace_*` (11).
- **Skills**: `claude_code_*` (7), `mcp_*` (5), `project_*` (12), `shell_background_*` (4), `tabstack_*` (5), `vault_*` (11).

**Tools that DO NOT EXIST anymore**: `memory_search`, `memory_recent`, `memory_save`, `think`.

Renaming history (inferred): `memory_*` → `vault_*` as part of the vault unification. `think` — never existed in current tree; possibly an old Claude-specific convention that was never implemented here.

### Per-file verdicts

#### `ingest.yaml` (1 test)

| # | Test | Verdict |
|---|------|---------|
| 1 | "ingest workspace file produces primary page + change summary" | 🔍 needs-runtime-evidence — command `/ingest` and all referenced tools (`tabstack_extract_markdown`, `workspace_read`, `vault_search`, `vault_read`, `vault_write`, `vault_list`, `vault_backlinks`, `current_time`) all exist. Path arg semantics (`workspace/imports/ripgrep-notes.md` → `workspace_read(path="imports/ripgrep-notes.md")`) matches SKILL.md Step 1. Regex assertion `ingested:.*ripgrep-notes\.md.*primary page.*\[\[.*\]\]` matches the skill's Step 6 output shape. |

Overall: **structurally valid, pending runtime confirmation.**

#### `memory.yaml` (8 tests)

No `allowed_tools` restriction; agent has full tool set. `memories:` setup still works (the runner seeds the journal directory + indexes for semantic search if strategy is semantic).

| # | Test | Verdict |
|---|------|---------|
| 1 | "finds preference by direct term" | ✅ should-work — journal seed + vault search flow is intact |
| 2 | "finds preference by related term" | ✅ should-work |
| 3 | "recalls recent memories" | ⚠️ assertion-quality — `response_contains: [concise, event bus]` is OR per runner semantics; the test name implies AND |
| 4 | "handles missing memories gracefully" | ✅ should-work — expects "don't" in response |
| 5 | "saves memory when asked" | ✅ should-work — expects "Python" in response |
| 6 | "connects related memories" | ⚠️ assertion-quality — same OR-vs-AND issue as #3 |
| 7 | "uses think tool for complex question" | ⚠️ test-name-rot — test is named after a `think` tool that no longer exists; the test itself just uses response-text matching so it still works, but the name is misleading |
| 8 | "finds info with indirect phrasing" | ⚠️ assertion-quality — three alternatives all count as match (`software engineer`, `engineer`, `platform`); the last alone is too weak |

Overall: **mostly valid, but assertion-quality issues permeate**. Not bit-rot; test design.

#### `memory-multi-turn.yaml` (4 tests)

All tests lack `max_tool_calls` and `max_tool_errors` bounds — runaway tool-loop behavior wouldn't fail these. Otherwise clean.

| # | Test | Verdict |
|---|------|---------|
| 1 | "save then recall hobby" | ✅ should-work |
| 2 | "save preference then ask about it indirectly" | ⚠️ assertion-quality — OR semantics on `["Thai", "spicy"]` |
| 3 | "save multiple facts then recall all" | ⚠️ assertion-quality — OR semantics on `["Luna", "Mozilla"]`; test name implies AND |
| 4 | "correct a memory" | ✅ should-work (final-turn assertion is a single string) |

Overall: **valid but loose**. No bounds + OR semantics mean these catch gross regressions only.

#### `memory-semantic.yaml` (7 tests)

**❌ ALL FUNDAMENTALLY BROKEN.** Every test has `allowed_tools: [memory_search, memory_recent, memory_save, think]` — **none of these tools exist**. The runner's `ctx.tools.allowed = set(allowed_tools)` means the agent can't call any real tool; every attempt returns a disallowed-tool error. Predicted runtime: 0/7 pass, possibly all failing with `[error: tool not allowed]` or similar.

| # | Test | Verdict |
|---|------|---------|
| 1–7 | (all) | ❌ fundamentally-broken — `allowed_tools` references removed tool names |

Fix requires replacing the `allowed_tools` list with current `vault_*` tools. That's more than a sentence-level fix — it's a substantive rewrite of every test in the file, and should probably be done as part of the "vault evals" spun-out issue.

#### `postmortem.yaml` (1 test)

| # | Test | Verdict |
|---|------|---------|
| 1 | "postmortem produces all five sections, blamelessly framed" | 🔍 needs-runtime-evidence — command `/postmortem` exists. SKILL's `allowed-tools: vault_write, current_time` both exist. The five-section regex matches SKILL.md's required structure exactly. `response_not_contains` list of apology phrases matches SKILL.md's "Forbidden phrasing" rule. |

Overall: **structurally valid.**

#### `project-skill.yaml` (7 tests)

Uses `setup: skills: [project]` for pre-activation. All `project_*` tools referenced (`project_update_spec`, etc.) still exist. `spec_review` appears in `response_not_contains` — it's a **state name** in `project.state.WorkflowState.SPEC_REVIEW`, not a tool, so the assertion is about the LLM not narrating entering that state. Still meaningful.

| # | Test | Verdict |
|---|------|---------|
| 1 | "creates project before asking questions" | 🔍 needs-runtime-evidence — well-bounded (max_tool_calls: 3, max_tool_errors: 0) |
| 2 | "asks a question on first turn, does not jump to spec" | 🔍 needs-runtime-evidence |
| 3 | "continues interviewing after first answer" | 🔍 needs-runtime-evidence |
| 4 | "writes spec when asked" | 🔍 needs-runtime-evidence — `max_tool_calls: 50` is very loose for a 2-turn test (intentional per file's top-comment about auto_confirm chaining) |
| 5 | "writes plan after spec approval" | 🔍 needs-runtime-evidence — similar |
| 6 | "does not ask verbal approval question" | 🔍 needs-runtime-evidence |
| 7 | "executes plan steps" | 🔍 needs-runtime-evidence |
| 8 | "handles review denial gracefully" | 🔍 needs-runtime-evidence — sets `auto_confirm: false`, so EndTurnConfirm denies |

Overall: **structurally valid, well-designed.** This is the most carefully-built eval file; recent work on #17.

### Summary table

| File | Tests | Structurally valid | Bit-rotted | Assertion-quality flags |
|------|-------|--------------------|------------|-------------------------|
| `ingest.yaml` | 1 | 1 | 0 | 0 |
| `memory.yaml` | 8 | 8 | 0 | 4 (OR-vs-AND, stale test name) |
| `memory-multi-turn.yaml` | 4 | 4 | 0 | 3 (OR-vs-AND, no bounds) |
| `memory-semantic.yaml` | 7 | 0 | **7** | n/a — all broken |
| `postmortem.yaml` | 1 | 1 | 0 | 0 |
| `project-skill.yaml` | 8 | 8 | 0 | 0 |
| **Total** | **29** | **22** | **7** | **7** |

### Structural issues worth flagging

- **OR-vs-AND semantics on `response_contains` lists.** The runner's implementation is OR (matches any). ~7 tests across the memory files use list form where the test name suggests AND. If AND semantics are wanted, either:
  - The runner grows a `response_contains_all` assertion (harness gap), OR
  - Tests are rewritten using regex alternation or multiple test cases.
  - At minimum, document the OR semantics clearly in `docs/eval-loop.md`.

- **Missing `max_tool_calls` on `memory-multi-turn.yaml`.** All 4 tests lack any tool-budget bound. Adding generous bounds (e.g. 20 per turn) would catch agent-loop regressions.

- **Missing bounds audit in `memory.yaml`.** Tests 3 ("recalls recent"), 4 ("handles missing"), 6 ("connects related"), 7 ("uses think tool"), 8 ("finds info indirect") lack `max_tool_calls`.

- **`memory-semantic.yaml` is completely bit-rotted.** This is the big ticket item for the "vault evals" spun-out issue — the test content (input/expected output) is largely fine, but the `allowed_tools` field needs wholesale replacement with current `vault_*` tool names. Also an opportunity to verify semantic-search behavior actually still works end-to-end.

### Notes on runtime prediction

Going into Step 3 with this prediction:

- `ingest.yaml`: 1/1 likely passes (pending `tabstack_extract_markdown` key being present; note ingest.yaml is workspace-path, not URL, so shouldn't need Tabstack at all — but SKILL.md requires the `tabstack` skill per `required-skills` frontmatter even for workspace paths? Worth checking runtime.)
- `memory.yaml`: 8/8 likely passes (OR-semantics means most tests are very generous).
- `memory-multi-turn.yaml`: 4/4 likely passes.
- `memory-semantic.yaml`: **0/7 expected** (fundamentally broken allowed_tools).
- `postmortem.yaml`: 1/1 likely passes.
- `project-skill.yaml`: probably 6-7/8 based on #17 prior pass rate.

Total predicted pass rate: **~21-22 / 29 = 72-76%**, with the bulk of failures concentrated in `memory-semantic.yaml`. If that pattern holds, Step 4's triage will have a clear "fix the vault allowed_tools" bucket and a small "real per-test" bucket.

---

## Step 9: Filing recipe (pre-computed)

Project: `decafclaw` — number 6, ID `PVT_kwHNVLfOAUg5pw`, owner `lmorchard`.

### Field IDs

| Field | Field ID | Options |
|-------|----------|---------|
| Status | `PVTSSF_lAHNVLfOAUg5p84P7O9L` | Backlog `f75ad846`, Ready `61e4505c`, In progress `47fc9ee4`, In review `df73e18b`, Done `98236657` |
| Priority | `PVTSSF_lAHNVLfOAUg5p84P7O_D` | P0 `79628723`, P1 `0a877460`, **P2 `da944a9c`**, P3 `19341fa5`, P4 `8657c355` |
| Size | `PVTSSF_lAHNVLfOAUg5p84P7O_E` | XS `6c6483d2`, S `f784b110`, **M `7515a9f1`**, L `817d0097`, XL `db339eb2` |

Defaults for this batch per spec: **P2 / M / Backlog** (P2 = `da944a9c`, M = `7515a9f1`).

### Recipe (bash)

```bash
# 1. File issue with project membership
URL=$(gh issue create \
  --repo lmorchard/decafclaw \
  --title "<TITLE>" \
  --body-file /tmp/body.md \
  --project "decafclaw")

# 2. Resolve project item ID from the issue URL
ITEM_ID=$(gh project item-list 6 --owner lmorchard --format json --limit 200 \
  | jq -r ".items[] | select(.content.url == \"$URL\") | .id")

# 3. Set Priority (P2) and Size (M)
gh project item-edit --project-id PVT_kwHNVLfOAUg5pw --id "$ITEM_ID" \
  --field-id PVTSSF_lAHNVLfOAUg5p84P7O_D --single-select-option-id da944a9c
gh project item-edit --project-id PVT_kwHNVLfOAUg5pw --id "$ITEM_ID" \
  --field-id PVTSSF_lAHNVLfOAUg5p84P7O_E --single-select-option-id 7515a9f1
```

Step 10 verifies this works end-to-end on the first issue before bulk-applying.

---

## Step 5: Coverage gap walk

#240's scope is a mix of skills, tools, and system behaviors. Walking each bullet and mapping to a proposed eval file. Grouping follows the "one issue ≈ one eval file ≈ one PR" model from spec.md.

Note: #234 ("Evaluate todo tools") was closed as completed on 2026-04-15. The current always-loaded `checklist_*` tools replaced todos. So the "Todo tools" bullet in #240 should be read as **checklist tools**.

### Proposed eval files

| Proposed file | Covers (from #240 and adjacent) | Current state | Notes |
|---------------|--------------------------------|---------------|-------|
| `vault.yaml` | Vault skill: read, write, search, journal_append, backlinks, list, delete, rename, show_sections, move_lines, section | Partial (memory*.yaml cover journal/search indirectly via "remember" prompts; no direct tool-call coverage for most) | `memory-semantic.yaml` needs wholesale rewrite (broken `allowed_tools`). `memory.yaml` + `memory-multi-turn.yaml` can stay as "conversational recall" tests but the *new* file should directly exercise each tool. |
| `health.yaml` | Health skill + `health_status` tool | None | Test `!health` invocation + raw tool call. Mostly a smoke test since health is descriptive. |
| `consolidation.yaml` | Dream + garden skills | None | Hard to eval: these are scheduled long-running skills. Probably needs harness support for "simulated scheduled context" or canonical seed vaults. Could split into `dream.yaml` + `garden.yaml` — recommend combined. |
| `claude-code.yaml` | Claude Code subagent skill | None | Dangerous to run in eval — spawns real subprocesses. Likely needs sandbox/mock. Flag as "blocked on harness support" unless we're OK running real claude_code sessions in the eval. |
| `project.yaml` | Project skill | `project-skill.yaml` exists, 8 tests, well-built | Leave as-is; possibly rename for consistency. Per #17 lessons, this is the gold standard eval file. |
| `workspace-tools.yaml` | workspace_read/write/edit/search/glob/list/move/delete/diff/insert/replace_lines/append | None | 12 tools; biggest file. Should test happy paths + error paths (e.g. write to outside workspace, read non-existent file). |
| `shell.yaml` | shell, shell_patterns, shell_background_* (4) | None | `auto_confirm` behavior is central — test the approval flow. shell_patterns test: confirm allowlist-bypass. |
| `conversation.yaml` | conversation_search, conversation_compact | None | Compaction requires a populated history. Needs harness support for seeding conversation history, or multi-turn setup that builds history then triggers compact. |
| `delegate.yaml` | delegate_task | None | Forks a child agent. Test: simple delegate returns result; delegate with failing task returns error. |
| `tool-deferral.yaml` | tool_search + deferred loading + context budget awareness | None | Test: agent fetches a deferred tool via `tool_search`. Harness gap: can't currently assert "tool X was fetched" — need new assertion or check via tool_calls history. |
| `checklist.yaml` | checklist_create, checklist_step_done, checklist_abort, checklist_status (was "todo tools") | None | Always-loaded tools. Bullet in #240 was stale ("todo tools if we keep them"); we do keep them under new name. |
| `commands.yaml` | User-invokable `/command` and `!command` dispatch | Implicitly tested via `ingest.yaml`, `postmortem.yaml`, `project-skill.yaml` | Dedicated file should test the dispatch layer: unknown command, missing required args, `$ARGUMENTS` substitution, `context: fork` isolation, `--help` listing. |
| `effort-switching.yaml` | Effort level switching | None | **Blocked on harness support** — need a way to assert effort state changed. |
| `cancel.yaml` | Stop/cancel mid-turn | None | **Blocked on harness support** — need a way to simulate user cancellation mid-turn. |

### Existing files that stay as-is (with fixes)

| File | Action |
|------|--------|
| `ingest.yaml` | Keep — touches ingest skill (not in #240 list). May expand to cover URL + attachment paths. |
| `postmortem.yaml` | Keep — touches postmortem skill (not in #240 list). |
| `project-skill.yaml` | Keep — is the project eval file. Consider renaming to `project.yaml` for consistency. |
| `memory.yaml` | Keep, tighten — add `max_tool_calls` bounds, fix OR-vs-AND test naming (either rename tests or convert to regex alternation / split into separate tests). |
| `memory-multi-turn.yaml` | Keep, tighten — same as memory.yaml. |
| `memory-semantic.yaml` | **Rewrite** as part of the `vault.yaml` issue. Fix `allowed_tools` to current `vault_*` names; rethink whether it should live as a semantic subset of `vault.yaml` or separately. |

### Minimal viable test set per new eval file

For the "medium" priced issues, a 3–5 test minimum. Sketches below — not detailed YAML, just test ideas.

#### `vault.yaml`

- `vault_read`: seed workspace_files with a page, agent reads it when referenced.
- `vault_write`: agent creates a new page with frontmatter under `agent/pages/`.
- `vault_search`: seed memories, semantic-query finds the right one (fix the existing `memory-semantic.yaml` tests here).
- `vault_backlinks`: seed two pages with wiki-links between them, check backlinks query returns the linker.
- `vault_journal_append`: "remember X" prompt triggers the right tool.
- Section-aware: `vault_section` + `vault_show_sections` + `vault_move_lines` — probably one test showing a section edit that leaves the rest intact.

#### `workspace-tools.yaml`

- `workspace_read`: seed file, read it by path.
- `workspace_write` + sandbox: attempt to write outside workspace, expect error.
- `workspace_edit`: search-replace in a seeded file; verify via `workspace_read` after.
- `workspace_glob`: seed dir with 3 matching + 3 non-matching; query finds the 3.
- `workspace_search`: seed files with ripgrep-findable content; query finds the match.
- `workspace_diff`: edit a file then diff to see the change.

#### `shell.yaml`

- Unapproved shell command with `auto_confirm: true` (default): agent runs `echo hello`, returns stdout.
- Unapproved shell command with `auto_confirm: false`: agent's call is denied; agent gracefully recovers.
- `shell_patterns` allowlist bypass: `ls` is allowlisted, agent runs it without confirmation overhead.
- Background start + status + stop lifecycle.

#### `conversation.yaml`

- `conversation_search`: seed a conversation archive file, agent searches it. **Needs harness gap closed — `setup` doesn't currently seed conversation archives.**
- `conversation_compact`: multi-turn test that gets long enough to trigger compaction; agent calls `conversation_compact` and history shrinks.

#### `delegate.yaml`

- Delegate a trivial task (e.g. "count the letters in this string"); child returns result.
- Delegate a task that times out; parent gets error.

#### `tool-deferral.yaml`

- Agent is asked something requiring a deferred tool; agent calls `tool_search`, then calls the fetched tool.
- Tool budget: context budget stays under N tokens; verify via new harness assertion.

#### `commands.yaml`

- `/unknown-command` dispatch returns clean error, agent doesn't crash.
- `/ingest` with no args uses attachment flow (covered by `ingest.yaml`, but worth including here).
- `$ARGUMENTS` substitution: a test skill with `$1` in its command body gets the arg.
- `context: fork` isolation: fork command has no access to prior conversation history.

#### `health.yaml`

- `!health` command returns the agent's status block.
- `health_status` tool called mid-conversation returns a structured result.

#### `consolidation.yaml`

- Seed a journal with several days of entries; run the dream consolidation; expect a vault page update.
- Seed a messy vault; run garden; expect cross-link fixes.
- **Requires harness support for "simulated scheduled context"** — scheduled runs don't go through the normal interactive path. Blocked.

#### `checklist.yaml`

- Agent given a multi-step task; creates a checklist; iterates step-by-step; completes.
- Agent aborts a checklist mid-way.
- `checklist_status` returns remaining steps.

### Harness capabilities required per new file

| File | Requires new harness capability? |
|------|----------------------------------|
| `vault.yaml` | No |
| `health.yaml` | No |
| `consolidation.yaml` | **Yes** — simulated scheduled context |
| `claude-code.yaml` | **Yes** — sandbox or mock for subprocess spawning |
| `workspace-tools.yaml` | Mild — post-turn workspace state assertion would strengthen tests, but existing `workspace_files` setup + follow-up `workspace_read` via a multi-turn test can substitute |
| `shell.yaml` | Mild — a way to assert a shell command ran with specific args (currently only count-based) |
| `conversation.yaml` | **Yes** — setup needs to seed a conversation archive |
| `delegate.yaml` | Possibly — child-agent timeout simulation |
| `tool-deferral.yaml` | **Yes** — assertion on which specific tool was called (the planned `expect_tool`) |
| `commands.yaml` | No |
| `effort-switching.yaml` | **Yes** — effort-state assertion |
| `cancel.yaml` | **Yes** — simulated cancellation mid-turn |
| `checklist.yaml` | Mild — post-turn workspace assertion for the checklist markdown file would help |

**Pattern:** most new eval files can be built with the current harness. The blocked-on-harness ones are `consolidation.yaml`, `claude-code.yaml`, `conversation.yaml`, `tool-deferral.yaml`, `effort-switching.yaml`, `cancel.yaml`. Two of these (`effort-switching`, `cancel`) may be hard enough that they're better treated as "investigate how to eval these at all" exploration issues rather than "write these tests" issues.

---

## Step 3: Runtime results

Run: `evals/results/2026-04-24-1015-default/` (worktree).

Summary: **25/29 passed (86.2%)**, 4 failures, ~372s total wall time, 744k tokens. Model resolved as `default` (whatever config default_model points to — needs checking for Step 4; likely Gemini Flash per config.json).

### Pass/fail by file

| File | Passed | Failed | Pass rate |
|------|--------|--------|-----------|
| `ingest.yaml` | 1 | 0 | 100% |
| `memory.yaml` | 6 | 2 | 75% |
| `memory-multi-turn.yaml` | 4 | 0 | 100% |
| `memory-semantic.yaml` | 7 | 0 | **100% (but see note)** |
| `postmortem.yaml` | 1 | 0 | 100% |
| `project-skill.yaml` | 6 | 2 | 75% |

### Critical non-failure finding: `memory-semantic.yaml` passes for the wrong reason

Every test in `memory-semantic.yaml` has `tool_calls: 0` or 5 for the one that actually needs it. The allowed_tools list references nonexistent tools, but **the agent doesn't NEED those tools** because proactive memory retrieval injects the seeded memories directly into context. The tests pass by the agent echoing back injected memories, not by actually exercising `vault_search`.

This is worse than the tests failing outright — it's a silent loss of coverage. The file claims to test semantic search, but the assertions pass even if vault_search is completely broken.

Contrast:
- Test "finds specific cat fact via semantic search" — `tool_calls: 5` — this one actually drove a tool call, possibly because "When was Felix the Cat created?" is outside the seeded memories, so proactive retrieval didn't help and the agent reached for a tool.
- All other tests — `tool_calls: 0` — agent answered straight from the context-injected memory.

**Implication for the vault rewrite issue**: new vault tests must force tool use. Either seed memories via a path that bypasses proactive retrieval (e.g. only index them without adding to recent memories), or use `allowed_tools: [vault_search]` (correct name this time) + force the distractor fixture, or use longer distractor vaults that push relevant entries out of the proactive retrieval window.

---

## Step 4: Failure triage

| # | Test (file) | Category | Analysis |
|---|-------------|----------|----------|
| 15 | "recalls recent memories" (memory.yaml) | **(d) real coverage issue** | Agent response: "I don't have any specific information about your past projects..." — 1 tool call. Seeded memories (`DecafClaw event bus refactor`, `Prefers concise answers`) existed but agent failed to surface them. Proactive memory retrieval didn't inject; vault_search returned unhelpful results. **Fix direction:** the test exposes a real gap where memory retrieval doesn't fire for an open-ended "what do you know about me" prompt. Belongs in the `vault.yaml` issue's scope to investigate — either the test is too vague (model interprets "what do you know about me" as "what did the user tell *you*" and doesn't reach for stored knowledge) or the retrieval heuristic needs tuning. Not bit-rot. |
| 19 | "uses think tool for complex question" (memory.yaml) | **(d) real coverage issue** | Agent response: invented a fictitious Caprese/etc. menu, 0 tool calls. Expected: reference Thai/Japanese food OR cocktails (per seeded memories). Agent didn't reach for memory at all. **Fix direction:** the prompt "If you were planning a dinner party for me, what would you serve?" doesn't explicitly say "based on my preferences" — the agent treats it as a generic creative prompt. Same as #15: either tighten the prompt, or tune retrieval triggers. Note: test name says "uses think tool" but `think` doesn't exist; test name is stale (already flagged in static audit). |
| 25 | "writes spec when asked" (project-skill.yaml) | **(d) real project-skill behavior issue** | 3 tool errors: `unknown tool 'project_advance'. Did you mean: project_advance, ...` (self-contradicting error message — **separate bug**), `write the plan with project_update_plan before project_task_done`, `no steps parsed. Use checkbox format`. Agent produced the haiku but stumbled through the project workflow. **Fix direction:** project skill step-parsing regex is strict; the agent's output format doesn't match. Belongs in project-skill issue tracking, NOT this audit. |
| 26 | "writes plan after spec approval" (project-skill.yaml) | **(d) real project-skill behavior issue** | Same pattern: 31 tool calls, 3 tool errors around step ordering and parsing. Agent eventually completed but ran over the `max_tool_errors: 1` bound. **Fix direction:** same as #25. |

### Harness bug caught (by accident)

- **Self-contradicting "did you mean" error message.** Error reads: `unknown tool 'project_advance'. Did you mean: project_advance, ...` — the suggested alternative is identical to the unknown tool. This is a bug somewhere in the tool dispatch / unknown-tool-suggestion path (possibly `src/decafclaw/tools/__init__.py` or the registry). **Separate issue, not eval-related.** Spin out.

### What the results bundle captured

- `results.json` — per-test details with full history on failures.
- `reflections/` — 4 markdown files, one per failure, with judge-model analysis. Worth reviewing briefly to see if judge's suggestions match my manual triage.

### Pass-rate discipline

No flaky pattern in the failures (each has a consistent, reproducible root cause). No rate-limit-shaped failures. Concurrency at 4 was fine. No need to re-run sequentially.

### Re-prediction scorecard

My static audit predicted `memory-semantic.yaml` at 0/7 (actual: 7/7 — but for the wrong reasons, see note above) and 72-76% total (actual 86.2%). The static analysis underweighted proactive memory injection. Worth remembering: eval test coverage isn't just "does it pass" — it's "does it test what it claims to test." The harness can't detect tests that pass trivially.

---

## Step 6: Harness gaps

Consolidated list of harness capability issues surfaced across steps 1, 4, 5. Each is a candidate for its own spin-out issue.

| # | Gap | Rationale | Size |
|---|-----|-----------|------|
| H1 | **`expect_tool` / `expect_no_tool` assertions** | Docs already claim these exist; runner doesn't implement them. Fundamental for testing "did the agent reach for the right tool" without relying on fragile `response_contains` strings. Unblocks `tool-deferral.yaml`, strengthens nearly every other eval. | S |
| H2 | **`expect_tool_count_by_name` assertion** | Fine-grained counting — e.g. assert exactly 1 `vault_search` call. Complements H1. | XS |
| H3 | **`expect_tool_args` assertion** | Assert a specific tool was called with specific arg values. More powerful but also more brittle. Defer until we hit a test that really needs it. | M |
| H4 | **Multi-model matrix runner** | `--model` takes one value. Need a single invocation that runs the suite across a configured list of models and produces a combined pass-rate report. Requested by #240 ("run against multiple models") and #17's lesson that different models fail differently. | M |
| H5 | **Pass-rate trend tracking** | Each run writes a standalone bundle. `evals/results/` is gitignored so there's no history. Proposal: `evals/history.jsonl` (or similar) capturing per-run summary, committed to git. Lets us detect regressions. | S |
| H6 | **Post-turn workspace state assertions** | Currently can only assert on response text and tool-call count. No way to check "after this turn, `agent/pages/foo` exists with frontmatter `summary: ...`". Critical for vault/workspace/ingest evals. | S–M |
| H7 | **Conversation archive seeding in `setup`** | `setup` can't seed a conversation archive for `conversation_search` / `conversation_compact` evals. Need `setup.conversation_history: [...messages]` or `setup.conversation_archive_file`. | S |
| H8 | **Scheduled/heartbeat mode simulation** | Evals run as `AgentMode.INTERACTIVE`. Dream/garden/heartbeat-mode tests need the scheduled context path (different system prompt assembly). Probably `setup.mode: scheduled` + a per-task preamble injection. | M |
| H9 | **Cancel probe (simulated stop mid-turn)** | No way to test cancel behavior. Needs a hook that triggers `ctx.cancelled = True` at a scripted point mid-turn. | M–L |
| H10 | **Effort-level switching probe** | No way to assert effort state changed. Need visibility into effort-level transitions (or a probe that inspects post-turn state). | M |
| H11 | **Claude Code sandbox/mock** | `claude_code_*` tools spawn real subprocesses — dangerous in parallel eval runs. Need a mock mode or a sandbox that exercises the skill's dispatch without real execution. | L |
| H12 | **Context-budget / deferred-tool probe** | Assertion on "tool X was fetched via tool_search" or "context size under N tokens". Could ride on H1 (`expect_tool_call_sequence`) or be its own thing. | S |
| H13 | **`reflect.py` multi-turn bug** | Lines 38-42 extract `role`/`content` keys from turns; our turn schema is `{input, expect}`. Multi-turn failure reflections have malformed inputs. **Trivial fix — can be inline.** | XS |
| H14 | **`reflect.py` ignores non-`response_contains` assertions** | Judge prompt only interpolates `expected = expect.get("response_contains", "?")`. Failures on `max_tool_calls` / `max_tool_errors` / `response_not_contains` get useless judge advice. **Trivial fix — can be inline.** | XS |
| H15 | **`make eval` target** | No Makefile target. Current invocation is `uv run python -m decafclaw.eval evals/`. Add `make eval` (single-run) and maybe `make eval-matrix` (after H4). | XS |
| H16 | **`response_contains_all` assertion (AND semantics)** | The runner's list form of `response_contains` is OR (matches any). Several tests are mis-named implying AND. A `response_contains_all` would close the gap without rewriting tests. | XS |
| H17 | **Silent-pass detection / test quality guard** | `memory-semantic.yaml` passed for the wrong reason (proactive retrieval bypassed the `allowed_tools` constraint because the agent didn't need any tool). No mechanical fix; instead, a docs addition: "when your test aims to exercise a specific tool, assert `tool_calls > 0` via a new `min_tool_calls` or equivalent, OR explicitly `expect_tool` (H1)." Half-harness, half-docs. | XS |
| H18 | **Separate tool-registry bug (self-contradicting "did you mean")** | Not an eval harness issue per se, but surfaced by the eval run. Tool dispatch produces `unknown tool 'foo'. Did you mean: foo, ...` where the suggestion is identical to the unknown. File as its own (non-eval) issue. | XS |

### Inline-fix candidates from this list

H13, H14, H15, H16 are each tiny and borderline between "inline fix on this branch" and "spin out". Per the spec's inline bar ("sentence-level" fixes only), H13+H14 fit (bug fix ≤ 5 lines each), H15 fits (Makefile line), H16 debatable (new assertion = small new feature, probably spin out).

**Decision for Step 7:** inline-fix H13, H14, H15 + `docs/eval-loop.md` field names. Spin out everything else.

H18 (tool dispatch "did you mean" bug) is not an eval issue at all — file separately, not part of the eval-coverage umbrella.

---

## Session retrospective

### Recap

Audit session for #240 ("Eval coverage: audit and expand"). Produced:

- **PR #338** — 4 commits on branch `eval-coverage`. Session scaffolding + spec, inline doc/bug fixes (`docs/eval-loop.md` schema rewrite, `reflect.py` multi-turn fix, `make eval` target), the audit doc + plan + notes, and the audit-doc issue-number update.
- **16 GitHub issues filed** (#339–#354) as children of #240, each on the decafclaw project board with priority + size set.
- **1 standalone issue filed** (#355) for the tool-registry did-you-mean bug caught by accident during the eval run.
- **#240 closed** with an umbrella comment linking all children.
- **9 deferred issues noted** in the audit doc but not filed — all P3 and blocked on P3 harness work.

### Divergences from plan

- **Step 12 reordered.** Plan had "final sync, PR, retro" as the last step. Les asked mid-execute to push the PR before filing issues so the filed issue bodies could point at a visible branch + PR. Executed as push + PR → Step 10 → Step 11 → retro.
- **Added Commit 4.** Not in the original plan. After filing issues I updated the audit doc's Section 5 with actual issue numbers (#339–#355) so the frozen audit record is self-sufficient, and committed that as a 4th commit. Small cost, useful artifact.
- **Issue count landed at 17 filed, not 16.** The standalone tool-registry bug (#355) was a side finding, not a #240 child. Filed it anyway as a separate issue since it's a real tool-dispatch bug surfaced by the eval run.

### Key findings (the actual content payoff)

- **`memory-semantic.yaml` was silently broken.** 7/7 runtime pass but testing nothing — allowed_tools referenced removed tool names, but proactive memory retrieval injected the seeded memories directly into context so the agent never reached for a tool. The insight "a test passing doesn't mean it's testing what it claims" was the most important audit finding and drove a docs update in `eval-loop.md` about forcing tool use when you mean to test a tool.
- **Doc rot in `docs/eval-loop.md`.** Field names (`prompt`/`expect_contains`/`expect_tool` as flat fields) described a schema the runner never had, including an `expect_tool` row documenting an assertion that doesn't exist. Fixed inline.
- **Two real harness bugs in `reflect.py`.** Multi-turn input extraction used the wrong schema keys (`role`/`content` vs. `input`/`expect`); fixed inline. Judge prompt only interpolates `response_contains` and gives useless advice for `max_tool_calls` / `max_tool_errors` failures; spun out to #354.
- **Self-contradicting "did you mean" error** in tool dispatch, caught during the eval run — filed as #355, unrelated to #240.

### Insights

- **Audit before solution.** Spending the brainstorm phase on "what's the scope of this session" rather than "how to build N evals" made the whole downstream work sharper. The discovery that `memory-semantic.yaml` silently passes is a good example — would have been invisible if I'd jumped straight to writing new eval files.
- **Per-eval-file issue granularity was right at this count.** 16 issues is a lot, but each is discrete and has a clear PR shape. Coarser would've muddied priorities; finer would've been annoying to track.
- **Don't trust a "pass" — check whether the test exercises the path it claims to.** This belongs in `docs/eval-loop.md` (now added) and in every future eval review.
- **GitHub Project Board filing is scriptable but tedious.** `gh issue create --project "NAME"` gets it on the board; `gh project item-edit` sets the custom fields. Three calls per issue. Fine for 16; would want a helper for 50+.
- **Reflections have real informational value even for an audit.** The judge model's reflections on the 4 runtime failures mostly matched my manual triage, which was useful as an independent check. Worth keeping `reflections/` generation enabled.

### Efficiency observations

- **Parallelization worked well.** Step 3 (eval run, ~2 min wall-clock) overlapped with Step 9 (gh project probe + field discovery) — saved some minutes. Similarly, writing the 15 issue body files happened in parallel tool calls where possible.
- **The runner's concurrency default (4) was right.** Zero rate-limit-shaped failures; no need to fall back to sequential. The pre-committed fallback plan was reassuring but unused.
- **The venv ambiguity was a near-miss.** My initial `ps aux` check showed the eval run was using Homebrew Python, not clearly the worktree venv. Turned out to be fine because main and worktree were on the same commit (no Python changes yet), but a future session with real code changes would need a deliberate `cd` into the worktree or absolute paths.
- **Writing the audit doc at end was the right shape.** Trying to draft it incrementally while still gathering evidence would have been choppy. Better to accumulate in `notes.md` and synthesize once the picture is complete.

### Process improvements

- **Memory: venv discipline in worktrees.** Every worktree command that invokes Python should use the worktree's venv path explicitly (`$WORKTREE/.venv/bin/python`), OR the session should start with `cd "$WORKTREE" && source .venv/bin/activate` as a preamble. The project CLAUDE.md already says "Worktrees go under `.claude/worktrees/`; each needs its own venv" — could expand that to "and every Python command should reference `$WORKTREE/.venv/bin/python` explicitly."
- **Helper for bulk issue filing.** The shell loop in Step 10 worked, but worth extracting as a shared utility (maybe a small `scripts/file-project-issue.sh`) since this pattern will repeat for future splits. Field IDs are stable per project, so hardcoded is fine.
- **Audit doc location principle (applied, keep).** Point-in-time audits live in the session dir; living guidance goes inline in `docs/` — this split worked and is worth keeping as a convention.

### Conversation turns

Roughly 20 back-and-forth exchanges. The brainstorm phase was ~6 turns (one question per turn), plan phase 2 turns, execute phase the bulk (~12 turns including the two intermediate checkpoints).

### Other highlights

- **PR body serves as a mini-retro.** Because the PR summarizes findings + test plan, readers get the audit's bottom-line without reading the full 900-line audit doc. Session doc is the deep record; PR is the elevator pitch.
- **#240's closing comment doubles as a dashboard.** The checkbox list of 16 children with blocker notes lets Les (or anyone) pick up where this left off without re-reading the audit.
- **Zero `make dev` conflict.** The parallel eval run didn't interfere with any running dev instance.


