# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/586
**Frozen at:** 9347eb3 (2026-07-24)
**Check files — read-only from Phase 1 onward:**
- *(none)* — both criteria are `grep` commands over the implementation file itself. There is no
  acceptance-test file to author, so the `Check files` list is empty and the `git diff <freeze-sha>`
  tamper check is vacuous for this run. The oracle for this run lives entirely in **this file**,
  which the tamper baseline does not cover. See the run note at the bottom.

## C1
CRITERION: the redundant guard block is gone.
CHECK: `grep -c 'if not conversations_dir.exists():' src/decafclaw/conversation_manager.py` returns `0`.
AT FREEZE: fails — returns `1` (`src/decafclaw/conversation_manager.py:1809`). Correct reason:
the guard is genuinely present, not a path/typo error (the same grep with the file omitted errors
differently, and `grep -n` locates the line).

## C2
CRITERION: the now-unused local is gone with it.
CHECK: `grep -c 'conversations_dir = self.config.workspace_path' src/decafclaw/conversation_manager.py` returns `0`.
AT FREEZE: fails — returns `1` (`src/decafclaw/conversation_manager.py:1808`). Correct reason as
above: `grep -n` shows the assignment on that line.

## Guards
(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- G1: `uv run pytest tests/test_conversation_manager.py -k startup_scan -q` — `startup_scan`
  behaviour unchanged: still recovers pending confirmations, still returns 0 on an empty/missing
  conversations dir, still respects the 24h staleness cutoff, still skips resolved confirmations.
  Passed at freeze: `12 passed in 1.60s`, 12 tests collected (not 0 — the `-k` filter matches).
- G2 (project gate): `make test` — full suite. Passed at freeze:
  `3338 passed, 2 skipped in 36.58s`. Recorded as a *relative* invariant, not an absolute count:
  nothing previously passing may be lost, newly skipped, or newly failing. (An absolute count
  here would trip on any upstream test added during a rebase — the #638 run hit exactly that.)

## Amendments
(Append-only. Empty unless an amendment was made.)

*None.*

## Tamper verdict, recorded pre-squash

Recorded here because `git reset --soft origin/main` collapses the freeze commit and `9347eb3`
stops being reachable from the branch afterwards. This record is the durable evidence the gate
cites.

Run at `26e8dab`, immediately before the squash:

- `git diff 9347eb3 -- <Check files>` — **vacuous**: `Check files` is empty, so there is nothing to
  diff. This is not a clean tamper diff; it is an *absent* one. See the run note above.
- `git diff 9347eb3 --stat` — 3 files: this manifest (1 line), `plan.md` (new), and
  `src/decafclaw/conversation_manager.py` (−4). **No test file touched.**
- `git diff 9347eb3 -- checks.md` — one line, the `Frozen at:` placeholder replaced by the sha.
  No CRITERION, CHECK, or guard command altered. (Note: this diff can never be empty, because the
  contract's own freeze procedure records the sha in a follow-up commit.)
- Independent verifier cross-checked C1's, C2's, and G1's commands **byte-for-byte against the
  issue body** — identical, including under `cat -vet`. The issue is the pre-existing record the
  manifest was copied from, so this substitutes for the missing file-level baseline.

**Verdict: no tampering detected.** Basis is the manifest-vs-issue equality plus the
no-test-files-touched stat, not a `Check files` diff.

---

## Run note — the tamper baseline does not cover this manifest

Both of C1/C2 are shell commands rather than test files, so:

- Freeze step 2 ("author the tests the checks name") is a no-op — nothing to author.
- `Check files` is empty, so the read-only rule protects nothing and
  `git diff <freeze-sha> -- <Check files>` is trivially empty.
- The full text of the oracle is the two CHECK lines *in this file*, and `checks.md` is not
  itself listed in the tamper baseline. An implementer that edited a CHECK command here would not
  be caught by any mechanical check in the contract.

Mitigation for this run: the CHECK commands are copied verbatim from the issue body, and the
issue body is the independent, pre-existing record. The verifier is instructed to take the
commands from the **issue**, not from this file, and the diff of this file against the freeze
commit is reported alongside the (vacuous) `Check files` diff.
