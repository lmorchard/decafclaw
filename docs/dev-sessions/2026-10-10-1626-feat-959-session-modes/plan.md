# Conversation Modes Implementation Plan

**Goal:** Implement switchable session modes that bundle shell auto-approval presets and promoted situational tool profiles into a cohesive abstraction across server, agent, websocket protocol, and web UI.

**Approach:** Define `SessionMode` in `src/decafclaw/modes.py` with built-in `default`, `dev`, `research`, and `admin` modes, configurable via `config.modes`. Promote situational tools in `classify_tools` by prioritizing them at the front of `normal`. Expose mode switching to both agent (`shell_guidance(action="set_mode")`) and user (sidebar dropdown via `set_mode` websocket message).

**Tech stack:** Python 3.13, Starlette/WebSockets, Lit (Web Components), Pico CSS, Pytest, Vitest.

---

## Phase 1: Mode Data Model, Configuration, and Tool Classification Integration

Define `SessionMode`, the built-in mode registry, and integrate tool promotion into `classify_tools`.

**Files:**
- Create: `src/decafclaw/modes.py`
- Modify: `src/decafclaw/config_types.py` — add `SessionModeConfig` (or use `SessionMode`)
- Modify: `src/decafclaw/config.py` — parse `modes` in `load_config`, store on `Config`
- Modify: `src/decafclaw/tools/tool_registry.py` — update `classify_tools` to accept `promoted_tools` and prioritize them at front of `normal`
- Modify: `src/decafclaw/tool_definitions.py` — pass `promoted_tools` to `classify_tools` in `build_tool_list`
- Modify: `src/decafclaw/context_composer.py` — pass `promoted_tools` to `classify_tools` in `_compose_tools`
- Test: `tests/test_modes.py`
- Test: `tests/test_tool_registry.py`

**Key changes:**
```python
# src/decafclaw/modes.py
@dataclass
class SessionMode:
    name: str
    description: str
    presets: list[str] = field(default_factory=list)
    promoted_tools: list[str] = field(default_factory=list)

DEFAULT_MODES: dict[str, SessionMode] = {
    "default": SessionMode(
        name="default",
        description="Standard assistance loadout and baseline shell confirmation",
        presets=[],
        promoted_tools=[],
    ),
    "dev": SessionMode(
        name="dev",
        description="Software engineering and pairing",
        presets=["developer", "github"],
        promoted_tools=[
            "shell",
            "workspace_diff",
            "workspace_search",
            "workspace_glob",
            "workspace_list",
            "workspace_edit",
        ],
    ),
    "research": SessionMode(
        name="research",
        description="Deep knowledge and web research",
        presets=[],
        promoted_tools=[
            "web_fetch",
            "tabstack_research",
            "tabstack_extract_markdown",
            "vault_search",
            "vault_recent",
            "vault_tags",
            "vault_journal_append",
        ],
    ),
    "admin": SessionMode(
        name="admin",
        description="Agent management and diagnostics",
        presets=[],
        promoted_tools=[
            "admin_read",
            "admin_list",
            "admin_edit",
            "admin_write",
            "mcp_status",
            "health_status",
            "heartbeat_trigger",
        ],
    ),
}

def get_all_modes(config: "Config | None" = None) -> dict[str, SessionMode]:
    modes = {k: replace(v) for k, v in DEFAULT_MODES.items()}
    if config and getattr(config, "modes", None):
        modes.update(config.modes)
    return modes

def resolve_active_mode(config: "Config | None", mode_name: str = "") -> SessionMode:
    all_modes = get_all_modes(config)
    return all_modes.get(mode_name or "default", DEFAULT_MODES["default"])
```

In `classify_tools`:
```python
def classify_tools(
    all_tool_defs: list[dict],
    config: Config,
    fetched_names: set[str] | None = None,
    skill_tool_names: set[str] | None = None,
    preempt_matches: set[str] | None = None,
    promoted_tools: list[str] | set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
...
    # Partition normal tools into promoted first, then remaining normal
    if promoted_tools:
        promoted_set = set(promoted_tools)
        promoted_order = {name: i for i, name in enumerate(promoted_tools)}
        p_normal = [td for td in normal if td.get("function", {}).get("name") in promoted_set]
        # Also promote any low tools that are in promoted_tools
        p_low = [td for td in low if td.get("function", {}).get("name") in promoted_set]
        low = [td for td in low if td.get("function", {}).get("name") not in promoted_set]
        p_all = p_normal + p_low
        p_all.sort(key=lambda td: promoted_order.get(td.get("function", {}).get("name"), 999))
        rem_normal = [td for td in normal if td.get("function", {}).get("name") not in promoted_set]
        normal = p_all + rem_normal
```

