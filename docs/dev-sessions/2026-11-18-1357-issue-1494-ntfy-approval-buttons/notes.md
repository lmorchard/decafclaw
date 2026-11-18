# issue-1494-ntfy-approval-buttons

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1494
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1494-ntfy-approval-buttons
- Branch: issue-1494-ntfy-approval-buttons
- Base: origin/main at 4de8da3384a9e80d5ffb5a95c97ba6007116aebc
- Next action: read the issue and start.

## Log

## Findings (base 4de8da33)

- No ntfy support exists yet (`rg -i ntfy src/` empty).
- Confirmation flow: managers emit `confirmation_request` to per-conv subscribers only. A global
  bus event is published only when there are no subscribers AND `last_transport == "mattermost"`
  (`conversation_manager._emit_confirmation_request`). An ntfy adapter subscribed to the global bus
  would miss nearly every confirmation, so the emit must publish a global event always (with
  `conv_id`) and keep the Mattermost extras on the existing fallback path.
- Single-use callback tokens live in `mattermost_ui.get_token_registry()` (module-global
  `ConfirmTokenRegistry`); `http_server.handle_confirm` consumes the token and routes through
  `manager.respond_to_confirmation(conv_id, confirmation_id, ...)` when both ids ride in the token.
- `handle_confirm` currently calls `await request.json()` unconditionally: an empty POST body from
  an ntfy `action=http` callback would raise and 500. Must tolerate empty/non-JSON/non-dict bodies.
- HTTP client dep: httpx already a runtime dependency.
- Notification channel pattern: `make_<ch>_adapter(config)` handler filters event type, resolves
  config per event, formats, then `asyncio.create_task(_deliver(...))`.

## Plan

1. `NtfyChannelConfig` (enabled/server_url/topic/access_token[secret]/min_priority/
   notify_on_confirmation) added to `NotificationsChannelsConfig`.
2. `notification_channels/ntfy.py`: payload builders (notification + confirmation with
   Approve/Deny http actions minting single-use tokens and an Open Session view action), bearer
   header helper, httpx delivery, adapter handler.
3. Register adapter in `init_notification_channels` (guard: enabled + topic + http(s) server_url).
4. Publish `confirmation_request` on the global bus always, keeping Mattermost-specific fields only
   on the no-subscriber/Mattermost fallback.
5. `handle_confirm`: tolerate empty/non-JSON/non-dict bodies.
6. Tests: payload construction, headers, action tokens -> confirm endpoint -> manager resolution,
   empty-body handling, adapter filters, init guard.
7. Docs: notifications.md + config.md tables.

## Implementation status (all seven steps done)

- Widget confirmations (`action_type == "widget_response"`) are skipped: Approve/Deny cannot carry
  widget data, mirroring the Mattermost adapter.
- Confirmation tokens are only minted when `http.enabled` and the event carries `conv_id` +
  `confirmation_id`.
- Tests added: `tests/test_notification_channels_ntfy.py` (26 cases), empty/non-JSON body and
  manager-routing cases in `tests/test_http_server.py`, global-bus-with-subscriber case in
  `tests/test_conversation_manager.py`, init guard cases in `tests/test_notification_channels_init.py`,
  config defaults/JSON-load cases in `tests/test_config.py`.

## Checks (base 4de8da33 + working tree)

- `uv run pytest tests/test_config.py tests/test_notification_channels_*.py tests/test_http_server.py tests/test_conversation_manager.py -q` → 276+ passed.
- `make fmt` → clean. `make lint typecheck` → 0 errors.
- `make check` → passed (fmt-check, lint, typecheck, JS checks/gen).
- `make test` → 5833 passed, 2 skipped in 33.31s.

