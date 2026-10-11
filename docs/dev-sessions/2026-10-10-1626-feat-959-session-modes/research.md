# Research: Conversation Modes (Issue #959)

## 1. Shell Presets and Approval Guidance
- `src/decafclaw/tools/shell_tools.py:350-410`: Built-in presets `developer` and `github`. `_get_all_presets()` merges built-in with `config.shell.aux_approval_presets`.
- `src/decafclaw/tools/shell_tools.py:529-580`: `resolve_aux_approval_guidance(ctx)` resolves active preset names from `ctx.config.shell.active_aux_approval_presets` and `ctx.tools.active_aux_approval_presets` minus `ctx.tools.disabled_aux_approval_presets`.
- `src/decafclaw/tools/shell_tools.py:881-1050`: `tool_shell_guidance(ctx, action, preset, rule)` implements `list`, `enable_preset`, `disable_preset`, `add_rule`, `remove_rule`, `save_preset`.
  - Confirmation-gated via `request_confirmation(ctx, tool_name="shell_guidance", ...)`.
  - Disallowed on unattended turns (`if ctx.is_unattended: ...`).
  - When narrowing presets (`disable_preset`), cache is flushed: `ctx.tools.llm_approved_shell_patterns.clear()`.

## 2. Tool Classification and Priority
- `src/decafclaw/tools/tool_registry.py:104-227`: `classify_tools(all_tool_defs, config, fetched_names, skill_tool_names, preempt_matches)`.
  - Splits tools into `critical`, `normal`, `low`, `hidden_skill_tools`.
  - `active = list(critical)`.
  - Budget filled via `_fill(normal)` then `_fill(low)`.
  - Caller input order within a tier is preserved for tie-breaking.
- `src/decafclaw/tool_definitions.py:233-245`: `build_tool_list(ctx)` calls `classify_tools`.
- `src/decafclaw/context_composer.py:1573-1590`: `_compose_tools(ctx, config)` calls `classify_tools`.

## 3. Context & State Persistence Across Turns
- `src/decafclaw/context.py:44-50`: `ToolState` holds `active_aux_approval_presets: list[str]`, `disabled_aux_approval_presets: list[str]`, `aux_approval_guidance: list[str]`, `llm_approved_shell_patterns: list[str]`.
- `src/decafclaw/context.py:161-164`: `Context` holds runtime attributes like `active_model: str`.
- `src/decafclaw/conversation_manager.py:148-158`: `PersistedTurnState` dataclass declares per-conversation persisted fields across turns.
  - Line 165: `_PERSISTED_BINDINGS` maps field names to (reader, writer) tuples on `ctx`.
  - Line 208: `_CTX_DRIVEN_FIELDS` declares which fields flow `ctx -> state` at turn end.
  - Line 223: `_REPLACEABLE_COLLECTION_FIELDS` defines collections replaced rather than merged.
  - Line 1136: `set_flag(conv_id, key, value)` allows external updates to `state.persisted.<key>`.
  - `tests/test_conversation_manager.py:test_persisted_field_bindings_exhaustive` ensures every field on `PersistedTurnState` has a binding.

## 4. Web Gateway & WebSocket Protocol
- `src/decafclaw/web/message_types.json`: Central wire protocol definitions.
  - `conv_history` (lines 45-49): Includes `conv_id`, `messages`, `has_more`, `context_limit`, `read_only?`, `estimated_tokens?`, `active_model?`, `available_models?`, `default_model?`, `turn_active?`, `pending_confirmation?`.
  - `set_model` (lines 181-185): Client -> Server `{conv_id: string, model: string}`.
  - `model_changed` (lines 70-74): Server -> Client `{conv_id: string, model: string}`.
- `src/decafclaw/web/websocket.py:326-350`: In `_handle_load_history`, sends `active_model`, `available_models`, `default_model`.
- `src/decafclaw/web/websocket.py:623-652`: In `_handle_set_model`, checks `conv_id`, updates archive and `manager.set_flag(conv_id, "active_model", model_name)`, sends `model_changed`.
- `src/decafclaw/web/websocket.py:975-1038`: `on_conv_event` forwards events emitted via `manager.emit(conv_id, ...)` to WebSocket client.

## 5. Web UI Frontend
- `src/decafclaw/web/static/components/conversation-sidebar.js:641-662`:
  - Renders `<div class="model-picker">` with `<select id="model-select">`.
  - Synchronizes selection in `updated()` and listens to `@change`.
  - Bound options use `?selected=${m === target} .selected=${m === target}` per Pico v2 convention.
- `src/decafclaw/web/static/lib/conversation-store.js:534-541`:
  - `setModel(model)`: updates `#activeModel`, sends `SET_MODEL`, emits change.
  - Lines 585-587: `CONV_HISTORY` sets `#activeModel`, `#availableModels`, `#defaultModel`.
  - Lines 660-664: `MODEL_CHANGED` updates `#activeModel` and emits change.
