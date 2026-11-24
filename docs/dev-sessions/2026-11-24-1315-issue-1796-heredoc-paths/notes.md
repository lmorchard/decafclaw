# issue-1796-heredoc-paths

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1796
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1796-heredoc-paths
- Branch: issue-1796-heredoc-paths
- Base: origin/main at bf8530acfa38661b148c05a416d9be4f175e030b
- Next action: implement heredoc body masking; tests first.

## Decisions

- Root cause: `shlex` has no heredoc support, so every body line becomes a token
  and segment. `/\s+/g` unquotes to `/s+/g, ` and is path-checked.
- Mask heredoc body lines (and delimiter lines) before tokenizing for the path
  check and venv detection. Detect declarations per line with the existing
  tokenizer so arithmetic `<<` (`$((1 << 2))`) and quoted `"a << b"` are not
  mistaken for heredocs; use `#` commenters for declaration detection.
- Catastrophic and sensitive patterns still scan the raw command, so body text
  remains subject to hard blocks and sensitive-command ASK rules.
- Masking removes the accidental body path scan. Compensate by treating a
  heredoc fed to a shell/interpreter as ambiguous (`is_ambiguous_command`), so
  Tier 2 still reviews the opaque script.

## Log

- Baseline `make test` on untouched origin/main bf8530ac: 4 failed (all
  `tests/test_host_metrics.py`, host-specific macOS metrics), 6795 passed,
  3 skipped.
- Tests first: the reported `agent-browser eval --stdin <<'EOF'` shape,
  path-like body text, and venv detection failed before the change.
- Implementation: `_mask_heredoc_bodies` blanks body and delimiter lines before
  the path check and venv detection. Declarations are found by tokenizing each
  line strictly, so quoted `<<`, arithmetic `<<`, comments, and unterminated
  quotes are not declarations. `_heredoc_feeds_interpreter` adds interpreter-fed
  heredocs to `is_ambiguous_command`.
- Edge probes: reported command ALLOW; body with apostrophe ALLOW; redirect and
  argument outside the body ASK; `shutdown` in a body still BLOCK via the raw
  dangerous-pattern scan; 5000-line body ALLOW in ~8ms.
- Focused shell/approval batch (10 files, `-n 0`): 451 passed, 1 skipped.
- `make check`: passed, no generated drift.
- `make test` after change: same 4 host-metrics failures, 6800 passed,
  3 skipped.

