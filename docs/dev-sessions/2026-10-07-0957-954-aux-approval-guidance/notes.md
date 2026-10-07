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

### Addressed PR Review Findings (Batch 1):
1. **Destructive cache broadening (`_suggest_pattern`)**: Implemented `_suggest_aux_approval_pattern` to prevent state-changing commands under guidance (e.g. `git checkout`, `git add`) from wildcarding into destructive variants (`git checkout *`), while preserving safe wildcarding for read-only test runners and status commands (`pytest *`, `git status`).
2. **Environment override for dict config (`SHELL_AUX_APPROVAL_PRESETS`)**: Added `_parse_dict` in `config.py` for dict fields in `_coerce`, and added defensive json-parsing in `_get_all_presets`.
3. **Cross-turn session persistence in `ConversationManager`**: Added `active_aux_approval_presets`, `disabled_aux_approval_presets`, `aux_approval_guidance`, and `llm_approved_shell_patterns` to `PersistedTurnState`, `_PERSISTED_BINDINGS`, and `_CTX_DRIVEN_FIELDS`.
4. **Git push pre-empted by Security Monitor**: Updated `developer` preset and documentation to remove `git push` claim, since `git push` is hard-classified as `ASK` by `security_monitor.py`.
5. **CWD leakage in relative guidance file path**: Constrained `_load_guidance_text` to only resolve absolute paths directly; relative paths are strictly resolved against `agent_path` and `workspace_path`, preventing CWD directory leakage.

### Addressed PR Review Findings (Batch 2):
1. **Tool preapproval bypass for trust-boundary mutations**: Added `force=True` to `request_confirmation`, ensuring `ctx.tools.preapproved` cannot bypass user confirmation for `tool_shell_guidance` or `tool_shell_patterns`.
2. **Persistent disable of config presets**: Stored `disabled_presets` in `shell_approval_guidance.json` and resolved in `resolve_aux_approval_guidance`, allowing persistent disables to mask presets enabled via `config.shell.active_aux_approval_presets`.
3. **Collection fields clearing across turns**: Defined `_REPLACEABLE_COLLECTION_FIELDS` in `conversation_manager.py` to ensure empty lists on save/restore replace previous turn collections rather than being ignored by truthy checks.
4. **Tool-choice evals for `shell_guidance`**: Added bounded eval cases in `evals/tool_choice/core_overlaps.yaml` disambiguating `shell_guidance` vs `shell_patterns` vs `shell`.
5. **PR description alignment**: Clarified in PR summary that `git push` is not auto-approved by the `developer` preset because it is always intercepted by the security monitor.

## Test Results
- `tests/test_shell_approval_guidance.py`: 21 passed.
- `tests/test_conversation_manager.py` persisted binding tests: passed.
- `tests/test_eval_tool_choice_runner.py` & loadout: 25 passed.
- Pyright: 0 errors, 0 warnings.
- Ruff format and check: all clean.
