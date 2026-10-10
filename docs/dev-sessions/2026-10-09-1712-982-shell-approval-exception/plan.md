# Plan: Offer to add a shell auto-approval exception after a DECLINED command is approved (#982)

## Phase 1: Core Aux LLM Prompt & Parsing
- [ ] Update `build_aux_approval_prompt` in `src/decafclaw/tools/shell_tools.py`:
  - Request `suggested_rule`: string or null in the JSON format.
  - Instruct the model to formulate a concise natural-language exception rule if `auto_approve` is false, specifying under what conditions this command/class of commands is acceptable in this repo.
- [ ] In `check_shell_approval`:
  - Parse `suggested_rule` and `reason` from the response.
  - Pass `decline_reason` and `suggested_rule` into `request_confirmation(...)` when falling through after aux LLM denial.
  - On approval result, if `result.get("add_rule")` and `result.get("rule")`:
    - Add `result["rule"]` to `ctx.tools.aux_approval_guidance` (if not already present).
    - Log addition.
- [ ] Write unit tests for `build_aux_approval_prompt` and `check_shell_approval` parsing in `tests/test_shell_approval.py`.

## Phase 2: Confirmation Types, Manager & Transports Plumbing
- [ ] `src/decafclaw/confirmations.py`:
  - Add `add_rule: bool = False` and `rule: str = ""` to `ConfirmationResponse`.
  - Update `to_archive_message()` and `from_archive_message()` in `ConfirmationResponse`.
- [ ] `src/decafclaw/conversation_manager.py`:
  - Update `respond_to_confirmation` signature to accept `add_rule: bool = False, rule: str = ""`.
  - Pass `add_rule` and `rule` to `ConfirmationResponse(...)`.
- [ ] `src/decafclaw/tools/confirmation.py`:
  - In `_request_via_manager`, forward `decline_reason` and `suggested_rule` into `action_data`.
  - Convert `ConfirmationResponse` to tool result dict including `add_rule` and `rule`.
  - In `_request_via_event_bus` (legacy), support `add_rule` and `rule` in event.
- [ ] `src/decafclaw/web/message_types.json`:
  - In `confirm_request`: add optional `decline_reason: "string?"` and `suggested_rule: "string?"`.
  - In `confirm_response`: add optional `add_rule: "boolean?"` and `rule: "string?"`.
  - Run `make gen-message-types`.
- [ ] `src/decafclaw/web/websocket.py`:
  - In `_confirmation_to_dict`, include `decline_reason` and `suggested_rule` from `action_data`.
  - In `_handle_confirm_response`, pass `add_rule` and `rule` to `manager.respond_to_confirmation` and event bus fallback.
- [ ] `src/decafclaw/http_server.py`:
  - In `handle_confirm`, handle `action == "add_rule"`: `approved=True, add_rule=True, rule=rule`.
  - Include result label `📝 Approved + rule added`.

## Phase 3: Mattermost UI & Emoji Polling
- [ ] `src/decafclaw/mattermost_ui.py`:
  - In `build_confirm_buttons`:
    - Accept `decline_reason=""` and `suggested_rule=""`.
    - If `tool_name == "shell"` and `suggested_rule` is present, add an interactive button:
      `{"id": "addrule", "name": "Approve + remember why", "style": "default", "integration": {"url": ..., "context": {..., "action": "add_rule", "rule": suggested_rule}}}`
- [ ] `src/decafclaw/mattermost_display.py`:
  - In `on_confirm_request`:
    - Accept `decline_reason=""` and `suggested_rule=""`.
    - If `decline_reason`: display `\n*Declined: {decline_reason}*` in post text.
    - If `suggested_rule`: display `\n*Suggested rule:* `{suggested_rule}`` in post text.
    - Emoji prompt: add `:memo: approve + remember why` if `suggested_rule` is present.
- [ ] `src/decafclaw/mattermost.py`:
  - In `_poll_confirmation_manager`:
    - Support emoji `"memo"` / `"pencil2"`: `await _resolve(True, add_rule=True, rule=suggested_rule, label="📝 approved + rule added")`.
  - In `handle_stream_event` for `confirmation_request`: pass `decline_reason` and `suggested_rule` to `on_confirm_request` and `_poll_confirmation_manager`.

## Phase 4: Web UI Components & Styling
- [ ] `src/decafclaw/web/static/lib/tool-status-store.js`:
  - Pass `decline_reason` and `suggested_rule` from `CONFIRM_REQUEST` into the pending confirm object.
  - Support `add_rule` and `rule` in `respondToConfirm`.
- [ ] `src/decafclaw/web/static/components/confirm-view.js`:
  - If `c.decline_reason`: render a notice / warning showing why the reviewer declined the command.
  - If `c.suggested_rule`:
    - Maintain input state for the rule (editable by user, defaulted to `c.suggested_rule`).
    - Render a text input field for the rule.
    - Render an **"Approve + remember why"** button.
    - Clicking it calls `#handleConfirm(..., true, { add_rule: true, rule: editedRuleValue })`.
- [ ] `src/decafclaw/web/static/styles/confirm-view.css`:
  - Style the decline notice and suggested-rule input cleanly using Pico CSS variables.

## Phase 5: Verification & Documentation
- [ ] Unit & integration tests:
  - Test aux LLM prompt formatting & response parsing (`test_shell_approval.py`).
  - Test confirmation round-trip with `add_rule` and `rule` in manager and websocket (`test_web_confirm.py`, `test_web_websocket_confirm_response.py`).
  - Test HTTP callback handler for `add_rule` (`test_http_server.py`).
  - Test Mattermost button and emoji dispatch (`test_mattermost_ui.py`, `test_mattermost_display.py`, `test_mattermost.py`).
  - Test JS client components (`confirm-view.test.js`, `tool-status-store.test.js`).
  - Test end-to-end: command declined -> approved with "Approve + remember why" -> rule added to `ctx.tools.aux_approval_guidance` -> subsequent command evaluated with new rule.
- [ ] Run `make check`, `make test-js`, `make test`.
- [ ] Update `docs/tools.md` to document the "Approve + remember why" exception flow.
- [ ] Update dev-session `notes.md`.
