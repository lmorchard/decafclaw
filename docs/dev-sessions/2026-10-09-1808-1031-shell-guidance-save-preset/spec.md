# Spec: Save active shell auto-approval guidance rules to a new or existing preset (#1031)

## Motivation and Goal

With #982 ("Approve + remember why") and #986 (restricting `shell_guidance` to per-conversation scope), rules added during a conversation are strictly conversation-scoped.

However, when an interactive session accumulates good, constrained exception rules, users should be able to bundle or save those rules into a named situational preset (e.g. `save_preset` with `preset="my_project"`) so they can easily be reused on demand in future sessions via `shell_guidance(action="enable_preset", preset="my_project")`.

## Requirements

1. **New Action in `shell_guidance`**:
   - `action="save_preset"`
   - Parameters:
     - `preset`: str (required) — the target preset name (e.g. `client_work`, `deploy_pipeline`).
     - `rule`: str (optional) — if specified, saves only this specific rule. If omitted or empty, saves all active rules in the current conversation (`ctx.tools.aux_approval_guidance`).

2. **Persistence File**:
   - Saved presets are stored at `config.agent_path / "shell_approval_presets.json"`.
   - Format:
     ```json
     {
       "my_project": "- Auto-approve gh pr create and git push during PR workflow\n- Auto-approve npm run test:watch"
     }
     ```
   - Helper functions: `_saved_presets_path(config)`, `_load_saved_presets(config) -> dict[str, str]`, `_write_saved_presets(config, presets: dict[str, str])`.

3. **Preset Merging & Protection**:
   - `_get_all_presets(config)` returns:
     `{**DEFAULT_AUX_APPROVAL_PRESETS, **config_presets, **saved_presets}`
   - Built-in static presets (`developer`, `github`, etc.) cannot be overwritten. Attempting to `save_preset` using a built-in name returns an error advising the user to choose a custom name.
   - If `preset` already exists in custom presets (from disk or config): the new rule(s) are appended to the existing preset text (avoiding duplicate lines).
   - If `preset` is new: created with the formatted rule(s).

4. **Confirmation Flow**:
   - Creating or updating a saved preset modifies persistent admin state on disk.
   - It requires confirmation with `force=True`, displaying the preset name and the rules being saved.

5. **Safety Boundary**:
   - Saving a preset does **not** auto-activate it globally in future conversations.
   - Fresh conversations start with the base policy. Future conversations can activate the saved preset on demand via `shell_guidance(action="enable_preset", preset="<name>")`.

## Acceptance Criteria

- `tool_shell_guidance(action="save_preset", preset="<name>")` bundles active conversation rules into a custom preset on disk.
- If `rule` is passed, saves only that rule; if omitted, saves all `ctx.tools.aux_approval_guidance` rules.
- If there are no rules to save (no `rule` arg and `ctx.tools.aux_approval_guidance` is empty), returns an error.
- Cannot overwrite built-in presets (`developer`, `github`, etc.).
- Appending to an existing custom preset preserves existing rules and avoids duplicate lines.
- `_get_all_presets` includes custom saved presets, making them visible in `shell_guidance(action="list")` and activatable via `shell_guidance(action="enable_preset")`.
- Confirmation is requested with `force=True` before disk mutation.
- Schema in `SHELL_TOOL_DEFINITIONS` and documentation in `docs/tools.md` updated with `save_preset`.
- Unit tests cover new preset creation, appending to existing presets, built-in name protection, empty-rule validation, and loading into `list` and `enable_preset`.
