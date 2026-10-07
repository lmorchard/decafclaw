# Dev Session Notes: Issue 954

- Worktree: `decafclaw/.claude/worktrees/feat-954-aux-approval-guidance`
- Branch: `feat/954-aux-approval-guidance`
- Base: `origin/main` (commit 226f923)

## Findings & Decisions
- Removed duplicate `@dataclass` decorator on `ShellConfig` in `config_types.py`.
- Added `aux_approval_guidance`, `aux_approval_presets`, and `active_aux_approval_presets` to `ShellConfig`.
- Added runtime session fields `active_aux_approval_presets` and `aux_approval_guidance` to `ToolState`.
- Implemented `DEFAULT_AUX_APPROVAL_PRESETS` in `shell_tools.py` containing `developer` and `github` presets.
- Implemented `_load_guidance_text`, `resolve_aux_approval_guidance`, and `build_aux_approval_prompt` in `shell_tools.py`.
- Updated `check_shell_approval` to use `build_aux_approval_prompt(ctx, command)`.
- Verified strict backward compatibility: when no guidance/presets are active, the prompt matches the prior hardcoded prompt.

## Test Results
- `tests/test_shell_approval_guidance.py`: 8 passed.
- `tests/test_security_monitor.py` & `tests/test_unattended_approval.py`: 17 passed.
- `tests/test_background_tools.py`: 28 passed.
- Pyright: 0 errors, 0 warnings.
- Ruff format and check: all clean.
