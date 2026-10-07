# Dev Session Notes: Issue 954

- Worktree: `decafclaw/.claude/worktrees/feat-954-aux-approval-guidance`
- Branch: `feat/954-aux-approval-guidance`
- Base: `origin/main` (commit 226f923)

## Findings & Decisions
- Removed duplicate `@dataclass` decorator on `ShellConfig` in `config_types.py`.
- Added `aux_approval_guidance`, `aux_approval_presets`, and `active_aux_approval_presets` to `ShellConfig`.
- Added runtime session fields `active_aux_approval_presets`, `disabled_aux_approval_presets`, and `aux_approval_guidance` to `ToolState`.
- Implemented `DEFAULT_AUX_APPROVAL_PRESETS` in `shell_tools.py` containing `developer` and `github` presets.
- Implemented persistent storage helpers (`_persistent_guidance_path`, `_load_persistent_guidance`, `_save_persistent_preset`, `_remove_persistent_preset`, `_save_persistent_rule`, `_remove_persistent_rule`) in `shell_tools.py` saving to `shell_approval_guidance.json`.
- Implemented `tool_shell_guidance` supporting `list`, `enable_preset`, `disable_preset`, `add_rule`, and `remove_rule`.
- All mutation actions require user approval via `request_confirmation` before taking effect.
- Added support for `persistent=True` to persist presets and rules across sessions in `shell_approval_guidance.json`.
- Blocked mutating approval rules on unattended turns (`is_unattended`).
- Registered `shell_guidance` in `SHELL_TOOLS` and `SHELL_TOOL_DEFINITIONS`.
- Updated `check_shell_approval` to use `build_aux_approval_prompt(ctx, command)`.
- Verified strict backward compatibility: when no guidance/presets are active, the prompt matches the prior hardcoded prompt.

## Test Results
- `tests/test_shell_approval_guidance.py`: 14 passed.
- `tests/test_security_monitor.py` & `tests/test_unattended_approval.py`: 17 passed.
- `tests/test_background_tools.py`: 28 passed.
- Pyright: 0 errors, 0 warnings.
- Ruff format and check: all clean.
