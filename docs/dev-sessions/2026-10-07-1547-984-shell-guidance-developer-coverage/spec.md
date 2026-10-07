# Issue 984 Spec: Improve Discoverability and Coverage of shell_guidance Presets

## Objective
Reduce approval friction during development sessions by:
1. Expanding the built-in `developer` and `github` presets in `src/decafclaw/tools/shell_tools.py` to cover modern workflows (`uv run`, typecheckers, safe remote git operations like fetch/pull/feature-push, safe `&&` chaining).
2. Updating the `shell_guidance` tool description to advise proactive invocation at the start of dev sessions.
3. Updating development skills (`skills/decafclaw/SKILL.md`, `skills/github/SKILL.md`) to instruct agents to activate `developer` and `github` presets during session initialization.

## Detailed Changes

### 1. Preset Expansions (`src/decafclaw/tools/shell_tools.py`)
- **`developer` preset**:
  - Add package/environment runners: `uv run`, `poetry run`, `npm run`, `npx`, `cargo`.
  - Add typecheckers: `pyright`, `mypy`, `tsc`.
  - Add non-destructive remote git operations: `git fetch`, `git pull`, and pushing to feature/topic branches (`feat/*`, `fix/*`, etc.).
  - Explicitly allow safe sequential command chaining (`&&`) between approved dev commands (e.g. `git checkout main && git pull origin main`).
  - Keep strict guardrails against destructive operations (`git reset --hard`, force-pushing, pushing to `main`/`master`, deleting branches/repos).
- **`github` preset**:
  - Add `gh pr checks`, `gh pr watch`, `gh run watch`, `gh issue create`.

### 2. Tool Description Framing (`SHELL_TOOL_DEFINITIONS`)
- Update `shell_guidance` description to clarify its role as a proactive session setup accelerator:
  *"Call at the start of software development, testing, or GitHub workflows to reduce approval friction on routine commands."*

### 3. Skill Runbook Documentation
- Add `## Development Workflow Setup` in `skills/decafclaw/SKILL.md`.
- Add `## Auto-Approval Guidance` note in `skills/github/SKILL.md`.

### 4. Tests
- Ensure `tests/test_shell_tools.py` verifies preset definitions, prompt construction, and guidance management.
