# Dev Session Plan: Issue 984 - shell_guidance Coverage and Discoverability

## Phase 1: Code Updates in decafclaw
- [x] In `src/decafclaw/tools/shell_tools.py`:
  - Expand `DEFAULT_AUX_APPROVAL_PRESETS["developer"]` with scoped runners (`uv run <task>`, `poetry run <task>`, `npm run <script>`, `cargo test/check/build`), `pyright`, `mypy`, `git fetch`, `git pull`, feature branch pushing, and safe `&&` chaining.
  - Expand `DEFAULT_AUX_APPROVAL_PRESETS["github"]` with `gh pr checks`, `gh issue create`, `gh run watch`.
  - Update `shell_guidance` description in `SHELL_TOOL_DEFINITIONS`.
- [x] In `src/decafclaw/security_monitor.py`:
  - Narrow `git push` sensitive pattern to protected branches (`main`/`master`), force-pushes, and remote branch deletions, allowing feature branch pushes to reach aux-LLM auto-approval.
- [x] Update unit tests in `tests/test_shell_approval_guidance.py` and `tests/test_security_monitor.py`.

## Phase 2: Documentation Updates
- [x] In `docs/tools.md`:
  - Add documentation on Situational Auto-Approval Presets (`developer`, `github`).

## Phase 3: Evals & Testing
- [x] Add proactive `shell_guidance` routing case to `evals/tool_routing.yaml` with `expect_tool_args`.
- [x] Run targeted shell tests: `uv run --directory decafclaw pytest tests/test_shell_approval_guidance.py tests/test_security_monitor.py`.
- [x] Run linting & type checks: `ruff check`, `ruff format`, `pyright`.
- [x] Update session notes in `notes.md`.
- [x] Commit, push branch `feat/984-shell-guidance-developer-coverage`, and update pull request.
