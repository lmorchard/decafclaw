# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/638
**Frozen at:** 728cd7f (re-anchored after rebase onto origin/main; was c4028de pre-rebase — same tree, rewritten sha)
**Check files — see "Scoped tamper check" below (this issue edits its own oracle):**
- `tests/test_terminals.py`
- `tests/web/test_terminal_ws.py`

## C1
CRITERION: WHEN the two real-spawn tests run, the suite SHALL report no warnings.
CHECK: `uv run pytest tests/test_terminals.py tests/web/test_terminal_ws.py -q` emits no
`warnings summary` section.
AT FREEZE: **fails** — `13 passed, 2 warnings in 2.16s`, with a `warnings summary` listing two
`pty.py:95: DeprecationWarning ... forkpty()` entries (one per file). Correct reason: the
warnings are genuinely present, not a collection error.

## C2
CRITERION: WHEN the full suite runs, it SHALL report no warnings — this is the clean-suite
signal the issue is about, and `make test` is the invocation that erodes it.
CHECK: `make test` emits no `warnings summary` section.
AT FREEZE: **fails** — `3234 passed, 2 skipped, 2 warnings in 59.56s`, `warnings summary`
present. Correct reason: same two forkpty warnings.

## Guards
(Pass today; must keep passing. Not criteria — they cannot fail at freeze.)

- **G1:** `make test` exits 0 with **no test lost, newly skipped, or newly failing** relative to
  the freeze commit. Stated as an invariant, not a pinned count — upstream legitimately moves
  the totals (see Clarifications). **Passed at freeze** (`3234 passed, 2 skipped`, exit 0);
  **passes post-rebase** (`3265 passed, 2 skipped`, exit 0 — the +31 is upstream's, and this
  branch's diff vs freeze adds no tests, so nothing was lost).
- **G2:** both exempted tests still RUN and PASS — `uv run pytest
  tests/test_terminals.py::test_real_pty_echo_and_cleanup
  tests/web/test_terminal_ws.py::test_ws_handler_serves_real_spawned_session -q` → `2 passed`,
  zero skipped. Guards the degenerate "fix" of deleting or skipping the real-spawn coverage to
  make C1/C2 go green. **Passed at freeze.**
- **G3:** `make check` (ruff + pyright + tsc) green. **Passed at freeze** (exit 0).

## Scoped tamper check (this issue edits its own oracle)

The oracle here *is* the two test files, and the implementation must edit exactly those files to
add the marks. A whole-file tamper diff would flag the legitimate change, so the protection is
scoped instead:

```
git diff <freeze-sha> -- tests/test_terminals.py tests/web/test_terminal_ws.py
```

**The invariant:** no line in that diff may change what any frozen check asserts. Concretely,
any change to a test body, an assertion, a `skip`/`xfail` marker, or a function signature is
tampering and blocks the gate. Adding the `@pytest.mark.filterwarnings` marks and explanatory
comments is the sanctioned change. G2 is the behavioural backstop: the tests must still run and
pass.

Stated as an invariant deliberately, not as a whitelist of permitted line forms — see the
clarification below.

## Corrections made at freeze (pre-freeze, so not amendments)

The intake pass filed a malformed guard. Recorded here because the issue body still carries it:

- **Filed G1** was "`make test` → 3234 passed, 2 skipped **with no `warnings summary`**". Its
  second half fails today, so it was half guard, half criterion — a guard that cannot pass at
  freeze isn't a guard. Split into **C2** (no warnings summary — discriminates, fails now) and
  **G1** (counts unchanged — passes now).

## Clarifications
(Post-freeze wording fixes that change no criterion or guard. Human-adjudicated.)

- **Scoped tamper check, restated as an invariant.** As frozen it read "every added line MUST be
  a `@pytest.mark.filterwarnings(...)` decorator" — a whitelist of line *forms*, which the
  explanatory comments the plan required do not match. The independent verifier flagged the
  mismatch (correctly, reading it literally); the implementer's own mechanical check had used a
  looser regex allowing comments, i.e. had silently applied the intent rather than the letter.
  Restated as the invariant it was always meant to express: nothing may change what a check
  asserts. Comments are inert to pytest and cannot weaken an assertion.
  - **Not an amendment, and no tier change:** no criterion or guard was altered, and the
    substantive protection held throughout — the verifier confirmed on the record that no test
    body, assertion, marker, or signature was touched. Human-adjudicated before the PR.
  - The reason this matters beyond wording: a tamper rule that fires on inert changes produces
    false positives, and false positives train the operator to wave the mechanism through.

- **G1 restated as an invariant.** As frozen it pinned `3234 passed, 2 skipped`. The rebase onto
  `origin/main` brought in two upstream commits (~31 new tests), so the literal count no longer
  holds while the property it was protecting — nothing lost, newly skipped, or newly failing —
  does. Restated accordingly. Second instance of the same defect in one run: **a brittle
  absolute encoding a relative invariant.** No criterion or guard changed meaning; no tier
  change.
  - Evidence nothing was lost: this branch's diff vs the freeze commit adds only decorators and
    comments to two files (no test added or removed), so the collected set is unchanged relative
    to freeze; the delta is entirely upstream's.

- **Freeze sha re-anchored after rebase.** `c4028de` → `728cd7f`. The rebase rewrote the freeze
  commit, so the recorded sha pointed at an object outside the branch's history; a tamper diff
  against it would have conflated upstream changes with this branch's. Same tree, new sha.
  Verified upstream did not touch either frozen file (`git diff c4028de 728cd7f -- <files>` is
  empty), so the two baselines are equivalent here.

## Amendments
(Append-only. Empty unless an amendment was made post-freeze — i.e. a change to what a criterion
or guard asserts, which downgrades the run to `needs-review`.)
