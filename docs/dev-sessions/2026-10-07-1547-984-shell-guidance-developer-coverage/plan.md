# Dev Session Plan: Issue 984 - shell_guidance Coverage and Discoverability

## Phase 1: Code Updates in decafclaw
- [x] In `src/decafclaw/tools/shell_tools.py`:
  - Expand `DEFAULT_AUX_APPROVAL_PRESETS["developer"]` with `uv run`, `pyright`, `mypy`, `git fetch`, `git pull`, feature branch pushing, and safe `&&` chaining.
  - Expand `DEFAULT_AUX_APPROVAL_PRESETS["github"]` with `gh pr checks`, `gh issue create`, `gh run watch`.
  - Update `shell_guidance` description in `SHELL_TOOL_DEFINITIONS`.
- [x] Update unit tests in `tests/test_shell_approval_guidance.py` for the new preset texts and behavior.

## Phase 2: Skill Documentation Updates
- [x] In `skills/decafclaw/SKILL.md`:
  - Add section on Development Workflow Setup with `shell_guidance` preset activation commands.
- [x] In `skills/github/SKILL.md`:
  - Add guidance on enabling the `github` preset.

## Phase 3: Testing & Quality Gates
- [x] Run targeted shell tests: `uv run --directory decafclaw pytest tests/test_shell_approval_guidance.py` (22 passed).
- [x] Run linting & type checks: `ruff check`, `ruff format`, `pyright` (all clean).
- [x] Update session notes in `notes.md`.
- [x] Commit, push branch `feat/984-shell-guidance-developer-coverage`, and open pull request.
