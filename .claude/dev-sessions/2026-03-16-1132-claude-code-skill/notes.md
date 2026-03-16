# Claude Code Skill — Notes

## Session Log

- **2026-03-16 11:32** — Session started. Brainstorming Claude Code SDK integration as a DecafClaw skill.
- **2026-03-16 12:00** — Spec and plan completed.
- **2026-03-16 12:05** — Execution started.

## SDK API Findings (Phase 1)

**Package:** `claude-code-sdk` v0.0.25 (NOT `claude-agent-sdk` — research was wrong on name)

**Two API patterns:**
1. `query(prompt, options)` — one-shot, returns `AsyncIterator[Message]`. Supports `resume=session_id` for multi-turn and `continue_conversation=True`.
2. `ClaudeSDKClient` — persistent client with connect/query/receive_response/disconnect.

**Decision:** Use `query()` + `resume` pattern instead of `ClaudeSDKClient`. Simpler — no connection lifecycle, session state lives on Claude's side. Each `claude_code_send` is an independent `query()` call that resumes the previous session.

**Key types:**
- `ClaudeCodeOptions` — main config: `cwd`, `model`, `can_use_tool`, `permission_mode`, `resume`, `continue_conversation`, `max_turns`, `allowed_tools`, `disallowed_tools`, `env`
- `can_use_tool: Callable[[str, dict, ToolPermissionContext], Awaitable[PermissionResultAllow | PermissionResultDeny]]` — async callback, takes tool name + input dict + context
- `PermissionResultAllow(behavior="allow", updated_input=None)` / `PermissionResultDeny(behavior="deny", message="")`
- `ResultMessage` — has `session_id`, `total_cost_usd`, `duration_ms`, `result`, `is_error`, `num_turns`
- `AssistantMessage` — has `content: list[TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock]`, `model`
- `ToolUseBlock` — has `id`, `name`, `input`
- `ToolResultBlock` — has `tool_use_id`, `content`, `is_error`
- `PermissionMode` — `Literal['default', 'acceptEdits', 'plan', 'bypassPermissions']`

**No `max_budget_usd` on options** — budget control not directly available. We'll need to track cost from ResultMessage and enforce on our side.

**Spec adjustment:** Session management simplifies significantly. Session = stored session_id + metadata. No client lifecycle. `claude_code_stop` just forgets the session_id. Idle expiration = same (forget the ID). `shutdown()` hook becomes unnecessary for now.
