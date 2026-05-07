# Codebase research: vault write boundary + confirmation patterns

## 1. Vault write/delete/rename boundary

**Helpers** (`src/decafclaw/skills/vault/tools.py`):
- `_vault_root(config)` → `config.vault_root` — line 22
- `_agent_dir(config)` → `config.vault_agent_dir` — line 26
- `_is_in_agent_dir(config, path)` — line 114, returns `path.is_relative_to(config.vault_agent_dir)`
- `_safe_write_path(config, page)` — line 86, strips `.md`, rejects `..` / absolute prefixes, resolves under vault root

**Boundary enforcement (current behavior — hard refuse outside agent dir):**
- `tool_vault_write` line 162; refusal at line 171: `"only pages under the agent folder may be written"`
- `tool_vault_delete` line 195; refusal at line 204: `"only pages under the agent folder may be deleted"`
- `tool_vault_rename` line 233; refusals at lines 248 and 251

**Config:**
- `config.vault_root` from `config.vault.vault_path` (default `workspace/vault/`)
- `config.vault_agent_dir` from `config.vault.agent_folder` (default `vault_root/agent/`)
- `vault_agent_journal_dir = vault_agent_dir/journal/`
- `vault_agent_pages_dir = vault_agent_dir/pages/`

## 2. Vault skill self-description

**SKILL.md (`src/decafclaw/skills/vault/SKILL.md`):**
- Frontmatter: `always-loaded: true` (lines 1-4)
- "Your Home Folder" section (lines 11-18) — `agent/pages/` and `agent/journal/`
- Line 18 (verbatim): "Write to `agent/` by default. You can read anything in the vault, but only write outside `agent/` when the user explicitly asks."
- Boundaries section (lines 46-52): line 52: "The user's files are readable but not yours to modify autonomously. Only edit user files when asked."

**SKILL.md says one thing; runtime says another.** SKILL.md tells the LLM "write outside `agent/` when the user explicitly asks." The runtime in `tools.py:171` hard-refuses regardless of user request. This is the observable mismatch the session targets.

**Tool descriptions (`src/decafclaw/skills/vault/tools.py`):**
- `vault_write` description (lines 834-843): "Writes are restricted to the agent folder (agent/pages/, agent/journal/); admin and user pages are off-limits."
- `vault_delete` description (lines 869-875): "Only pages under the agent folder ... may be deleted; admin and user pages are off-limits."
- `vault_rename` description (lines 896-902): "Agent-owned pages only ... target must also land under the agent folder."

## 3. EndTurnConfirm pattern

**Definition (`src/decafclaw/media.py:18-33`):**
```python
@dataclass
class EndTurnConfirm:
    message: str = ""
    approve_label: str = "Approve"
    deny_label: str = "Deny"
    on_approve: Callable[[], Any] | None = None
    on_deny: Callable[[], Any] | None = None
```

**Tool returns it on `ToolResult.end_turn`.** Agent loop:
1. Collects `end_turn_signal` after tool execution (`agent.py:814-819`)
2. Final no-tools LLM "presentation" call to render reasoning (`agent.py:1175-1183`)
3. `_handle_end_turn_confirm()` calls `request_confirmation()` (`agent.py:1186`)
4. `on_approve` / `on_deny` callback invoked — sync or async (`agent.py:1189-1204`)
5. History gets injected user note: `[User approved: ...]` or `[User denied: ...]` (`agent.py:1194-1196`)
6. Loop continues (approved) or ends (denied)

**Persistence (`src/decafclaw/confirmations.py`):**
- `ConfirmationRequest` (lines 24-38): `action_type`, `action_data`, `message`, `approve_label`, `deny_label`, `tool_call_id`, `timeout`, `confirmation_id`, `timestamp`
- `ConfirmationResponse` (lines 64-76): `confirmation_id`, `approved`, `always`, `add_pattern`, `data`, `timestamp`
- `to_archive_message()` / `from_archive_message()` serialize as `role: "confirmation_request"` / `role: "confirmation_response"` — survives page reload + server restart

## 4. Email allowlist + confirmation fallthrough

**`src/decafclaw/tools/email_tools.py`:**
- `_recipient_allowed(addr, allowlist)` (lines 31-58) — exact match or `@domain.com` suffix
- `_all_recipients_allowed(recipients, allowlist)` — all-or-nothing
- `check_email_approval(ctx, recipients, subject, body, ...)` (lines 133-158):
  - All recipients allowlisted → auto-approve, no confirmation
  - Otherwise → builds preview message, calls `request_confirmation()`
- Preview format (lines 116-130):
  ```
  Email to: alice@example.com, bob@example.com
  Subject: Meeting Notes
  2 attachment(s) (512.5 KB)
  ---
  <body preview, first 200 chars>
  ```
- On denial: `return ToolResult(text="[error: email send was denied by user]")`

**Allowlist sources unioned:** `ctx.config.email.allowed_recipients + ctx.tools.preapproved_email_recipients` (per-task frontmatter).

## 5. Tool parameter conventions

- `TOOL_DEFINITIONS` is OpenAI-shape JSON schema. Required args in `required: []`; optional via omission.
- **No `force=true` / opt-in bypass flag pattern in the codebase today.** Boundary checks are server-side.
- Closest precedent for "user explicitly opting in": email's per-task `email-recipients` frontmatter (config-level), not a per-call arg.
- Enum-style discriminator pattern for action variants exists (`tool_shell_patterns(action="list"|"add"|"remove")` at `shell_tools.py:162`).

## Class-of-bug analogues (per brainstorm framing)

The user's prompt names `vault_write`. The same boundary in the same file gates `vault_delete` and `vault_rename`. Whatever the design here is, those two are in scope to consider — even if the decision is "leave them as hard-refuse".
