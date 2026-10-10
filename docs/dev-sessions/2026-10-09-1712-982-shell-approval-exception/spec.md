# Spec: Offer to add a shell auto-approval exception after a DECLINED command is approved (#982)

## Motivation and Goal

When the auxiliary LLM evaluates a shell command and declines auto-approval, the user currently receives a confirmation dialog with "Approve", "Deny", and either "Allow Pattern: <glob>" or "Always". Glob patterns are brittle and dangerous for multi-purpose CLI commands or complex arguments (e.g. `gh pr create` or `python script.py`), and they cannot express contextual intent or constraints.

The goal of #982 is to allow the user to approve a declined command while capturing an explicit, per-conversation natural-language exception rule (`shell_guidance`) drafted as a rebuttal to the decline reason. Future commands within the same conversation will have this exception rule included in the aux approval prompt, allowing similar constrained actions to be auto-approved without manual intervention.

## Key Decisions (from Issue #982 Triage & Interview)

1. **Auxiliary Reviewer Drafts the Rule**:
   - In `build_aux_approval_prompt`, when instructing the model on output JSON format:
     `{"auto_approve": bool, "reason": "<string>", "risk": "low" | "medium" | "high", "suggested_rule": "<string or null>"}`
   - The prompt instructs: if `auto_approve` is false, formulate a concise exception rule (`suggested_rule`) phrased as a constrained condition under which this command (or this class of operation in this repo) is acceptable (e.g. `"Auto-approve gh pr create and git push during PR workflow in this repo"`). If auto-approved or no specific rule makes sense, `suggested_rule` is null or omitted.
   - Benefit: zero additional LLM latency or token overhead during declines.

2. **Confirmation Action & UI Flow**:
   - The decline reason and suggested rule are passed into `request_confirmation` via `action_data` (and wire events: `decline_reason`, `suggested_rule`).
   - If `suggested_rule` is present, the confirmation prompt presents:
     - The decline reason (e.g. `Declined: <reason>`)
     - An editable input for the rule (Web UI) pre-filled with `suggested_rule`.
     - A third button: **"Approve + remember why"** (or in Mattermost: an interactive button with that label, plus emoji `:memo:` / `:pencil2:` support).
   - If `suggested_rule` is absent, the prompt displays the decline reason (if any) and standard choices without the "Approve + remember why" button.

3. **User Editing of the Rule**:
   - **Web UI**: The user can inspect the decline reason, review the drafted rule in an input field, optionally edit it, and click "Approve + remember why". Clicking this sends `approved: true`, `add_rule: true`, and `rule: "<edited rule text>"`.
   - **Mattermost**: Buttons cannot have inline text edits. Mattermost renders the decline reason and drafted rule in the post text, and clicking "Approve + remember why" saves the rule exactly as drafted.

4. **Saving the Rule**:
   - The rule is strictly scoped to the current conversation (added to `ctx.tools.aux_approval_guidance`). It does NOT write to persistent disk storage.
   - When "Approve + remember why" is chosen:
     - The command is executed (`approved = True`).
     - The rule (drafted or edited) is appended to `ctx.tools.aux_approval_guidance`.
     - `ctx.tools.llm_approved_shell_patterns` (session cache) remains valid for the conversation.
   - Future commands in that conversation evaluate against `resolve_aux_approval_guidance(ctx)`, which now includes the new rule.

5. **Confirmation Serialization & Bridge**:
   - `ConfirmationResponse`: add optional fields `add_rule: bool = False`, `rule: str = ""`.
   - Update `to_archive_message` / `from_archive_message` to serialize and deserialize them safely.
   - In `conversation_manager.respond_to_confirmation`, accept `add_rule: bool = False`, `rule: str = ""` and forward to `ConfirmationResponse`.
   - In `web/websocket.py` and `http_server.py`, pass `add_rule` and `rule` to `manager.respond_to_confirmation`.
   - In `web/message_types.json`, add `add_rule` and `rule` to `confirm_response` and `decline_reason` / `suggested_rule` to `confirm_request`. Run `make gen-message-types`.

6. **Relation to #1026**:
   - Issue #1026 asks whether approval history can inform future approvals or suggest approval rules.
   - #982 directly delivers this mechanism by converting manual overrides of declines into active conversation guidance rules.

## Acceptance Criteria

- When the aux reviewer declines a command, its JSON response includes `reason` and `suggested_rule`.
- `check_shell_approval` passes `decline_reason` and `suggested_rule` to `request_confirmation`.
- `request_confirmation` propagates `decline_reason` and `suggested_rule` into `action_data` of `ConfirmationRequest` and websocket wire payloads.
- The confirmation card in Web UI shows the decline reason and an editable text box for the suggested rule, along with an "Approve + remember why" button.
- Mattermost post shows the decline reason and suggested rule, with an "Approve + remember why" action button and `:memo:` emoji option.
- Responding with "Approve + remember why" executes the command AND adds the rule (as edited or as drafted) to `ctx.tools.aux_approval_guidance`.
- Subsequent shell evaluations in the same conversation include the new rule in the aux LLM prompt.
- If the reviewer provides no `suggested_rule`, "Approve + remember why" is not shown.
- All existing approve, deny, and allow pattern choices work unchanged.
- Full test coverage for reviewer parsing, fallback behavior, rule editing in Web UI, Mattermost button/emoji flow, and prompt inclusion on subsequent commands.
