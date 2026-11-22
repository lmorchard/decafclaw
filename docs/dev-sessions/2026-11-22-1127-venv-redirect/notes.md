# Issue #1693 — redirect venv binary invocations to `uv run`

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1693
- Worktree: `.claude/worktrees/issue-1693-venv-redirect`
- Branch: `issue-1693-venv-redirect`, stacked on `issue-1690-sed-regex-paths` (PR #1711)
- Goal: one-shot redirect before a human prompt when a command's executable is an out-of-workspace `*/.venv/bin/*` or `*/venv/bin/*` binary; fix executable detection with leading env assignments.
- Status: stacked on `issue-1690-sed-regex-paths` (`932e64a2`), PR #1712. Implementation, focused tests, `make check`, and full suite complete. Next: ready submission; retarget to `main` after #1711 merges.

## Decisions

- Reuse `_claim_shell_redirect` / `_redirect_text` from the #1626 redirect path.
- Detect the venv executable in `security_monitor` (same segment tokenizer as #1690), expose a helper, and invoke it from the security-monitor ASK branch of `check_shell_approval`.
- Skip leading `NAME=value` assignments when identifying the executable token; use the same index for the `SYSTEM_EXEC_DIRS` allow check.
- On the repeat, fall through to the normal confirmation with the `uv run` suggestion in the card (`feedback`, plus the ask message).
- Unattended turns also get the one-shot redirect, consistent with #1626.

## Checks

- Baseline `make test` on untouched `origin/main` in this worktree: 4 failures, all in
  `tests/test_host_metrics.py` (host-specific macOS metrics collection returns `None`);
  6587 passed. Unrelated to the shell change.
- Focused after change: `tests/test_security_monitor.py tests/test_shell_approval_activity.py
  tests/test_shell_purpose.py tests/test_shell_readonly.py tests/test_shell_readonly_adversarial.py`
  → 330 passed.
- Test-path note: a venv under the test temp dir is *allowed* by the security monitor
  (`ALLOWED_TEMP_DIRS` includes `/var/folders`), so the redirect tests use
  `/opt/decafclaw-venv-tests/...` to exercise the real out-of-workspace ASK.
