# Evals renovation — plan

Sequence the spec into concrete, reviewable PRs.

## Branch and baseline discipline

- Single branch per PR, off `main`. Sync `main` from `origin/main` before each branch.
- Baseline model: **`vertex-gemini-flash`** (matches the 2026-04-24 audit run, matches today's re-audit).
- Pre-renovation baseline: **24/30 = 80.0%** (370s, 1.05M tokens), recorded in `audit.md`.
- Run `make eval` against the default model before each PR opens and after each PR's tests are added; record the result bundle path + pass-rate delta in the PR body.
- Per-PR session entries appended to this dir's `notes.md` — one section per PR, capturing surprises, tool-description tweaks made along the way, and any new bugs filed.

## PR-A — Harness polish (3 issues, XS+S+S)

Closes: **#354** (response_contains_all + judge prompt fix), **#352** (post-turn workspace state assertions), **#353** (setup.conversation_history seed).

### Commits (one per logical step)

1. `feat(eval): response_contains_all (AND semantics) + judge-prompt assertion coverage` — closes #354.
   - `src/decafclaw/eval/runner.py::_check_assertions`: new `response_contains_all` branch; AND semantics; supports `re:` prefix; case-insensitive for non-regex.
   - `src/decafclaw/eval/reflect.py`: build an `expected_summary` from whichever assertions are set (`response_contains`, `response_not_contains`, `max_tool_calls`, `max_tool_errors`, `expect_tool`, `expect_no_tool`, `expect_tool_count_by_name`, `response_contains_all`). Include the actual failure reason from `result["failure_reason"]` in the prompt.
   - `docs/eval-loop.md`: new row in the expect-fields table; brief contrast paragraph against `response_contains`.
   - `tests/test_eval_runner_assertions.py`: cases for `response_contains_all` (string list, regex list, mixed, empty list).
   - `tests/test_eval_reflect.py`: new file or extend existing — assert the judge prompt includes non-`response_contains` assertion summaries.

2. `feat(eval): post-turn workspace_files / workspace_file_exists / workspace_file_absent assertions` — closes #352.
   - `src/decafclaw/eval/runner.py`: extend `_check_assertions` to take a `workspace_path: Path` and check three new fields. Run after all turns complete (single check at end of test, not per-turn — paths reflect final state).
   - Sandbox the assertion path resolution the same way `_setup_workspace` does (no `..` escape).
   - `re:` prefix on content values triggers regex match; bare strings are exact match.
   - `docs/eval-loop.md`: three new rows.
   - `tests/test_eval_runner_assertions.py`: cases for exact match, regex match, exists-only, absent (positive and negative).

3. `feat(eval): setup.conversation_history seeds archive JSONL` — closes #353.
   - `src/decafclaw/eval/runner.py::_setup_workspace`: accept a `conversation_history: [...messages]` list and write `{workspace_path}/conversations/eval.jsonl` using the same format as `archive.py::append_message`.
   - Validate messages have the minimum required shape (role + content); fail-loud if malformed.
   - `docs/eval-loop.md`: setup-fields row + example showing a compaction test seeded with prior history.
   - `tests/test_eval_runner_setup.py`: case asserting the archive file is written and conversation_search can find a seeded message.

### Validation

- `make check` + `make test` clean.
- All three new assertion types covered by unit tests.
- Existing `evals/memory.yaml` smoke-test runs unchanged (no regression on the existing assertion set).

### Out of scope for PR-A

- Actually using these new fields in YAML — that's PR-B / PR-C.

---

## PR-B — Vault + memory cleanup (2 issues + finding F1)

Closes: **#339** (vault evals — replaces broken `memory-semantic.yaml`), **#348** (tighten `memory.yaml` + `memory-multi-turn.yaml`). Resolves **F1** (notes/vault disambiguation surfaced by failing PR #429 smoke test).

### Commits

1. `fix(tools): disambiguate notes_append from vault_journal_append in tool descriptions` — addresses F1.
   - Goal: "Please remember <user-level fact>" routes to `vault_journal_append`; "remember to follow up on X during this conversation" routes to `notes_append`.
   - Sharpen `notes_append` description's "use for" clause: emphasize *task-scoped*, *transient*, *will be discarded when this conversation ends*. Move the "things you want to remember" hook out — that's too generic and overlaps with vault.
   - Sharpen `vault_journal_append` description: emphasize *durable*, *cross-conversation*, *user-level facts and preferences*.
   - Validate by running `make eval-tools` first (the `tool_choice` surface) — add a `notes_append ↔ vault_journal_append` case to `evals/tool_choice/core_overlaps.yaml` that covers both directions: "Please remember my favorite color is blue" → `vault_journal_append`; "Note that I need to follow up on the migration question next turn" → `notes_append`.
   - Then re-run `make eval`; verify the existing smoke test in `memory.yaml` passes without changing the test.

3. `fix(evals): replace silently-broken memory-semantic.yaml with vault.yaml` — closes #339.
   - Delete `evals/memory-semantic.yaml`.
   - Create `evals/vault.yaml` with cases for:
     - `vault_journal_append`: "Remember X" prompt → `expect_tool: vault_journal_append`, `expect_no_tool: [vault_write, shell]`.
     - `vault_search`: question reachable only via search (use distractor-heavy embeddings fixture + memories the proactive-retrieval window won't surface) → `expect_tool: vault_search`.
     - `vault_read`: prompt naming a specific seeded page → `expect_tool: vault_read`, `expect_no_tool: vault_search`.
     - `vault_backlinks`: two seeded pages with wiki-links; backlink question → `expect_tool: vault_backlinks`.
     - Section edit: `vault_section` / `vault_move_lines` on a multi-section page → assert post-turn `workspace_files` shows other sections unchanged (uses PR-A's #352).
   - Use `setup.workspace_files` to seed vault pages; use `embeddings_fixture` for the search-distractor case.
   - Every test bounded: `max_tool_calls` + `max_tool_errors`.

4. `chore(evals): tighten memory.yaml + memory-multi-turn.yaml` — closes #348.
   - Rename `memory.yaml` #7 ("uses think tool for complex question") to reflect that there is no `think` tool — likely "synthesizes from multiple memories for a complex question" or similar.
   - Add `max_tool_calls` + `max_tool_errors` bounds to every test that lacks them.
   - For each list-form `response_contains` whose name implies AND, convert to `response_contains_all` (now available from PR-A). Tests that legitimately want OR keep `response_contains` and the test name should reflect that.
   - Smoke run against default model: existing pass rate ≥ pre-renovation baseline.

### Validation

- `vault.yaml` tests pass against the default model.
- `memory.yaml` + `memory-multi-turn.yaml` pass rate ≥ pre-renovation baseline.
- No reference to `memory_search` / `memory_recent` / `memory_save` / `think` anywhere in `evals/`.
- If `vault.yaml` exposes real LLM-behavior gaps (likely — `vault_backlinks` and section tools are nuanced), file each as its own issue and move on. Do not block PR-B on fixes.

### Risk

- **Section-edit assertion depends on PR-A's `workspace_files` checker.** PR-B is gated on PR-A merging first.
- **Distractor fixture may need expansion.** `cat-facts-embeddings.db` was built for cat trivia; if vault_search test needs a non-cat distractor floor, build a small fixture as part of PR-B or use enough distractor memories inline.

---

## PR-C — Tool-selection coverage sweep (6 issues, all S)

Closes: **#430** (tool-deferral evals), **#344** (deferral context budget), **#340** (workspace tool selection), **#341** (shell approval flow), **#343** (delegate decision), **#342** (conversation post-compaction recall).

### Commits (split into C1 + C2 if review fatigue is real)

1. `feat(evals): tool-deferral.yaml — assert tool_search fetch + no-fetch + pre-emptive promotion` — closes #430 + #344.
   - Deferred-tool fetch: prompt requiring a deferred tool (e.g. a vault_show_sections-like one) → `expect_tool: tool_search`, then the fetched tool.
   - No-fetch on already-loaded tools: prompt satisfiable from critical-priority tools → `expect_no_tool: tool_search`.
   - Pre-emptive promotion: keyword in prompt matches a deferred tool's description → tool is pre-promoted; agent calls it without `tool_search` → `expect_tool: <the-tool>`, `expect_no_tool: tool_search`.
   - Near-miss promotion: tool-description-overlap case borrowed from `evals/tool_choice/` if any of those overlaps make sense as full-turn evals (mostly they don't — keep them in tool_choice).

2. `feat(evals): workspace-tools.yaml — glob vs search vs read vs move selection` — closes #340.
   - "Find any `.py` files in src/" → `expect_tool: workspace_glob`, `expect_no_tool: [workspace_search, workspace_read]`.
   - "Find code that uses `asyncio.Lock`" → `expect_tool: workspace_search`.
   - "Show me the file at `src/foo/bar.py`" → `expect_tool: workspace_read`, `expect_no_tool: [workspace_search, workspace_glob]`.
   - "Rename `old.py` to `new.py`" → `expect_tool: workspace_move`, `expect_no_tool: [workspace_read, workspace_write, workspace_delete]`.
   - Sandbox-refusal prompt → response narrates refusal or sandboxed error message; no escape.

3. `feat(evals): shell.yaml — auto_confirm paths + allowlist bypass + background lifecycle` — closes #341.
   - `auto_confirm: true`, simple command → response contains stdout.
   - `auto_confirm: false`, agent denied → response narrates denial; `max_tool_calls` bounded to prevent retry-storm; `expect_no_tool: shell` on subsequent turns (multi-turn).
   - `shell_patterns` allowlist bypass → command runs; no confirmation request (assert via `expect_no_tool: request_confirmation` once that tool name is the right thing to check, otherwise via tool-call timing in the response).
   - Background lifecycle: start → status → stop in a single multi-turn test, each turn asserting on the right tool.

4. `feat(evals): delegate.yaml — delegate-decision only` — closes #343.
   - Good-candidate task ("Read 5 vault pages and produce a one-paragraph summary") → `expect_tool: delegate_task`.
   - Bad-candidate task ("Set my favorite color to blue") → `expect_no_tool: delegate_task`.

5. `feat(evals): conversation.yaml — post-compaction recall + search-choice` — closes #342.
   - Post-compaction recall: multi-turn test using `setup.conversation_history` (PR-A #353) to seed a long pre-existing history that triggers compaction. Final turn asks about a fact established early. `response_contains` the salient fact.
   - Search-choice: fact known to be outside the active window → `expect_tool: conversation_search`.

### Validation

- Each new file passes against the default model.
- `make eval` against the default model: aggregate pass rate ≥ 80% (allowing for real LLM-behavior gaps that get filed as issues).

### Risk

- **Pre-emptive promotion test (#344) is fragile** — depends on `preempt_search.py` keyword catalog. May need to seed a custom keyword to make the test reliable; if so, prefer a fixture over a real tool.
- **Conversation history seed test depends on PR-A merging first** (same as section-edit test in PR-B).

---

## PR-D — Pass-rate trend tracking (1 issue, S)

Closes: **#351** (pass-rate trend across runs).

### Commits

1. `feat(eval): append per-run summary to evals/history.jsonl + make eval-history report` — closes #351.
   - `src/decafclaw/eval/runner.py::run_eval`: at end of run, append a per-run record to `evals/history.jsonl` — `{timestamp, model, total, passed, failed, duration_sec, total_tokens, per_file: {name: {passed, total}}}`.
   - `evals/history.jsonl` checked into git (the detail bundles stay in the gitignored `evals/results/`).
   - New CLI flag `--history` (or `make eval-history`) renders a stdout table over the last N runs: timestamp, model, pass-rate, delta vs. previous.
   - `docs/eval-loop.md`: short section on history + how to read the trend.
   - `tests/test_eval_history.py`: appends to a temp file, renders the table.

### Validation

- After PR-D merges, run `make eval` once and verify `evals/history.jsonl` grows by one record.
- `make eval-history` table prints sanely.

### Out of scope

- **HTML dashboard**, **CI integration**, **alerting on regressions** — file as P3 follow-ups if anyone asks.

---

## End-of-session retro

Tracked in `notes.md` once all PRs land. Sections to fill:

- Before/after pass rates per file.
- Real behavior gaps surfaced + which got filed as issues.
- Tool-description tweaks made along the way (with before/after impact if known).
- Any harness limitations that bit us → file as follow-ups.
