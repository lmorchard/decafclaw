# Code Quality Review — Session Notes

**Started:** 2026-03-28
**Branch:** `refactor/code-quality-review`
**PR:** https://github.com/lmorchard/decafclaw/pull/161

## Recap

Full codebase quality review and refactoring session. Started with a comprehensive review of all Python and JS code under `src/`, then systematically addressed findings across 4 phases plus a follow-up Context redesign.

### Phase 1: Quick Wins
- Extracted `renderMarkdown()` to shared `lib/markdown.js` (fixed wiki link rendering bug)
- Extracted `formatTime()` to shared `lib/utils.js`
- Extracted `estimate_tokens()` to shared `util.py` (was duplicated in 4 modules)
- Merged `TerminalMediaHandler` + `WebMediaHandler` → `LocalFileMediaHandler`
- Fixed naive datetimes in `conversations.py` (now UTC)
- Added JSONL error handling in `archive.py` (skip corrupt lines)
- Replaced unbounded regex cache with `lru_cache` in `agent.py`

### Phase 2: Duplication Cleanup
- Extracted `_file_error()` helper in `workspace_tools.py` (13 catch blocks consolidated)
- Added `_ga()` uniform accessor in `mcp_client.py` (eliminates dict-vs-object branching)
- Consolidated reindex functions in `embeddings.py` with `_reindex_entries()`
- Split `_get_db()` into `_init_schema()` + `_migrate_legacy()`
- Extracted `setupResizeHandle()` to `lib/utils.js`
- Extracted `#renderConversationItem()` in `conversation-sidebar.js`
- Added `@_authenticated` decorator in `http_server.py`

### Phase 3: Decomposition
- Decomposed `run_agent_turn()`: extracted `_setup_turn_state()`, `_prepare_messages()`, `_handle_reflection()`
- Decomposed `compact_history()`: extracted `_partition_turns()`, `_determine_compaction_mode()`, `_rebuild_history()`
- Decomposed `_process_conversation()`: extracted `_prepare_conversation()`
- Refactored `_subscribe_progress()` from elif chain to dispatch dict
- Created `polling.py` with shared `run_polling_loop()` + `build_task_preamble()`
- Added `Context.for_task()` factory method
- Moved `restore_history()` to `archive.py` as public function

### Phase 4: Architecture & Consistency
- Split `ConversationStore` into `MessageStore` + `ToolStatusStore` sub-stores
- Added deferred parsing to `MarkdownDocument` (batch edits reparse once)
- Audited tool return types (wrapped error paths in `ToolResult`)
- Added explanatory comments to magic numbers
- Extracted checkbox constants in `todos.py`

### Module Extractions
- Extracted `interactive_terminal.py` from `agent.py` (170 lines)
- Extracted `mattermost_display.py` from `mattermost.py` (383 lines)
- `agent.py`: 1080 → 920 lines, `mattermost.py`: 1340 → 973 lines

### Context Redesign (Wave 2)
- Grouped Context attributes into `TokenUsage`, `ToolState`, `SkillState` dataclasses
- Kept conversation identity fields (`conv_id`, `user_id`, etc.) flat
- Updated 26 files, 0 old-style references remaining
- Updated `fork_for_tool_call()` with explicit copy semantics

### PR Review Fixes
- Added `UnicodeDecodeError` to 4 `read_text()` catch blocks
- Standardized auth error message
- Fixed misleading comments and docstrings
- Removed dead overlap-protection code in `polling.py`

## Divergences from Plan

1. **Skipped circular import resolution (4.3)** — Only 4 deferred imports in function bodies. Decided the idiomatic Python pattern was fine and restructuring would be churn.

2. **Skipped JS private method naming (4.7)** — High risk of silent Lit template breakage for purely cosmetic benefit. Filed as lmorchard/decafclaw#162 instead.

3. **Context redesign was Option B, not spec's original design** — Spec proposed grouping all 31 attributes including conversation fields. After counting 126+ references, we debated options and chose to only group the internal machinery (tokens, tools, skills = 80 refs) while keeping the 5 most-accessed conversation fields flat. Right call.

4. **Module extractions weren't in the original spec** — `interactive_terminal.py` and `mattermost_display.py` emerged from a mid-session discussion about whether to break large modules into directories. Decided targeted extractions were better than directory restructuring.

5. **Didn't pull from main before starting** — Wiki links feature had been merged to main but we branched from stale local main. Caused rebase conflicts and we tolerated pre-existing test failures for most of the session until Les caught it. Lesson: always `git fetch && git rebase origin/main` before starting.

## Key Insights & Lessons Learned

1. **Always pull from main before starting a session.** We carried 7 "pre-existing" test failures through the entire session that were actually just missing code from an unmerged branch. Wasted mental overhead dismissing them repeatedly.

2. **Zero tolerance for warnings and failures is the right policy.** The pyright warnings about `ReflectionResult` typed as `object` and the `str | ToolResult` issue in commands.py were real type safety gaps, not noise.

3. **Verify spec claims against actual code before planning.** The initial review agents overstated several findings (20+ elif branches was actually 11, 34 attributes was 31, wiki page resolution "duplication" wasn't real). Self-review pass caught these at 61% accuracy rate.

4. **Parallel sub-agents work well for mechanical refactoring.** Phases 2-4 each launched 3-5 agents in parallel for independent changes. All produced working code that merged cleanly. Good pattern for high-confidence, well-scoped changes.

5. **Present options honestly when there's a real tradeoff.** The A/B/C options for Context redesign led to a better outcome (Option B) than if we'd just plowed ahead with the spec's original design.

6. **PR review caught real issues.** The `UnicodeDecodeError` gaps in workspace_tools were genuine bugs — binary files would crash tool calls. The dead `tick_running` code and misleading docstrings were quality issues worth fixing.

## Stats

- **10 commits** on the branch
- **61 files changed**, +3,461 / -1,935 lines
- **833 tests passing**, 0 errors, 0 warnings
- **~35 conversation turns**
- **New modules:** `util.py`, `polling.py`, `interactive_terminal.py`, `mattermost_display.py`, `conversation_display.py` (renamed to `mattermost_display.py`), `lib/markdown.js`, `lib/utils.js`, `lib/message-store.js`, `lib/tool-status-store.js`
- **1 issue filed:** lmorchard/decafclaw#162 (JS private naming)

## Efficiency Observations

- The review phase (6 parallel agents reading the codebase) was the most expensive part but produced the foundation for everything else.
- Phases 1-2 were fast — simple extractions and find-replace patterns.
- Phase 3 (decomposition) was the riskiest but agents handled it well with clear prompts about what to extract.
- The Context redesign (26-file mechanical update) went smoothly with 4 parallel agents partitioned by concern (agent.py, tools/, mattermost+web, tests).
- Biggest time sink: the rebase conflict resolution after discovering we hadn't pulled from main.

## Process Improvements

1. **Add `git fetch origin && git log --oneline main..origin/main` to session startup checklist.** Would have caught the stale main immediately.
2. **Run `make test` at the very start of a session** to establish baseline. If anything fails, fix it before doing new work.
3. **When spec claims specific line numbers/counts, spot-check a sample before planning.** The 61% accuracy on first pass wasted some planning effort.
