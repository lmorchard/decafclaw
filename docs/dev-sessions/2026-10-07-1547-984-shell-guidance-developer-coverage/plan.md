# Dev Session Plan: Issue 984 - shell_guidance Coverage and Discoverability

## Phase 1: Code Updates in decafclaw
- [x] In `src/decafclaw/tools/shell_tools.py`:
  - Expand `DEFAULT_AUX_APPROVAL_PRESETS["developer"]` with scoped runners (`uv run <task>`, `poetry run <task>`, `npm run <script>`, `cargo test/check/build`), `pyright`, `mypy`, `git fetch`, `git pull`, feature branch pushing, and safe `&&` chaining.
  - Expand `DEFAULT_AUX_APPROVAL_PRESETS["github"]` with `gh pr checks`, `gh issue create`, `gh run watch`.
  - Update `shell_guidance` description in `SHELL_TOOL_DEFINITIONS`.
- [x] Update unit tests in `tests/test_shell_approval_guidance.py` for the new preset texts and behavior.

## Phase 2: Skill & Documentation Updates
- [x] In `contrib/skills/opencode/SKILL.md`:
  - Add section on Development Setup & Shell Auto-Approval with `shell_guidance` preset activation commands.
- [x] In `docs/tools.md`:
  - Add documentation on Situational Auto-Approval Presets (`developer`, `github`).

## Phase 3: Evals & Testing
- [x] Add proactive `shell_guidance` routing case to `evals/tool_routing.yaml`.
- [x] Run targeted shell tests: `uv run --directory decafclaw pytest tests/test_shell_approval_guidance.py`.
- [x] Run linting & type checks: `ruff check`, `ruff format`, `pyright`.
- [x] Update session notes in `notes.md`.
- [x] Commit, push branch `feat/984-shell-guidance-developer-coverage`, and update pull request.
