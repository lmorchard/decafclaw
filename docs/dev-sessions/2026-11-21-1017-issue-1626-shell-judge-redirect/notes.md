# issue-1626-shell-judge-redirect

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1626
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1626-shell-judge-redirect
- Branch: issue-1626-shell-judge-redirect
- Base: origin/main at 598bf8583a091bdf9a26b6374131a89b206d1adb
- Status: implementation complete, uncommitted at time of writing.
- Next action: review diff, commit, run `make check`/`make test` at the commit, then submit PR.
- Checks run: targeted pytest (test_shell_purpose, test_mattermost_display, test_background_tools, test_agent_loop_breaker) green; confirm-view vitest green; `make check` green; full `make test` green on rerun (6126 passed). First full run had one unrelated flake, `test_background_tools.py::test_pin_sticky_emit_timeout_does_not_block_reader` (sticky publish timing under -n auto; passes alone and on rerun).

## Log

- 2026-11-21: Read issue #1626 and current `check_shell_approval` / `confirmation.py` / card renderers.
- Design decisions:
  - Redirect budget is `ToolState.shell_redirect_fingerprints: set[str]`: declared dataclass field, shared across `fork_for_tool_call` (inner containers shared), fresh per turn because each turn builds a new `Context`. No persistence.
  - Fingerprint = `loop_breaker.fingerprint("shell", {command, cwd, purpose})`; `purpose` normalized with `_single_line`.
  - Cap is 3 total redirects per turn; each fingerprint redirects once. Claim happens with no await between check and add.
  - `check_shell_approval` returns `{"redirected": True, "feedback": ...}`; `tool_shell` and `tool_shell_background_start` format via `_redirect_text`.
  - Native tool index generated per call from `collect_all_tool_defs(ctx)` intersected with `ctx.tools.allowed`/`disallowed`; active = not in `ctx.tools.deferred_pool`, deferred listed separately. Unknown tools go to "Other". Names are not truncated (the agent already sees every tool schema).
  - `feedback` added to `_request_via_manager` action_data keys, web confirm card (`c.action_data?.feedback`), and Mattermost card (`on_confirm_request(feedback=...)`).
- Generated `openapi.*` / `api-client` drift appeared when `make check` regenerated clients (duplicate operation-id warning); restored those unrelated files with `git checkout --`.
- Tests updated: old fall-through tests rewritten; new tests cover first redirect, second prompt with reason+feedback, per-purpose/per-command budget, turn cap, fork sharing, turn reset, unattended first/second offense, no-feedback fall-through, live/deferred index, action_data feedback, web/MM rendering.

