# issue-1389-command-return-type

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1389 ([10.10] Unify `execute_command`/`dispatch_command` return type)
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1389-command-return-type
- Branch: issue-1389-command-return-type
- Base: origin/main at 0b42bbcc331c6fed7017c6b70879b5cd9e5b1250
- Status: implementation complete, gates green, ready to publish the PR (no merge authorization).

## Design

- `CommandMode = Literal["not_command", "help", "unknown", "fork", "inline", "error"]`
  and `CommandResult.mode: CommandMode` — the mode vocabulary is now typed.
- `execute_command` returns `CommandResult` instead of `tuple[str, str]`; it stamps
  `skill=skill` on every result.
- `dispatch_command` returns the `CommandResult` for non-inline modes and only
  `dataclasses.replace(result, display_text=...)` for inline (the archive label).
- Callers migrated: `dispatch_command`, `tests/test_commands.py`,
  `tests/test_command_tier_trust.py`, `tests/test_scoped_shell.py`. The other
  consumers (mattermost, web websocket, eval runner) already took `CommandResult`.
- No docs change: `docs/commands.md` documents frontmatter/trigger behavior, not
  return shapes.

## Verification

- `make fmt` (no reformat needed), `make check` passed (ruff, pyright 0 errors, tsc, module graph).
- `make test`: 5648 passed, 2 skipped.
