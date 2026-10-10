# Notes: Offer to add a shell auto-approval exception after a DECLINED command is approved (#982)

## What Was Done

Implemented the end-to-end "Approve + remember why" exception flow for shell commands that are declined by the auxiliary auto-approval reviewer:

1. **Auxiliary Reviewer Prompt & Parsing**:
   - Updated `build_aux_approval_prompt` in `src/decafclaw/tools/shell_tools.py` to instruct the model to return a `suggested_rule` when `auto_approve` is false, phrased as a concise natural-language exception rule for acceptable use in the current repository.
   - In `check_shell_approval`, captured `reason` and `suggested_rule` and passed them into `request_confirmation(...)`.
   - On approval response with `add_rule: True`, added the (potentially user-edited) rule to `ctx.tools.aux_approval_guidance`.

2. **Confirmation Data Structures & Persistence**:
   - Added `add_rule: bool = False` and `rule: str = ""` to `ConfirmationResponse` in `src/decafclaw/confirmations.py`.
   - Updated JSONL archive serialization (`to_archive_message` / `from_archive_message`).
   - Extended `respond_to_confirmation` in `src/decafclaw/conversation_manager.py` to accept and record `add_rule` and `rule`.

3. **Web Gateway & UI**:
   - Updated `src/decafclaw/web/message_types.json` with `decline_reason` and `suggested_rule` on `confirm_request`, and `add_rule` and `rule` on `confirm_response`. Ran `make gen-message-types`.
   - Updated `src/decafclaw/web/websocket.py` to forward decline and rule fields between client, manager, and event bus.
   - Updated `confirm-view.js` and `tool-status-store.js` in the Web UI:
     - Rendered reviewer decline reason clearly above action buttons.
     - Provided an editable text input pre-filled with `suggested_rule`.
     - Provided an **"Approve + remember why"** button that submits `add_rule: true` with the edited rule string.
   - Styled the decline callout and rule box in `confirm-view.css`.

4. **Mattermost Integration**:
   - Updated `mattermost_display.py` to show reviewer decline reason and suggested rule in the confirmation post text.
   - Added emoji reaction instructions: `:memo:` (`memo` or `pencil2`) to approve and remember the rule.
   - In `mattermost_ui.py`, added interactive button `Approve + remember why` (`id: "addrule"`).
   - In `mattermost.py`, handled `memo`/`pencil2` emoji reactions and `add_rule` HTTP action callbacks.

5. **Relation to #1026**:
   - Issue #1026 proposed using approval history to inform future auto-approvals or suggest approval rules.
   - #982 implements the explicit human-in-the-loop version of this: when a declined command is approved, an explicit rebuttal rule is drafted by the aux model, reviewed/edited by the user, and saved to the conversation's active guidance for subsequent commands.

## Verification
- `make check`: passed (install-js, check-message-types, fmt-check, lint, typecheck, check-js, check-browser-assets).
- `make test-js`: 479 passed.
- `make test`: 4,319 passed.
- All new tests added:
  - `tests/test_shell_approval.py`: `test_aux_llm_declined_passes_suggested_rule_and_applies_on_approval`
  - `tests/test_web_confirm.py`: `test_confirmation_to_dict_includes_decline_reason_and_suggested_rule`, `test_request_via_manager_forwards_decline_and_rule_fields`
  - `tests/test_web_websocket_confirm_response.py`: `test_add_rule_forwarded_to_manager`
  - `tests/test_http_server.py`: `test_confirm_add_rule`, `test_shell_buttons_with_suggested_rule`, `test_poll_confirmation_manager_resolves_memo_emoji_with_rule`
  - `tests/test_shell_approval_guidance.py`: `test_exception_rule_added_on_approval_surfaces_in_subsequent_aux_evaluations`
  - `src/decafclaw/web/static/components/confirm-view.test.js`: decline reason, editable rule, and Approve + remember why interaction test.
