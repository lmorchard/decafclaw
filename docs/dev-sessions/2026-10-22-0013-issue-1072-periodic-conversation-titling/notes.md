# issue-1072-periodic-conversation-titling

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1072
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1072-periodic-conversation-titling
- Branch: issue-1072-periodic-conversation-titling
- Base: origin/main at ff99d3f23337218287cc8b31f844161cc42ac9ff
- Port: HTTP_PORT=18996 (added to worktree .env)
- Next action: implementation in flight — see Log.

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
  Turn number = count of `role == "user"` messages in the archive.
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
- Baseline `make test` (pre-change): 4658 passed, 2 skipped.
- `make install-js` ok. Baseline `make test-js`: green.
- Implementation complete across: conversations.py (flag + guarded rename),
  http_server.py (REST rename manual=True), auto_titling.py (new),
  websocket.py (trigger + forwarder + subscribe), message_types.json +
  regenerated artifacts, conversation-store.js (CONV_RENAMED handling),
  tests: test_web_conversations.py (+4 index tests), test_web_auto_titling.py
  (new, 14 tests), conversation-store.test.js (+4 tests).
- Targeted JS run after paren fix: 49 files / 540 tests passed.
- Targeted Python run: tests/test_web_auto_titling.py + tests/test_web_conversations.py (result pending).
- Remaining: `make check` gate, full `make test` re-run, self-review, commit
  checkpoint, publish draft PR (handoff authorizes), final self-review + push.
