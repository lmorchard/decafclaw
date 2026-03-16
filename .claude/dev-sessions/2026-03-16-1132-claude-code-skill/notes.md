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

## Implementation Progress

- **Phase 1** — SDK installed (claude-code-sdk 0.0.25), skeleton created, API verified
- **Phase 2** — SessionManager with 18 tests (create, expire, stop, list, budget clamping)
- **Phase 3** — Permission bridge with 11 tests (auto-approve, allowlist, confirmation flow)
- **Phase 4** — Output logger with 9 tests (JSONL logging, metric tracking, summaries)
- **Phase 5** — All four tools wired up, config fields added, shutdown hook capture
- **Phase 6** — Merged into earlier phases (SKILL.md in Phase 1, config in Phase 5)
- **Phase 7** — Docs updated. Ready for live testing.

## Architecture Decisions

1. **`query()` + `resume` over `ClaudeSDKClient`** — each `claude_code_send` is an independent SDK call that resumes via session_id. No persistent connection to manage. Simpler error handling, no connection lifecycle.
2. **Budget enforcement on our side** — SDK has no `max_budget_usd`. We track `total_cost_usd` from `ResultMessage` and refuse new sends when budget exhausted.
3. **Lazy expiration** — no background timer. Sessions are checked for expiry on access. Simplest correct approach.
