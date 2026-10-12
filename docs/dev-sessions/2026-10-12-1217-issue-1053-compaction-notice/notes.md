# Issue #1053 — single shared compaction notice across Python transports

## Resume here

- **Goal:** extract the compaction_end notice text into one helper, `compaction_notice(...)`, in a new module `src/decafclaw/compaction_display.py`; route the Mattermost and interactive-terminal transports through it; add per-transport regression tests that pin the notice string. Web store + TUI stay UNCHANGED.
- **Issue:** https://github.com/lmorchard/decafclaw/issues/1053 (board: In progress, moved 2026-10-12 from Backlog)
- **Worktree:** `.claude/worktrees/issue-1053-compaction-notice` (absolute: `/Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1053-compaction-notice`)
- **Branch:** `issue-1053-compaction-notice`, base `origin/main` @ `b9d21206eaa5719191d150689a7b405f6968a1f5`
- **Confirmed decisions (user-settled, do not reopen):**
  1. New module `src/decafclaw/compaction_display.py`, ONE function `compaction_notice(success, ...)` returning the notice text (not the two-function split the issue also floated).
  2. Route Mattermost `compaction_end` handler (`mattermost.py`) + `interactive_terminal.py` through it.
  3. Transport-specific prefixes (Mattermost `📦 ` emoji, terminal `  [ ]` brackets) stay OUTSIDE the helper; caller composes prefix + helper result.
  4. Web store (`web/static/lib/message-store.js`) and TUI (`tui/src/dispatcher.ts`) UNCHANGED.
  5. Per-transport regression tests (mattermost + terminal) that pin the notice string.
- **Authorization limits:** implement + publish draft PR in this session. NOT authorized: mark ready, request review, merge. Flow endpoint: PR review follow-up (handled by coordinator after publish). Review will be an independent local review in a fresh context by the coordinator (same model config — user-approved exception).
- **Latest checks:** baseline `make test` on b9d21206: 4512 passed, 2 skipped. Checkpoint 1 (helper + Mattermost route + tests): targeted pytest 45 passed; `make fmt` (2 new test files reformatted) + `make lint` clean.
- **Next action:** commit checkpoint 1, publish draft PR, then terminal route (commit 2).

## Findings

- Event source: `compaction.py:648-655` publishes `compaction_end` with `success` (bool), `before_messages`, `after_messages`, `elapsed_sec` (round 1), `estimated_tokens_before`, `estimated_tokens_after`.
- Five rendering sites at base b9d2120:
  - `mattermost.py:620-652` — failure `📦 Compaction failed — conversation unchanged`; success `📦 Conversation compacted ({duration}{, N → M messages}{, ~T tokens compacted})`; duration `<60s → "{elapsed:.0f}s"` else `"{elapsed/60:.1f}m"`.
  - `interactive_terminal.py:134-141` — failure `  [compaction failed — conversation unchanged]`; success `  [compaction complete]` (no details).
  - `web/static/lib/message-store.js:222-225` — OUT OF SCOPE (decision 4).
  - `tui/src/dispatcher.ts:150-153` — OUT OF SCOPE (decision 4).
  - `web/websocket.py:1160-1172` — forwards fields only, no text; no change.
- Existing tests: `tests/test_interactive_terminal.py:72-96` pins loose substrings (`"compaction failed" in out`, `"compaction complete" in out`) — these WILL FAIL after the refactor (capitalization + wording change) and are the tests to tighten into string-pinning per decision 5.
- Terminal behavior change (intended): success line becomes `  [Conversation compacted (12s, 42 → 10 messages, ~50,000 tokens compacted)]`; failure line capitalizes `Compaction`.
- Test seams:
  - Terminal: `tests/test_interactive_terminal.py::_capture_on_event` drives `run_interactive`'s `on_event` closure with capsys.
  - Mattermost: no existing seam for `_ensure_subscribed`; pattern from `tests/test_mattermost_attachments.py:17-27` (`MattermostClient.__new__` + mocked attrs). `_ensure_subscribed(mm_state, conv_id, channel_id, root_id, app_ctx, manager)` builds the `on_manager_event` closure and ends with `manager.subscribe(conv_id, cb)` — capture `cb` from a fake manager, feed `compaction_start` (needs `client.send` → post id) then `compaction_end`, assert `client.edit_message` args. `app_ctx` needs `.config.reflection.visibility` (fixture `config` has it via `ReflectionConfig` default `"hidden"`).
  - `config` fixture (root conftest) works for the mattermost seam.

## Decisions

- Helper signature (routine, from issue proposal + decision 1): `compaction_notice(success, before=0, after=0, elapsed=0.0, est_tokens=0) -> str`. Failure check is `success is False` (preserves every existing transport's `event.get("success", True) is False` semantics exactly: missing/None flag → success notice). Callers pass the raw `event.get("success", True)` and keyword detail args.
- Shared wording = Mattermost's current wording (sentence case, em dash): failure `Compaction failed — conversation unchanged`; success `Conversation compacted ({details})`. Terminal's old lowercase/short wording is replaced by the shared one — that is the unification the issue asks for.
- Transport tests pin the LITERAL user-visible string (not `prefix + compaction_notice(...)`), so both drift without the helper AND an unreviewed wording change surface as a per-file mismatch (issue: "single source of wording ... reviewable per file"). Helper unit tests pin the same literals in `tests/test_compaction_display.py`.
- Commit split: (1) helper + Mattermost route + both new test files + notes; (2) terminal route + terminal test tightening + notes. Draft PR published after commit 1; final commits pushed to same draft before returning.

## Attempts and checks

- Baseline `make test` (worktree, branch @ b9d21206, no changes): 4512 passed, 2 skipped, 33.58s. Clean.
- Checkpoint 1 targeted: `uv run pytest tests/test_compaction_display.py tests/test_mattermost_compaction.py tests/test_mattermost_attachments.py tests/test_mattermost_display.py -n 0` → 45 passed. Re-run after `make fmt` → 10 passed (the two new files).
- `make fmt`: 2 files reformatted (the two new test files); `make lint`: all checks passed.
- Note: the Mattermost transport test pins a string the pre-refactor code already produced (pure move of wording) — expected for a behavior-preserving refactor; it pins the string against future drift. The terminal test (commit 2) is the one that fails pre-change (wording actually changes there).
