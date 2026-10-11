# Conversation Modes: Privilege Presets and Situational Tool Profiles Spec

**Goal:** Unify conversation privilege modes with situational tool profiles into a switchable `SessionMode` that configures shell auto-approval presets and promotes situational tools into active slots.

**Source:** https://github.com/lmorchard/decafclaw/issues/959

## Current state

Today, shell auto-approval presets (`shell_guidance`, `src/decafclaw/tools/shell_tools.py:350-410`) and tool priority classification (`classify_tools`, `src/decafclaw/tools/tool_registry.py:104-227`) are disjoint mechanisms.
- Preset activation is toggled individually via `shell_guidance(action="enable_preset"|"disable_preset", preset=...)`.
- Tool priority is determined statically by tool definitions (`Priority.CRITICAL`, `Priority.NORMAL`, `Priority.LOW`) and pre-emptive keyword search matches.
- There is no visible mode indicator or selector in the web UI sidebar, and no mechanism to bundle shell approval presets together with a promoted tool cluster for specific workflows (like software engineering vs. deep web research).

## Desired end state

1. **`SessionMode` dataclass and Registry** (`src/decafclaw/modes.py`):
   ```python
   @dataclass
   class SessionMode:
       name: str
       description: str
       presets: list[str] = field(default_factory=list)
       promoted_tools: list[str] = field(default_factory=list)
   ```
   Built-in modes:
   - `default`: standard assistance loadout, baseline shell confirmation (`presets=[]`, `promoted_tools=[]`).
   - `dev`: software engineering & pairing (`presets=["developer", "github"]`, `promoted_tools=["shell", "workspace_diff", "workspace_search", "workspace_glob", "workspace_list", "workspace_edit"]`).
   - `research`: deep knowledge & web research (`presets=[]`, `promoted_tools=["web_fetch", "tabstack_research", "tabstack_extract_markdown", "vault_search", "vault_recent", "vault_tags", "vault_journal_append"]`).
   - `admin`: agent management & diagnostics (`presets=[]`, `promoted_tools=["admin_read", "admin_list", "admin_edit", "admin_write", "mcp_status", "health_status", "heartbeat_trigger"]`).
   Users can define custom modes or override built-in modes in `config.json` under `modes: { "<name>": { "description": "...", "presets": [...], "promoted_tools": [...] } }`.

2. **Tool Priority Integration** (`src/decafclaw/tools/tool_registry.py`):
   - `classify_tools` accepts `promoted_tools: list[str] | set[str] | None = None`.
   - Tools in `promoted_tools` that are not already `critical` are promoted to the front of `normal` (in `promoted_tools` order), ensuring they claim available active tool budget before unpromoted normal and low tools.

3. **Shell Approval Integration & Mode Switching**:
   - Applying a mode sets `ctx.tools.active_aux_approval_presets = list(mode.presets)`.
   - If narrowing presets (or when changing modes), cached LLM-approved shell patterns (`llm_approved_shell_patterns`) are cleared.
   - Mode state is persisted per conversation via `PersistedTurnState.active_mode` in `src/decafclaw/conversation_manager.py`.

4. **Agent Action**:
   - `shell_guidance(action="set_mode", mode="<name>")` allows the agent to request a mode change.
   - Requires user confirmation via `request_confirmation` with a message explaining the mode shift (both auto-approvals and promoted tool profile).
   - Denied on unattended turns (`ctx.is_unattended`).
   - Emits `mode_changed` event to the conversation.

5. **WebSocket Wire Protocol** (`src/decafclaw/web/message_types.json`):
   - `set_mode`: Client -> Server `{"conv_id": "string", "mode": "string"}`.
   - `mode_changed`: Server -> Client `{"conv_id": "string", "mode": "string", "presets": "array of string", "promoted_tools": "array of string"}`.
   - `conv_history` adds `active_mode: string?`, `available_modes: array of object?`.
   - Server rejects `set_mode` if conversation is busy with an in-flight turn.

6. **Web UI** (`conversation-sidebar.js` and `conversation-store.js`):
   - Mode `<select id="mode-select">` placed in the sidebar directly above the Model picker.
   - Shows active mode; if presets do not match any known mode, shows a disabled `Custom` option.
   - Changing the dropdown calls `store.setMode(mode)`.
   - Updates live when `mode_changed` is received over WebSocket.

## Design decisions

- **Decision:** Place `SessionMode` and mode resolution helpers in `src/decafclaw/modes.py`.
  - **Why:** Avoids circular imports between `config_types`, `tool_registry`, `shell_tools`, and `conversation_manager`.
  - **Rejected:** Inlining in `shell_tools.py` (which would entangle tool classification with shell execution).
- **Decision:** Support mode transitions from the agent via `shell_guidance(action="set_mode", mode="...")`.
  - **Why:** Extends the existing tool that already manages presets and confirmation flows; keeps the tool count lean rather than adding another tool definition to the prompt budget.
  - **Rejected:** Creating a dedicated `set_mode` tool (which would occupy an extra tool slot in every conversation's prompt).
- **Decision:** Promote situational tools by ordering them at the front of `normal` in `classify_tools`.
  - **Why:** Keeps `critical` strictly reserved for the core safety net and indispensable system tools, while ensuring situational tools get top priority when filling the active budget.
  - **Rejected:** Forcing promoted tools into `critical` (which could cause hard budget overflows if multiple tools are promoted).
- **Decision:** Detect "Custom" mode in UI when active presets diverge from the active mode's preset bundle.
  - **Why:** Prevents misleading the user if custom presets or individual `enable_preset`/`disable_preset` calls modified the conversation state.
  - **Rejected:** Silently showing the named mode even when permissions diverge.

## Patterns to follow

- Dataclass and Config deserialization: `src/decafclaw/config.py:376-388` (`_load_model_configs` pattern for dicts of dataclasses).
- Per-conversation persisted state: `src/decafclaw/conversation_manager.py:148-225` (`PersistedTurnState`, `_PERSISTED_BINDINGS`, `_CTX_DRIVEN_FIELDS`, `test_persisted_field_bindings_exhaustive`).
- Confirmation-gated tool actions: `src/decafclaw/tools/shell_tools.py:954-963`.
- WebSocket message definition and dispatch: `src/decafclaw/web/message_types.json` and `src/decafclaw/web/websocket.py:630-652`.
- Lit UI dropdown and state synchronization: `src/decafclaw/web/static/components/conversation-sidebar.js:641-662` (Model picker `<select>` with `?selected` attribute and `updated()` synchronization).

## What we're NOT doing

- Repo-path scoping (`/dev-session start <repo>`) and teardown checks (explicit non-goal per issue description).
- Mattermost display / `!mode` command (tracked in child issue #1001).
- TUI client changes (explicitly excluded).
- Modifying custom guidance rules (`add_rule`) on mode change (custom rules remain intact).

## Open questions

- **Question:** How should the server respond if `set_mode` requests an unknown mode name?
  - **Default:** Send WebSocket error message `f"Unknown mode: {mode_name}"` and leave the active mode unchanged.
- **Question:** Should `default_mode` be a top-level config setting?
  - **Default:** Default is `"default"`, overridable if `config.default_mode` is present, but standard baseline is `"default"`.
