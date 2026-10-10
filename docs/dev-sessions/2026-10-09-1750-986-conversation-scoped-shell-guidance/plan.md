# Plan: Restrict shell_guidance preset activation to per-conversation scope (#986)

## Phase 1: Clean up persistence helpers in `src/decafclaw/tools/shell_tools.py`
- [ ] Remove `_save_persistent_preset`, `_remove_persistent_preset`, `_save_persistent_rule`, `_remove_persistent_rule`.
- [ ] Implement a single warning check for existing `config.agent_path / "shell_approval_guidance.json"` (e.g. `_warn_deprecated_guidance_file(config)`).
- [ ] In `resolve_aux_approval_guidance`:
  - Call warning check.
  - Remove loading from `_load_persistent_guidance`.
  - Active presets resolve from `ctx.config.shell.active_aux_approval_presets` and `ctx.tools.active_aux_approval_presets`, filtering out `ctx.tools.disabled_aux_approval_presets`.
  - Guidance rules resolve from `ctx.config.shell.aux_approval_guidance` and `ctx.tools.aux_approval_guidance`.
- [ ] In `tool_shell_guidance`:
  - Remove `persistent: bool = False` argument.
  - In `action == "list"`: list config and session sources only.
  - In `enable_preset`, `disable_preset`, `add_rule`, `remove_rule`: operate strictly on `ctx.tools` and format confirmation messages for conversation scope only.
- [ ] Update `SHELL_TOOL_DEFINITIONS`:
  - Remove `persistent` property from `shell_guidance` schema.
  - Update description to state that presets and rules apply to the current conversation.

## Phase 2: Documentation
- [ ] Update `docs/tools.md`:
  - Remove mention of `shell_approval_guidance.json`.
  - Clarify that presets and custom rules activated via `shell_guidance` are strictly per-conversation, while global defaults live in `config.json`.

## Phase 3: Tests
- [ ] In `tests/test_shell_approval_guidance.py`:
  - Remove persistence tests (`test_tool_shell_guidance_persistent_preset`, `test_tool_shell_guidance_persistent_disable_config_preset`).
  - Update `test_tool_shell_guidance_add_and_remove_rule` to test session rules only.
  - Add test verifying that if `shell_approval_guidance.json` exists on disk, it is ignored and logs a warning once.
- [ ] Run `make check`, `make test-js`, `make test`.
- [ ] Document notes in dev session `notes.md`.
