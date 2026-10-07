# Issue 984 Spec: Improve Discoverability and Coverage of shell_guidance Presets

## Objective
Reduce approval friction during development sessions by:
1. Expanding the built-in `developer` and `github` presets in `src/decafclaw/tools/shell_tools.py` to cover modern workflows (scoped `uv run`, `poetry run`, `npm run`, typecheckers, safe remote git operations like fetch/pull/feature-push, safe `&&` chaining).
2. Updating the `shell_guidance` tool description to advise proactive invocation at the start of dev sessions.
3. Updating development skill runbooks (`contrib/skills/opencode/SKILL.md`) and documentation (`docs/tools.md`) to instruct agents to activate `developer` and `github` presets during session initialization.
4. Adding an eval case to `evals/tool_routing.yaml` verifying proactive routing to `shell_guidance` at dev session kickoff.

## Detailed Changes

### 1. Preset Expansions (`src/decafclaw/tools/shell_tools.py`)
- **`developer` preset**:
  - Add task-scoped environment runners: `uv run <task>`, `poetry run <task>`, `npm run <script>`, `cargo test/check/build`.
  - Add typecheckers: `pyright`, `mypy`, `tsc`.
  - Add non-destructive remote git operations: `git fetch`, `git pull`, and pushing to feature/topic branches (`feat/*`, `fix/*`, etc.).
  - Explicitly allow safe sequential command chaining (`&&`) between approved dev commands (e.g. `git checkout main && git pull origin main`).
  - Keep strict guardrails against destructive operations (`git reset --hard`, force-pushing, pushing to `main`/`master`, deleting branches/repos) and package installations (`pip install`, `uv add`, `npm install`).
- **`github` preset**:
  - Add `gh pr checks` (`gh pr checks --watch`), `gh run watch`, `gh issue create`.

### 2. Tool Description Framing (`SHELL_TOOL_DEFINITIONS`)
- Update `shell_guidance` description to clarify its role as a proactive session setup accelerator:
  *"Call at the start of software development, testing, or GitHub workflows to reduce approval friction on routine commands."*

### 3. Skill & Documentation Runbooks
- Add `## Development Setup & Shell Auto-Approval` in `contrib/skills/opencode/SKILL.md`.
- Add `### Situational Auto-Approval Presets (developer, github)` in `docs/tools.md`.

### 4. Tests
- Ensure `tests/test_shell_approval_guidance.py` verifies preset definitions, prompt construction, and guidance management.

### 5. Evals
- Add proactive routing case in `evals/tool_routing.yaml` for activating `shell_guidance` at dev kickoff.
