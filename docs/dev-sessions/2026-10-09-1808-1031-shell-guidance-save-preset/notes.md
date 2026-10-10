# Notes: Save active shell auto-approval guidance rules to a new or existing preset (#1031)

## Summary of Changes

1. **`save_preset` Action in `shell_guidance`**:
   - Added `action="save_preset"` to `tool_shell_guidance`.
   - Allows users to save all active conversation guidance rules (`ctx.tools.aux_approval_guidance`) or a specific rule (`rule="..."`) into a named preset on disk (`data/{agent_id}/shell_approval_presets.json`).
   - If the preset name already exists, appends the new rules cleanly while avoiding duplicate lines.
   - Prevents overwriting built-in presets (`developer`, `github`, etc.).
   - Gated with user confirmation (`force=True`).
   - Does **not** activate the preset globally across all future conversations — it adds it to the catalog of available presets that can be situationally enabled on demand via `enable_preset`.

2. **Preset Merging**:
   - `_get_all_presets(config)` now merges built-in presets, custom presets declared in `config.shell.aux_approval_presets`, and custom presets stored in `shell_approval_presets.json`.

3. **Tool Definitions & Documentation**:
   - Updated `SHELL_TOOL_DEFINITIONS` with `save_preset` action and description.
   - Updated `docs/tools.md` explaining how active conversation rules can be saved/appended to reusable presets.

4. **Testing**:
   - Added unit and integration tests in `tests/test_shell_approval_guidance.py`:
     - Creating a new preset with active conversation rules.
     - Appending a single rule to an existing preset on disk.
     - Protecting built-in preset names from overwrite.
     - Validating non-empty rules and preset name.
     - Verifying newly saved presets appear in `action="list"` and can be enabled via `action="enable_preset"`.
