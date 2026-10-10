# Plan: Save active shell auto-approval guidance rules to a new or existing preset (#1031)

## Phase 1: Preset File Helpers & Merging in `src/decafclaw/tools/shell_tools.py`
- [ ] Add `_saved_presets_path(config) -> Path`: returns `config.agent_path / "shell_approval_presets.json"`.
- [ ] Add `_load_saved_presets(config) -> dict[str, str]`: loads JSON dict safely, handling missing or corrupt file.
- [ ] Add `_save_custom_preset(config, preset_name: str, rules: list[str]) -> str`: updates or creates preset definition on disk.
- [ ] Update `_get_all_presets(config) -> dict[str, str]`: merge built-in presets, `config.shell.aux_approval_presets`, and `_load_saved_presets(config)`.

## Phase 2: Action `save_preset` in `tool_shell_guidance` & Tool Definitions
- [ ] In `tool_shell_guidance`:
  - Handle `action == "save_preset"`.
  - Validate `preset` is non-empty.
  - Guard against overwriting built-in presets in `DEFAULT_AUX_APPROVAL_PRESETS` (`[error: cannot overwrite built-in preset '...']`).
  - Collect rules to save: if `rule` is provided, `[rule]`; else `list(ctx.tools.aux_approval_guidance)`.
  - Validate at least one rule is present (`[error: no rules to save...]`).
  - Request user confirmation with `force=True`.
  - On approval, call `_save_custom_preset`, which creates the preset or appends to existing custom preset.
  - Return clear confirmation message describing whether a new preset was created or an existing one updated.
- [ ] Update `SHELL_TOOL_DEFINITIONS`:
  - Add `"save_preset"` to `action` enum.
  - Document `save_preset` in description and parameter explanations.

## Phase 3: Documentation & Verification Tests
- [ ] Update `docs/tools.md`: document `save_preset` action, file location, and situational reusability.
- [ ] In `tests/test_shell_approval_guidance.py`:
  - Test saving active rules to a new preset.
  - Test appending a single rule to an existing preset.
  - Test protection of built-in preset names (`developer`, `github`).
  - Test validation when no rules exist to save.
  - Test that saved presets are listed in `action="list"` and activatable via `action="enable_preset"`.
- [ ] Run full gate (`make check`, `make test-js`, `make test`).
- [ ] Record retro / notes in `notes.md`.