**Verification — automated:**
- [ ] `uv run pytest tests/test_modes.py tests/test_tool_registry.py` passes
- [ ] `uv run ruff check src/ tests/` passes
- [ ] `uv run pyright` passes

**Verification — manual:**
- [ ] Verify `dev` mode orders `workspace_diff` and `workspace_search` ahead of general normal tools.

---

## Phase 2: Shell Guidance Tool and Conversation State Integration

Add conversation-scoped mode persistence and agent-triggered mode transitions via `shell_guidance`.

**Files:**
- Modify: `src/decafclaw/context.py` — add `active_mode: str = "default"` to `Context`
- Modify: `src/decafclaw/conversation_manager.py` — add `active_mode` to `PersistedTurnState`, `_PERSISTED_BINDINGS`, `_CTX_DRIVEN_FIELDS`
- Modify: `src/decafclaw/tools/shell_tools.py` — add `mode` param and `action="set_mode"` to `tool_shell_guidance`
- Test: `tests/test_shell_guidance_modes.py`
- Test: `tests/test_conversation_manager.py`

**Key changes:**
In `PersistedTurnState`:
```python
active_mode: str = "default"
```
In `_PERSISTED_BINDINGS`:
```python
"active_mode": (
    lambda ctx: getattr(ctx, "active_mode", "default") or "default",
    lambda ctx, v: setattr(ctx, "active_mode", v or "default"),
),
```
In `_CTX_DRIVEN_FIELDS`:
Include `"active_mode"`.

In `tool_shell_guidance`:
```python
async def tool_shell_guidance(
    ctx: "Context",
    action: str = "list",
    preset: str = "",
    rule: str = "",
    mode: str = "",
) -> str | ToolResult:
```
When `action == "set_mode"`:
- Validate `mode` against `get_all_modes(ctx.config)`.
- If unattended, reject.
- Confirm via `request_confirmation` with message explaining auto-approval presets and promoted tools.
- Set `ctx.active_mode = mode`.
- Update `ctx.tools.active_aux_approval_presets = list(target_mode.presets)`.
- Clear `ctx.tools.llm_approved_shell_patterns.clear()`.
- Emit `mode_changed` event via `emit_for_ctx(ctx)`.

**Verification — automated:**
- [ ] `uv run pytest tests/test_shell_guidance_modes.py tests/test_conversation_manager.py` passes
- [ ] `uv run ruff check src/ tests/` passes
- [ ] `uv run pyright` passes

**Verification — manual:**
- [ ] Verify `action="list"` displays available modes and active mode.
- [ ] Verify `action="set_mode"` triggers confirmation prompt and updates presets.

---

## Phase 3: WebSocket Protocol & Server Handlers

Define WebSocket messages, handle `set_mode`, broadcast `mode_changed`, and supply mode info in `conv_history`.

**Files:**
- Modify: `src/decafclaw/web/message_types.json` — add `set_mode`, `mode_changed`, update `conv_history`
- Regenerate: run `uv run python scripts/gen_message_types.py`
- Modify: `src/decafclaw/web/websocket.py`:
  - `_handle_load_history`: inject `active_mode` and `available_modes` into `conv_history` response.
  - `_handle_set_mode`: handle client `set_mode`, reject if busy, update manager state and emit `mode_changed`.
  - `on_conv_event`: forward `mode_changed` event.
- Test: `tests/test_websocket_modes.py`

**Key changes:**
In `message_types.json`:
```json
"set_mode": {
  "direction": "client_to_server",
  "description": "Change the active mode for a conversation.",
  "fields": {"conv_id": "string", "mode": "string"}
},
"mode_changed": {
  "direction": "server_to_client",
  "description": "The active mode for a conversation changed.",
  "fields": {"conv_id": "string", "mode": "string", "presets": "array of string", "promoted_tools": "array of string"}
}
```
In `conv_history`: add `"active_mode": "string?"`, `"available_modes": "array of object?"`.

