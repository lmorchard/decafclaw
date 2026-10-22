# issue-1072-periodic-conversation-titling

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1072
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1072-periodic-conversation-titling
- Branch: issue-1072-periodic-conversation-titling
- Base: origin/main at ff99d3f23337218287cc8b31f844161cc42ac9ff
- Port: HTTP_PORT=18996 (added to worktree .env)
- Next action: review corrections applied on the same branch; PR re-published for re-review.

## Decisions

- `custom_title: bool = False` added to `ConversationMeta`; `ConversationIndex.rename(conv_id, title, *, manual=False)`:
  - `manual=True` (user rename): sets `custom_title=True`, applies unconditionally.
  - `manual=False` (automated): refused (returns None) when `custom_title` is set.
- `custom_title` is deliberately NOT in `to_dict()` — existing tests pin the
  REST response shape (`{conv_id, title, created_at, updated_at}`) and the
  generated OpenAPI client; the flag is internal server state.
- Auto-titling lives in the web layer (`src/decafclaw/web/auto_titling.py`),
  not the manager: `ConversationIndex` is web-only and the manager must stay
  transport-agnostic. Trigger = post-turn hook in the websocket `turn_complete`
  branch (fire-and-forget `asyncio.create_task`), which the spec allows ("or
  post-turn hook").
- Cadence `TITLING_TURNS = (2, 5, 10, 15, 20)` (turn 2, then every 5, up to 20).
  Turn number = count of `role == "user"` archive messages, excluding
  `source == "widget_response"` synthetics (real user turns only).
- Review correction (F1): `maybe_auto_title` now runs the cheap index guards
  before any archive read (`index.get`, `custom_title` check), and a
  per-process `_exhausted` sentinel records convs whose last cadence point
  was attempted (or whose count already passed turn 20) so later turns
  skip the turn count entirely; the sentinel resets on restart.
- Model: the configured default model via `decafclaw.llm.call_llm` (default is
  the fast `gemini-2.5-flash`); no hard-coded model name.
- Dedup: module-level `_attempted: dict[conv_id, set[turn]]`; check-and-reserve
  is sync (no await) so it is atomic per event loop — multiple tabs of one
  conv fire one LLM call per cadence point.
- Notification: new server→client wire type `conv_renamed`
  (message_types.json + `make gen-message-types`), published on the global
  event bus by the titling task; per-socket forwarder
  `_make_conv_renamed_forwarder` (prefix `web-{username}-` is the cross-user
  guard, matching the conversation_status forwarder pattern);
  `ConversationStore` patches `#conversations` in place + re-sorts + emits
  change (no refetch).
- LLM call goes through `llm.call_llm` attribute lookup (`from .. import llm`)
  so the `stub_llm` fixture (monkeypatches `decafclaw.llm.call_llm`) applies.

## Log

- 2026-10-22: worktree created (uv sync ok), .env copied, HTTP_PORT=18996.
- Baseline `make test` (pre-change): 4658 passed, 2 skipped. `make install-js` ok.
- Implementation: conversations.py (flag + guarded rename), http_server.py
  (REST rename manual=True), auto_titling.py (new), websocket.py (trigger +
  forwarder + subscribe), message_types.json + regenerated artifacts,
  conversation-store.js (CONV_RENAMED handling), tests: test_web_conversations.py
  (+4 index tests), test_web_auto_titling.py (new, 20 tests),
  conversation-store.test.js (+4 tests).
- Test-fix note: `stub_llm` shares the test's monkeypatch instance, so
  `monkeypatch.undo()` in the websocket-hook test was restoring the real LLM
  seam before the background task ran (real HTTP attempt, swallowed by the
  fail-open path) — undo moved after `asyncio.gather`.
- Commit 57ba8354 published as draft PR #1132 (verify-commit --on BRANCH and
  --pr-head both clean).
- `make check` on committed head: green (npm policy OK, message-types
  regenerate+diff clean, fmt-check 445 files, ruff clean, pyright 0 errors,
  gen-api-client + tsc clean, static module graph 2 passed).
- `make test-js`: 49 files / 540 tests passed.
- Full `make test` on final head: 4682 passed, 2 skipped.
- CI caught the TUI exhaustive-switch break (check-tui): added `conv_renamed`
  no-op case + test to tui/src/dispatcher.ts (a72d9b21). CI then fully green
  on a72d9b21 (lint-and-test, js-test, check-tui all passed).
- Status: implementation complete, draft PR open (left draft per handoff).
  Next: independent local review (same-model, user-approved exception), then
  `pr ready` (PR left open for the user's review; no merge).
- 2026-10-22: review corrections (independent local review of head cd0fec6e,
  same-model, user-approved exception): F1 guard-order + `_exhausted`
  sentinel in auto_titling.py; turn-count excludes widget_response
  synthetics; docs/web-ui.md gained the auto-titling / custom_title /
  conv_renamed section; +5 tests in test_web_auto_titling.py. Notes 1 and 4
  settled by the coordinator (no code change). Re-pushed to PR #1132.
- 2026-10-22: review feedback addressed for PR #1132:
  1. Prefix collision fix: replaced `.startswith(f"web-{username}-")` with
     `is_web_conv_for_user(conv_id, username)` parsing full username portion
     without suffix, preventing cross-user event leakage between colliding
     usernames like `alice` and `alice-team`.
  2. Synthetic messages excluded from turn count: marked reflection retries
     (`source: "reflection_retry"`) in `agent.py` and system update notices
     (`source: "system_update"`) in `context_composer.py`; excluded synthetic
     user messages from `count_user_turns` and `_history_text` in `auto_titling.py`.
  3. Out-of-order cadence protection: added `_latest_cadence` tracking in
     `auto_titling.py` to discard superseded older cadence results when
     cadence passes resolve in reverse order.
  4. Added regression tests across `test_web_auto_titling.py`,
     `test_web_conversations.py`, and `test_web_websocket_conversation_status.py`.
