# Issue 142 — mcp_get_prompt `arguments` as object

## Resume here

Implementation complete, local gates green, DRAFT PR #1065 published, hosted CI green on the published head. Next action: independent review by parent (separate fresh-context worker, same local model per user-approved exception); do not mark PR ready, do not request review, do not merge.

## Context

- Issue: https://github.com/lmorchard/decafclaw/issues/142
- Base: `origin/main` @ `4eaef9ee7fa841ab3c06d4ae985662917f7175da` (CI green)
- Branch: `issue-142-mcp-get-prompt-object`
- Worktree: `.claude/worktrees/issue-142-mcp-get-prompt-object` (HTTP_PORT=18891 in `.env` copy)
- Board: moved Ready → In progress (2026-10-12, readback confirmed)

## Confirmed decisions (from triage, not reopened)

- `arguments` schema: `{"type": "object", "description": ...}`, no `additionalProperties` (matches `http_request.headers`, canvas/sticky `data`, vault `fields`, `delegate_task.return_schema`).
- Accept `dict` directly; keep accepting a JSON string (parse, require dict).
- Non-object (number/bool, unparseable string, JSON string parsing to non-object) → `ToolResult(text="[error: ...]")` with a clear message.
- Update `skills/mcp/SKILL.md` tool description line.
- Tests in `tests/test_mcp.py` next to `test_mcp_get_prompt_success`: dict passthrough, JSON string accepted, non-object errors.
- No `tool_choice` eval case (parameter-shape-only change; description disambiguation unchanged).

## Findings

- `execute_single_tool` (tool_execution.py:234) does `json.loads` of the LLM's argument string and passes the resulting dict as kwargs — no per-parameter schema validation, so a dict-typed `arguments` kwarg reaches the function untouched.
- No other callers of `tool_mcp_get_prompt` in `src/` (the `!mcp__…` command path in `commands.py` calls `session.get_prompt` directly, not the tool).
- `http_request.headers` handler signature uses `headers: dict | None = None` — same shape adopted here.

## Baseline (before any code change)

- `make test` in the worktree: **4522 passed, 2 skipped in 23.53s** — clean, no pre-existing failures.

## Test-first evidence

- New tests run against unmodified code: `test_mcp_get_prompt_dict_arguments` FAILED (TypeError: json.loads on dict), `test_mcp_get_prompt_non_object_arguments` FAILED (TypeError on `42`), `test_mcp_get_prompt_json_string_arguments` PASSED (backward-compatible behavior, as intended).
- After implementation: all 5 `mcp_get_prompt` tests pass (2 pre-existing + 3 new).

## Commands

- `git worktree add .claude/worktrees/issue-142-mcp-get-prompt-object -b issue-142-mcp-get-prompt-object origin/main` → OK, HEAD 4eaef9e
- `uv sync` → OK
- `cp .env` + `HTTP_PORT=18891` → OK
- `make test` (baseline) → 4522 passed, 2 skipped
- `uv run pytest tests/test_mcp.py -n 0 -k "dict_arguments or json_string_arguments or non_object_arguments" -v` (pre-change) → 2 failed, 1 passed (expected failures)
- `uv run pytest tests/test_mcp.py -n 0 -k "mcp_get_prompt" -v` (post-change) → 5 passed
- `uv run pytest tests/test_mcp.py -n 0` (post-change, whole file) → 73 passed
- `make fmt` → 1 file reformatted (collapsed one multi-line ToolResult call in tools.py), then clean
- `make check` → exit 0: install-js OK, check-message-types in sync, fmt-check 432 files, ruff clean, pyright 0 errors/0 warnings, check-js (tsc --noEmit) OK, check-browser-assets 2 passed
- `make test` (final) → **4525 passed, 2 skipped in 15.56s** (baseline +3 new tests)

## Publish

- PR URL: https://github.com/lmorchard/decafclaw/pull/1065 (draft)
- Base: `main` @ `4eaef9ee7fa841ab3c06d4ae985662917f7175da`
- Published head: the single commit carrying this file (see git log; amended after publish to fold in these notes).
- `verify-commit` (on branch, pre-amend head `6e7757a`): remote branch `issue-142-mcp-get-prompt-object` == local head, parent `4eaef9e`.
- `verify-commit --pr-head` (pre-amend head `6e7757a`): PR head == `6e7757a`.
- Post-amend: re-verify the new head against both the branch and the PR.
- Hosted CI on published head: green — `lint-and-test` passed (required), `js-test` passed (required), `check-tui` passed (optional); `mergeable: MERGEABLE`, `base_behind_by: 0`.

## Implementation model

`llama.cpp/llama` (local OpenCode subagent) — same local model as the parent will use for independent review, per the user-approved exception.

## Checks NOT run

- No `tool_choice` / `eval-tools` run — parameter-shape-only change, disambiguation unchanged (project convention).
- No live Mattermost / web UI test — that is a post-merge step.
