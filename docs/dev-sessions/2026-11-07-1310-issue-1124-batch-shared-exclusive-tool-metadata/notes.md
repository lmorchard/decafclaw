# issue-1124-batch-shared-exclusive-tool-metadata

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1124
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1124-batch-shared-exclusive-tool-metadata
- Branch: issue-1124-batch-shared-exclusive-tool-metadata
- Base: origin/main at 828fc782d9f19882338047992ef64ac98265af26
- Next action: commit, push, open PR (see Log for state).

## Implementation summary

- `src/decafclaw/tool_execution.py`:
  - `EXEC_SHARED` / `EXEC_EXCLUSIVE` constants + module comment.
  - `_resolve_execution_policy(ctx, fn_name)`: resolution order = explicit `execution` key → `writes` flag (`False`→shared, `True`→exclusive) → shared for resolvable-but-unannotated (contrib skill / MCP tools); `None` when the name resolves in no source. Sources checked in `execute_tool` dispatch order: skill extras → core registry → MCP.
  - `execute_tool_calls`: per-call policies resolved up front; any unresolvable name → whole batch serial (every call exclusive, in order). Otherwise a dependency graph: for each pair (earlier j, later i), an edge j→i when either is exclusive — so a shared call overlaps other shared calls, while any exclusive call waits for all preceding and blocks all later. Calls with no predecessors start immediately; each completed call sets its successors' start events in a `finally`, so exceptions and cancellation release barriers. Global semaphore + per-call ctx forks unchanged; results collected in assistant call order via `gather`.
- `tests/test_agent_turn.py` (5 new tests, all event/barrier-based, no fixed sleeps):
  - `shared_calls_overlap` — two shared calls overlap within the limit.
  - `exclusive_waits_for_preceding_and_blocks_later` — web_fetch(shared) → shell(exclusive, `writes: true`) → web_fetch(shared); asserts order via a slow-first-call event handshake.
  - `exception_releases_scheduling_barrier` — failing exclusive call still unblocks the later call.
  - `cancellation_releases_scheduling_barrier` — mid-batch cancel (ctx.cancelled) unblocks a call waiting on the barrier; returns the cancelled result.
  - `policy_resolution_failure_falls_back_to_serial` — unknown tool names → serial batch, order kept.
- Docs: `docs/tools.md` new "Batch execution policy" section (tool-author doc); CLAUDE.md tool-concurrency bullet updated.

## Log

- Burndown: #1123 delivered via draft PR #1236 (marked ready, head 3b3de85b, In review); moved to next Ready issue #1124, moved to In progress.
- Implemented batch scheduler + 4 tests; baseline `tests/test_agent_turn.py` passed 59/59.
- **Hang investigation (Nov 8):** `exclusive_waits...` hung under pytest but a standalone repro of the same body passed 5/5. Root cause: the worktree copy of that test was missing its `with patch("decafclaw.tool_execution.execute_tool", side_effect=_batch_tool):` block (a mangled "script insert" from the Nov 7 session) — so it executed the REAL tools, and the real confirmation-gated `shell` tool waited forever for user approval. A correct version of the test (with patch + `wait_for(task, timeout=15)`) exists in the main clone's uncommitted changes. Fixed the worktree test to match; all 63 tests pass in ~4s.
- Added the cancellation-barrier test (acceptance: "exceptions and cancellation release scheduling barriers"); 64/64 pass.
- Verified no regressions: related execution tests (agent loop breaker, widgets, attachments, completion status, process_tool_media, responses history, skills, steering, telemetry) 256 passed; confirmations + per-file mutation locks (test_confirmation, test_workspace_tools, test_workspace_concurrency, test_web_confirm) 204 passed. `make fmt` / `make lint` / `make typecheck` clean.
- **Main clone hygiene issue (needs Les's call):** the main clone (branch `issue-1224-copy-menu-position-fix`) has uncommitted changes that look like work done outside a worktree: `src/decafclaw/tools/delegate.py` (+73, #1123 strict-validation work), `tests/test_delegate.py` (+119), and `tests/test_agent_turn.py` (+121, the #1124 tests incl. the correct exclusive test). Need to determine whether these duplicate committed work (PR #1236 / this branch) or are unique, then restore the main clone to a clean state.
