# Issue #1690 — sed/awk/grep expressions as paths

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1690
- Worktree: `.claude/worktrees/issue-1690-sed-regex-paths`
- Branch: `issue-1690-sed-regex-paths`, based on `origin/main` @ `6b59bddc`
- Goal: stop the security monitor from treating filter-command scripts/patterns that start with `/` as filesystem paths.
- Status: implementation and focused tests complete; docs updated. Next: `make fmt`, `make check` (expect the known host-metrics failures), commit, publish draft.

## Decisions

- Reuse a standard-library tokenizer (`shlex.shlex` with punctuation chars) instead of a second hand-written parser; `shell_readonly.split_segments` intentionally rejects constructs the security monitor must still inspect.
- Identify command segments and the executable token after leading environment assignments.
- Mark only the script/pattern argument of `sed`, `awk`, `grep`/`egrep`/`fgrep`, and `perl -e` as non-path data; later positional arguments remain path-checked.
- Genuine out-of-workspace paths (`cat /etc/shadow`, sed file arguments) must still ASK.

## Checks

- Baseline `make test` on untouched `origin/main` in this worktree: 4 failures, all in
  `tests/test_host_metrics.py` (host-specific macOS metrics collection returns `None`);
  6587 passed. Unrelated to the shell change.
- Focused after change: `tests/test_security_monitor.py tests/test_shell_approval_activity.py
  tests/test_shell_purpose.py` → 56 passed. Earlier shell suite run →
  326 passed including read-only adversarial tests. `ruff` and `pyright` clean on changed files.
- `make check`: passed (fmt, lint, pyright, generated files, JS assets).
- Full `make test` after change: same 4 host-metrics failures; 6587 passed. Two extra
  shell timing failures appeared only while a second full suite ran concurrently; both
  passed immediately when rerun alone (`test_shell_auto_background`,
  `test_shell_output_collection`).
