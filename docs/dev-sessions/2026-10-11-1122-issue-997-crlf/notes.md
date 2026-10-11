# Issue #997 — workspace_edit: CRLF preservation + near-match hints

## Resume here

- DONE (local, pushed). Branch `issue-997-crlf-workspace-edit` @ worktree `/Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-997-crlf-workspace-edit`, base `987bd0c914b0c5cd5d30a67568fa2184c2bd85ce` (origin/main). Draft PR #1060.
- Commits (MokaGnome): `3a5c67ecfac0818f99b4248a2b5fbdd4d90708bc` (fix + tests + doc), `067ea0ba9f531364cf499cb381fda1dffd2b2065` (these notes), then `fix(workspace_tools): repair whitespace-insensitive hint and its tests (#997)` (review fixes D1/D2/M2). Working tree clean.
- Next action (coordinator, not this worker): independent re-review of the D1/D2 fixes, then Les's review. Do NOT merge.
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
    a. Whitespace-insensitive match: pattern = `r"\s+".join(re.escape(p) for p in re.split(r"\s+", old_norm) if p)` (escape non-whitespace segments, join with `\s+`), then `re.search(pattern, content_norm)`; if hit, 1-based line = `content_norm.count("\n", 0, m.start()) + 1`.
    b. Else near match: first non-blank line of old_norm vs `difflib.get_close_matches(line, file_lines, n=1, cutoff=0.8)`; report line number + text.
    c. Else existing error text unchanged.
 6. Doc: one sentence in the workspace_edit row of docs/tools.md.

## Test mapping (tests/test_workspace_tools.py)

- CRLF file + LF old/new -> `read_bytes()` all `\r\n`: test_edit_crlf_file_with_lf_old_text_preserves_crlf
- CRLF file + CRLF old_text succeeds, keeps `\r\n`: test_edit_crlf_file_with_crlf_old_text
- LF file + CRLF old_text succeeds, keeps `\n`: test_edit_lf_file_with_crlf_old_text
- LF file + LF edit unchanged: test_edit_lf_file_stays_unchanged_in_behavior
- whitespace-only-diff old_text -> tier-1 hint + line number, bytes unchanged: test_edit_whitespace_only_mismatch_gets_line_hint, test_edit_whitespace_hint_line_number_on_later_line (both assert the tier-1 phrase, unique to the whitespace hint)
- near-copy first line -> tier-2 hint names line number, bytes unchanged: test_edit_near_match_hint_names_line
- nothing close -> base error only: test_edit_no_hint_when_nothing_is_close
- Existing: test_edit_simple/_not_found/_ambiguous/_replace_all/_multiline/_returns_diff/_does_not_redirect_to_fuzzy_match.

## Decisions

- Hint text format: appended to the existing not-found error, one line each:
  - whitespace (tier 1): "It matches when whitespace differences are ignored — the match starts at line N."
  - near (tier 2): "A similar line exists at line N: <text>"
- Mini-diff is computed on the NORMALIZED (LF) content, not the raw file bytes — the diff output stays free of stray `\r`; for LF files normalized == raw, so output is identical to before.
- No shared helper for insert/replace_lines/append (out of scope per issue; follow-up issue).

## Review findings (2026-10-11, draft PR #1060)

- **D1 (fixed):** the tier-1 pattern `re.sub(r"\s+", r"\\s+", re.escape(old_text))` was dead code — `re.escape` escapes whitespace in 3.13, so the sub baked literal backslashes in and the pattern matched neither the exact nor the expanded whitespace. The tier-1 hint therefore never fired, and the two whitespace-hint tests passed via the base error text / tier-2 hint. Replaced with the segment-join construction (see plan step 5a); verified in the venv that `old_text="a b"` -> pattern `a\s+b` matches both `"a b"` and `"a    b"`, and that the indentation-drift and two-spaces scenarios hit tier 1 at the correct lines.
- **D2 (fixed):** the two whitespace-hint tests now assert the tier-1-specific phrase "whitespace differences are ignored" (bare "whitespace" matches the base error, so it proved nothing). `test_edit_whitespace_hint_line_number_on_later_line` got a genuine whitespace-run scenario (file line 2 `line  two` vs old_text `line two`); the old "sec ond" scenario was a typo, not a whitespace-run difference, and belongs to tier 2.
- **M2 (fixed):** these notes corrected (mini-diff on normalized content; tier-1 construction; hint wording).
- CRLF-preservation logic: review-confirmed solid, untouched. No fuzzy replacement; sibling tools untouched.

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
- Review round (D1/D2/M2): targeted edit tests 18/18; `make fmt` clean; `make check` and `make test` green on the new head (see PR #1060); pushed under MokaGnome, `verify-commit` + PR head read-back confirmed.