In `websocket.py`:
```python
async def _handle_set_mode(ws_send: WSSendCallable, index, username, msg, state) -> None:
    conv_id = msg.get("conv_id")
    mode_name = msg.get("mode")
    manager = state.get("manager")
    config = state.get("config")
    all_modes = get_all_modes(config)
    if mode_name not in all_modes:
        await ws_send({"type": WSMessageType.ERROR, "message": f"Unknown mode: {mode_name}", "conv_id": conv_id})
        return
    if manager:
        conv_state = manager.get_state(conv_id)
        if conv_state and conv_state.busy:
            await ws_send({"type": WSMessageType.ERROR, "message": "Cannot change mode while conversation is busy", "conv_id": conv_id})
            return
        manager.set_flag(conv_id, "active_mode", mode_name)
        mode = all_modes[mode_name]
        manager.set_flag(conv_id, "active_aux_approval_presets", list(mode.presets))
        manager.set_flag(conv_id, "llm_approved_shell_patterns", [])
        await manager.emit(conv_id, {
            "type": WSMessageType.MODE_CHANGED,
            "conv_id": conv_id,
            "mode": mode_name,
            "presets": list(mode.presets),
            "promoted_tools": list(mode.promoted_tools),
        })
```

**Verification — automated:**
- [ ] `make check-message-types` passes
- [ ] `uv run pytest tests/test_websocket_modes.py` passes
- [ ] `uv run pyright` passes

**Verification — manual:**
- [ ] Verify `load_history` sends active_mode and available_modes.
- [ ] Verify `set_mode` rejects when `conv_state.busy` is True.

---

## Phase 4: Web UI Frontend Mode Selector & Live Synchronization

Add mode selector dropdown to conversation sidebar, wire to `ConversationStore`, handle live `mode_changed` events.

**Files:**
- Modify: `src/decafclaw/web/static/lib/conversation-store.js` — track `#activeMode` and `#availableModes`, add `setMode(mode)`, handle `MODE_CHANGED` and `CONV_HISTORY`
- Modify: `src/decafclaw/web/static/components/conversation-sidebar.js` — render mode selector above model picker, bind `@change`, sync in `updated()`
- Test: `src/decafclaw/web/static/lib/conversation-store.test.js`
- Test: `src/decafclaw/web/static/components/conversation-sidebar.test.js`

**Key changes:**
In `conversation-sidebar.js`:
```html
<div class="mode-picker">
  <label class="mode-picker-label" for="mode-select">Mode</label>
  <select id="mode-select" class="mode-select"
          @change=${(e) => this.#handleModeChange(e)}>
    ${this._activeMode === 'custom' ? html`<option value="custom" disabled selected>Custom</option>` : nothing}
    ${this._availableModes.map(m => html`
      <option value="${m.name}" ?selected=${m.name === this._activeMode} .selected=${m.name === this._activeMode} title="${m.description}">
        ${m.name}
      </option>
    `)}
  </select>
</div>
```

**Verification — automated:**
- [ ] `npm test` / `make test-js` passes
- [ ] `make check-js` passes

**Verification — manual:**
- [ ] Mode selector renders above Model picker in sidebar.
- [ ] Switching mode in dropdown calls `setMode`.
- [ ] If custom presets applied, dropdown displays "Custom".

---

## Phase 5: Documentation & End-to-End Verification

Update documentation and run full test suites.

**Files:**
- Modify: `docs/tools.md` — document session modes, built-ins, and `config.modes`
- Modify: `docs/context-composer.md` — document mode-driven tool promotion in `classify_tools`
- Modify: `docs/dev-sessions/2026-10-10-1626-feat-959-session-modes/notes.md` — document implementation notes and learnings

**Verification — automated:**
- [ ] `make check` passes cleanly (install-js, check-message-types, fmt-check, lint, typecheck, check-js, check-browser-assets)
- [ ] `make test` passes (4400+ unit tests)
- [ ] `make test-js` passes

**Verification — manual:**
- [ ] Verify docs accurately describe the four built-in modes and tool promotion behavior.
