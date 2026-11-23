# issue-1771-normalize-file-ops

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1771
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1771-normalize-file-ops
- Branch: issue-1771-normalize-file-ops
- Base: origin/main at 1ef234f671c053973f0f5b08e96afb475f1f2f84
- Next action: review the committed diff, then publish the PR.

## Decisions

- `vault_show_sections` retired (issue allowed aligning it with
  `workspace_outline` or retiring it). `vault_read` with
  `start_line`/`end_line` covers the inspection use, and `workspace_outline`
  is the file-structure tool; a second heading-outline tool was the sprawl
  the issue targets.
- `vault_replace_lines` added: the issue title names it, and it is the
  intra-page counterpart of `workspace_replace_lines`. `vault_move_lines`
  (line arithmetic *between* pages) stays removed.
- `KIND_MOVE` / `KIND_SECTION` vault event kinds removed with their only
  emitter (`_sections_ops.py`). Clients re-fetch on every `vault_changed`
  regardless of kind, so nothing consumes them.
- `_sections.py` (Document model) stays: garden's `vault_apply_link_plan`
  still resolves and inserts into named sections deterministically.
- The shared engine also absorbed `_mini_diff`/`content_version`/
  `split_physical_lines`/`edit_not_found_hint`, and `admin_tools` now imports
  `_file_common` helpers instead of keeping copies.

## Evidence

- `make check`: pass (ruff, pyright 0 errors, generated types/API client
  clean, JS checks).
- `make test`: 6783 passed, 3 skipped, 4 failed — all four are the known
  `tests/test_host_metrics.py` host-environment failures on macOS 27.
- `make eval-tools` (vertex-gemini-flash): 55/65. The three new #1771 cases
  pass (`vault-edit-typo-in-view`, `vault-append-vs-write`,
  `vault-replace-lines-vs-edit`). Ten unrelated cases failed; no baseline
  comparison was run in this session.
- `make eval-history`: no records (the tool-choice eval does not write
  history in this worktree).

## Log

- Created the shared `decafclaw.text_patching` engine; rewired workspace,
  admin, and prompt tools.
- Added `vault_edit`, `vault_append`, `vault_replace_lines`; added line
  slicing to `vault_read`; retired `vault_section`, `vault_move_lines`,
  `vault_show_sections`.
- Added `admin_append` (confirmation-gated, replay-handled).
- Updated delegate/toil tool sets, skill guide, docs, and evals.
