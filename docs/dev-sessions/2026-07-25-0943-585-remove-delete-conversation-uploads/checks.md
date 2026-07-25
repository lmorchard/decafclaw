# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/585
**Frozen at:** 3adffe6 (2026-07-25) — re-anchored after rebasing onto `origin/main` (`e81f1ba`).
Original freeze sha was `c91189f`; the rebase rewrote it. Same tree confirmed:
`git diff c91189f 3adffe6 --stat` shows only upstream `e81f1ba`'s three files
(`CLAUDE.md`, `pyproject.toml`, `tests/test_pytest_collection.py`) and no change to any file in
this session directory. Diff against `3adffe6` from here on.

**Check files — read-only from Phase 1 onward:**

**None — this list is deliberately empty.** Both criteria are satisfied by a `grep` command,
not by a test file, so there is no frozen test for the implementer to be held off. That makes
`git diff <freeze-sha> -- <check files>` meaningless rather than satisfied: an empty diff there
would be an *absent* result, not a clean one. The three substitutes from
`references/frozen-checks.md` ("When the criteria are commands, not test files") apply instead —
manifest integrity, byte-equality against the issue body, and a collateral-edit scan — and the
tamper verdict must be reported as `clean-by-substitute` with its basis.

All commands below are run from the repository root.

## C1
CRITERION: the orphaned function is gone.
CHECK: `grep -c "def delete_conversation_uploads" src/decafclaw/attachments.py` returns `0`.
AT FREEZE: fails — returns `1` (`src/decafclaw/attachments.py:96`). Correct reason: the function
is genuinely still defined, not a bad path (the file exists and the grep matched).

## C2
CRITERION: its dedicated tests are gone with it.
CHECK: `grep -c "def test_delete_conversation_uploads" tests/test_attachments.py` returns `0`.
AT FREEZE: fails — returns `2` (`test_delete_conversation_uploads`,
`test_delete_conversation_uploads_noop_if_missing`). Correct reason: both test functions are
genuinely still defined.

## Guards
(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- **G1:** `uv run pytest tests/test_attachments.py -q` — the rest of the attachment suite
  (save / list / read / `uploads_dir` sandboxing). At freeze: `11 passed in 1.26s`, exit 0
  (collected 11, not zero — the check has teeth).
- **G2:** the real production delete path is untouched —
  `grep -n "delete_conversation_files" src/decafclaw/http_server.py` still shows the import and
  the call. At freeze: 2 hits (lines 696, 713).

### Note on gameability

Both criteria assert over *text*, so a rename, a reflow across two lines, or a comment-out would
drive `grep -c` to `0` with the behaviour untouched. G1 is the behavioural counterweight: after
the removal the attachment suite must still collect and pass its remaining tests, and G2 pins the
production delete path that is supposed to be the sole remaining caller-free route. The verifier
must state plainly whether the diff could satisfy C1/C2 without actually doing the work.

## Amendments
(Append-only. Empty unless an amendment was made.)

_None._

## Tamper verdict (recorded pre-squash)

**`clean-by-substitute`** — recorded before `git reset --soft origin/main` collapsed the freeze
commit, because after the squash `3adffe6` is a dangling local object and the commands below are
no longer reproducible by a reviewer or by CI. This record *is* the evidence.

`Check files` is empty, so `git diff 3adffe6 -- <check files>` is meaningless rather than
satisfied. The three substitutes ran instead:

**(a) Manifest integrity — invariant holds.**
`git diff 3adffe6 -- <session-dir>/checks.md` is non-empty, as the freeze procedure guarantees,
so the verdict is stated as an invariant rather than an equality: **no CRITERION line, no CHECK
command, and no guard command differs from the freeze version.** `Amendments` is still `_None._`.

Every hunk in that diff is one of the three sanctioned, inert appends — the `Frozen at` sha
(with its post-rebase re-anchor note), and this tamper-verdict section replacing its `_(pending)_`
placeholder. Don't count the hunks here: this section is itself part of the diff it describes, so
any fixed number goes stale the moment it's written. The invariant is what holds.

**(b) Equality against the independent source — identical.**
The independent verifier compared each CHECK and guard command in this manifest to GitHub issue
585's body byte-for-byte (including an `od -c` scan for smart quotes) and found them identical.
Substitute (a) then shows no command line has changed since, so the equality still holds.

**(c) No collateral edits.** Use `git diff origin/main...HEAD --stat`, **not**
`git diff 3adffe6 --stat`. The freeze sha now predates several upstream commits in the branch's
history, so a bare diff against it blends upstream's files in and a reader would flag them as
unexplained collateral. The branch-isolated form is the one that answers the question:

| File | Explained by |
|---|---|
| `<session-dir>/checks.md` | the sanctioned appends in (a) |
| `<session-dir>/spec.md`, `<session-dir>/notes.md`, `<session-dir>/plan.md` | session artifacts, no code effect |
| `src/decafclaw/attachments.py` | C1, plus the module docstring the removal made stale |
| `tests/test_attachments.py` | C2 |

Six files. No test file outside the one C2 names was touched, and no source file outside
`attachments.py`.

**Gameability (the question a text-asserting check has to answer).** The verifier read the diff
and reported the function genuinely deleted — not renamed, moved, commented out, or reflowed
across lines — with `grep -rn delete_conversation_uploads src/ tests/` returning no matches, and
the import entry removed too (a rename or move would have had to leave an importable target).
The behavioural counterweights held: G1 still collects and passes 9 tests, G2 still shows the
production import and call at `http_server.py:696,713`.
