# Dev Session Plan: Issue 954 - Configurable & Extensible Shell Auto-Approval Prompt

## Phase 1: Data Models & Configuration Types
- [x] In `src/decafclaw/config_types.py`:
  - Clean up duplicate `@dataclass` on `ShellConfig`.
  - Add `aux_approval_guidance: str = ""`
  - Add `aux_approval_presets: dict[str, str] = field(default_factory=dict)`
  - Add `active_aux_approval_presets: list[str] = field(default_factory=list)`
- [x] In `src/decafclaw/context.py`:
  - Add `active_aux_approval_presets: list[str] = field(default_factory=list)` to `ToolState`.
  - Add `disabled_aux_approval_presets: list[str] = field(default_factory=list)` to `ToolState`.
  - Add `aux_approval_guidance: list[str] = field(default_factory=list)` to `ToolState`.

## Phase 2: Preset Definitions & Prompt Builder
- [x] Define `DEFAULT_AUX_APPROVAL_PRESETS` in `src/decafclaw/tools/shell_tools.py`.
- [x] Implement persistent storage helpers (`_persistent_guidance_path`, `_load_persistent_guidance`, etc.) in `shell_tools.py`.
- [x] Implement `resolve_aux_approval_guidance(ctx: Context) -> list[str]`:
  - Resolves active preset names from `ctx.config.shell.active_aux_approval_presets`, persistent disk file, and `ctx.tools.active_aux_approval_presets`.
  - Accounts for `disabled_aux_approval_presets`.
  - Merges default presets with `ctx.config.shell.aux_approval_presets`.
  - Loads file content if `ctx.config.shell.aux_approval_guidance` is a readable file path (checking relative to agent_path, workspace_path, or absolute).
  - Appends inline `aux_approval_guidance`, persistent disk rules, and session-scoped `ctx.tools.aux_approval_guidance`.
- [x] Implement `build_aux_approval_prompt(ctx: Context, command: str) -> str`:
  - Constructs clear prompt for aux LLM, seamlessly injecting guidelines.

## Phase 3: Integrate into `check_shell_approval` & Add `shell_guidance` Tool
- [x] Update `check_shell_approval` in `src/decafclaw/tools/shell_tools.py` to use `build_aux_approval_prompt(ctx, command)`.
- [x] Implement `tool_shell_guidance` with actions `list`, `enable_preset`, `disable_preset`, `add_rule`, and `remove_rule`.
- [x] Require user approval via `request_confirmation` for all mutations.
- [x] Support session vs persistent modifications (`persistent=True`).
- [x] Register `shell_guidance` in `SHELL_TOOLS` and `SHELL_TOOL_DEFINITIONS`.

## Phase 4: Testing & Quality Gates
- [x] Create and extend `tests/test_shell_approval_guidance.py`:
  - Test prompt generation without extra guidance (strict baseline).
  - Test prompt generation with inline guidance.
  - Test prompt generation with file-based guidance.
  - Test prompt generation with built-in presets (`developer`, `github`).
  - Test prompt generation with custom presets and preset overrides.
  - Test session-scoped presets and guidance via `ctx.tools`.
  - Test `check_shell_approval` calls aux LLM with expected prompt and handles approvals.
  - Test `tool_shell_guidance` list, enable, disable, add_rule, remove_rule for session and persistent modes.
  - Test confirmation denial and unattended turn rejection.
- [x] Run test suite with pytest (14 tests passed).
- [x] Run `ruff check`, `ruff format`, `pyright` type checks (all clean).

## Phase 5: Verification & Documentation
- [x] Record dev notes and outcomes in `notes.md`.
- [x] Update documentation (`docs/config.md`, `docs/tools.md`).
- [ ] Commit changes and push to PR #968.
