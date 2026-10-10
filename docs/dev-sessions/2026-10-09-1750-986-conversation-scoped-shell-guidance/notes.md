# Notes: Restrict shell_guidance preset activation to per-conversation scope (#986)

## Summary of Changes

1. **Restricted `shell_guidance` to per-conversation scope**:
   - Removed `persistent: bool = False` argument from `tool_shell_guidance`.
   - Removed `persistent` parameter from `shell_guidance` schema in `SHELL_TOOL_DEFINITIONS`.
   - `enable_preset`, `disable_preset`, `add_rule`, and `remove_rule` now operate strictly on the current conversation's `ctx.tools` state.
   - Removed persistent modification helpers `_save_persistent_preset`, `_remove_persistent_preset`, `_save_persistent_rule`, `_remove_persistent_rule`.

2. **Deprecated `shell_approval_guidance.json` Runtime File**:
   - `resolve_aux_approval_guidance` no longer reads `shell_approval_guidance.json`.
   - If `shell_approval_guidance.json` is detected on disk, a one-time warning is logged advising admins to move global presets and guidance into `config.json` (`shell.active_aux_approval_presets`, `shell.aux_approval_guidance`).

3. **Documentation**:
   - Updated `docs/tools.md` to clarify that `shell_guidance` applies situational prompt guidance per-conversation, while global baselines belong in static `config.json`.

4. **Tests**:
   - In `tests/test_shell_approval_guidance.py`:
     - Rewrote persistence tests into per-conversation tests (`test_tool_shell_guidance_enable_and_disable_preset_session_scoped`).
     - Added `test_existing_persistent_guidance_file_ignored_and_warned_once` verifying that an existing `shell_approval_guidance.json` file is ignored and logs a single warning.
