# Issue 984 Spec: Improve Discoverability and Coverage of shell_guidance Presets

## Objective
Reduce approval friction during development sessions by:
1. Expanding the built-in `developer` and `github` presets in `src/decafclaw/tools/shell_tools.py` to cover modern workflows (scoped `uv run`, `poetry run`, `npm run`, typecheckers, safe remote git operations like fetch/pull/feature-push, safe `&&` chaining).
2. Narrowing `git push` in `src/decafclaw/security_monitor.py` to target protected branches (`main`/`master`), branch deletion, and force-pushes so feature-branch pushes reach the aux-LLM auto-approval evaluation.
3. Updating the `shell_guidance` tool description to advise proactive invocation at the start of dev sessions.
4. Documenting situational presets in `docs/tools.md`.
5. Adding an eval case to `evals/tool_routing.yaml` verifying proactive routing to `shell_guidance` with `expect_tool_args` at dev session kickoff.

## Detailed Changes

### 1. Preset Expansions & Security Monitor Narrowing
- **`developer` preset (`src/decafclaw/tools/shell_tools.py`)**:
  - Add task-scoped environment runners: `uv run <task>`, `poetry run <task>`, `npm run <script>`, `cargo test/check/build`.
  - Add typecheckers: `pyright`, `mypy`, `tsc`.
  - Add non-destructive remote git operations: `git fetch`, `git pull`, and pushing to feature/topic branches (`feat/*`, `fix/*`, etc.).
  - Explicitly allow safe sequential command chaining (`&&`) between approved dev commands (e.g. `git checkout main && git pull origin main`).
  - Keep strict guardrails against destructive operations (`git reset --hard`, force-pushing, pushing to `main`/`master`, deleting branches/repos) and package installations (`pip install`, `uv add`, `npm install`).
- **`github` preset (`src/decafclaw/tools/shell_tools.py`)**:
  - Add `gh pr checks` (`gh pr checks --watch`), `gh run watch`, `gh issue create`.
- **Security Monitor (`src/decafclaw/security_monitor.py`)**:
  - Replace blanket `\bgit\s+push\b` sensitive pattern with specific patterns for pushes to `main`/`master` and remote branch deletions (`--delete`, `-d`, `:branch`). Feature branch pushes pass to the aux-LLM.

### 2. Tool Description Framing (`SHELL_TOOL_DEFINITIONS`)
- Update `shell_guidance` description to clarify its role as a proactive session setup accelerator:
  *"Call at the start of software development, testing, or GitHub workflows to reduce approval friction on routine commands."*

### 3. Documentation
- Add `### Situational Auto-Approval Presets (developer, github)` in `docs/tools.md`.

### 4. Tests
- `tests/test_shell_approval_guidance.py`: verify prompt construction, preset handling, and end-to-end auto-approval of feature branch pushes.
- `tests/test_security_monitor.py`: verify `git push` to `main`, `-f`, and `--delete` remain sensitive (`ASK`), while feature branch pushes return `ALLOW`.

### 5. Evals
- Add proactive routing case in `evals/tool_routing.yaml` with `expect_tool_args` asserting `action="enable_preset"` and `preset="developer"`.
