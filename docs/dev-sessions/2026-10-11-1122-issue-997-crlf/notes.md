# Issue #997 — workspace_edit: CRLF preservation + near-match hints

## Resume here

- DONE (local). Branch `issue-997-crlf-workspace-edit` @ worktree `/Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-997-crlf-workspace-edit`, base `987bd0c914b0c5cd5d30a67568fa2184c2bd85ce` (origin/main).
- Commits (MokaGnome): `3a5c67ecfac0818f99b4248a2b5fbdd4d90708bc` (fix + tests + doc), `228106cb1d8919aee650807afaed4aa1ffb2d5cc` (these notes). Working tree clean.
- Next action (coordinator, not this worker): push branch + open PR + move board to In review. Do NOT merge.
- Board: moved Ready -> In progress (2026-10-11).

## Findings

- `origin/main` tip confirmed `987bd0c...` before branching (local `main` is 2 commits behind; ignored per instructions).
- Python 3.13.15 in worktree venv: `Path.read_text(newline=...)` and `Path.write_text(data, newline=...)` are available (added in 3.13), so the codebase's `read_text`/`write_text` idiom is kept — no `open()` needed.
- Issue correction confirmed: `read_text()` (universal newlines) means an LF `old_text` already matches a CRLF file; the defect is the *write* converting CRLF->LF, plus CRLF `old_text` never matching, plus no hint on not-found.
- Baseline (before any change): `make test` -> 4499 passed, 2 skipped, exit 0.
- `test_edit_not_found` (old_text "nonexistent" vs "def hello():\n    pass\n") triggers neither hint path, so its asserted text is unchanged.

## Implementation plan

In `tool_workspace_edit` (workspace_tools.py:465):
1. `content = resolved.read_text(newline="")` — no conversion on read.
2. `eol = "\r\n" if "\r\n" in content else "\n"`.
3. Normalize file text + `old_text` + `new_text`: `.replace("\r\n", "\n")`. Match/replace on normalized.
4. On exact match only: write `new_normalized.replace("\n", eol)` via `write_text(..., newline="")`.
   - Mixed-line-ending files: `eol` heuristic wins for newlines; never fails (out of scope to preserve precisely).
   - Lone `\r` (old-Mac) untouched by the `\r\n`-only replace; preserved.
5. Not-found hint (count == 0, file untouched):
   a. Whitespace-insensitive match: `re.search(re.sub(r"\s+", r"\\s+", re.escape(old_norm)), content_norm)`; if hit, 1-based line = `content_norm.count("\n", 0, m.start()) + 1`.
   b. Else near match: first non-blank line of old_norm vs `difflib.get_close_matches(line, file_lines, n=1, cutoff=0.8)`; report line number + text.
   c. Else existing error text unchanged.
6. Doc: one sentence in the workspace_edit row of docs/tools.md.

## Test mapping (tests/test_workspace_tools.py)

- CRLF file + LF old/new -> `read_bytes()` all `\r\n`: test_edit_crlf_file_with_lf_old_text_preserves_crlf
- CRLF file + CRLF old_text succeeds, keeps `\r\n`: test_edit_crlf_file_with_crlf_old_text
- LF file + CRLF old_text succeeds, keeps `\n`: test_edit_lf_file_with_crlf_old_text
- whitespace-only-diff old_text -> hint + line number, bytes unchanged: test_edit_whitespace_only_mismatch_hint
- near-copy first line -> hint names line number, bytes unchanged: test_edit_near_match_hint
- Existing: test_edit_simple/_not_found/_ambiguous/_replace_all/_multiline/_returns_diff/_does_not_redirect_to_fuzzy_match.

## Decisions

- Hint text format: appended to the existing not-found error, one line each:
  - whitespace: "It matches ignoring whitespace differences — the match starts at line N."
  - near: "Closest match: line N: '<text>'"
- Mini-diff computed on the raw (line-ending-preserved) content, as before.
- No shared helper for insert/replace_lines/append (out of scope per issue; follow-up issue).

## Commands & results

- `git worktree add -b issue-997-crlf-workspace-edit .claude/worktrees/issue-997-crlf-workspace-edit origin/main` -> ok, HEAD 987bd0c.
- `uv sync` -> 84 packages, CPython 3.13.15.
- `.env` copied + `HTTP_PORT=8791`.
- `ghflow identity` -> MokaGnome ready.
- Board: Ready -> In progress (readback confirmed).
- Baseline `make test` (pre-change) -> 4499 passed, 2 skipped, EXIT=0.
- Test-first: new tests failed as expected (CRLF write converted to LF; CRLF old_text "not found"; no hints).
- After fix: targeted edit tests 18/18 passed.
- `make fmt` -> 429 files unchanged.
- `make check` -> EXIT=0 (install-js, check-message-types, fmt-check, ruff, pyright 0 errors, gen-api-client + tsc, module-graph test 2 passed).
- `make test` (final) -> 4507 passed, 2 skipped, EXIT=0.
- No eval run (tool description unchanged, per issue).
