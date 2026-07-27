# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/710
**Frozen at:** `0d08b2d` (2026-07-27) — re-anchor this sha if the branch is rebased.
**Check files — read-only from Phase 1 onward:**
- `tests/test_heartbeat.py`

Note: this file is *also* the implementation's neighbour in one respect — the guards G1–G3 and
the criterion's new test all live in `tests/test_heartbeat.py`. The whole file is frozen; the
implementation lives in `src/decafclaw/heartbeat.py`, which is disjoint from it.

## C1

CRITERION: IF a turn's delivered text carries an abnormal-termination marker, THEN
`is_heartbeat_ok` SHALL return `False`, regardless of whether the sentinel appears within the
first 300 characters.

- Markers, verbatim: `[Agent reached max tool iterations` and `[loop-breaker] Stopped`.
- The assertion must cover **both** markers. A test covering only `max tool iterations` would
  pass while the loop-breaker path stays broken.

CHECK: `uv run pytest tests/test_heartbeat.py::test_is_heartbeat_ok_false_on_abnormal_termination`

AT FREEZE: **fails**, for the correct reason — a genuine behavioural assertion, not a
collection or import error.

```
E           AssertionError: max-iterations: abnormal termination reported as OK
E           assert True is False
E            +  where True = is_heartbeat_ok('\n\n[Agent reached max tool iterations (30) without a final response]\n\nNothing new since the last check — HEARTBEAT_OK.')
tests/test_heartbeat.py:192: AssertionError
1 failed in 4.31s
```

- **pytest exit code 1**, 1 test collected, 1 failed. (Not exit 5 — the check has teeth.)
- The test's `for` loop short-circuits at the max-iterations case, so the loop-breaker half's
  failure is masked in that output. Verified independently that **both** halves fail today, so
  a partial fix covering one marker cannot pass this check:

  | marker | sentinel index | text len | `is_heartbeat_ok` today |
  |---|---|---|---|
  | `[Agent reached max tool iterations` | 104 | 117 | `True` (should be `False`) |
  | `[loop-breaker] Stopped` | 161 | 174 | `True` (should be `False`) |

- Both sentinels sit well inside the 300-char window, and the test asserts that premise
  (`sentinel_index < 300`) so it cannot silently degrade into re-testing the beyond-300 path
  that `test_is_heartbeat_ok_beyond_300_chars` already covers.
- Whole-file run at freeze: `1 failed, 32 passed` — only the new check fails.

## Guards

(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- **G1:** `uv run pytest tests/test_heartbeat.py::test_is_heartbeat_ok_present
  tests/test_heartbeat.py::test_is_heartbeat_ok_case_insensitive
  tests/test_heartbeat.py::test_is_heartbeat_ok_not_present` — normal sentinel detection must
  not regress. A fix that returns `False` too eagerly would defeat the heartbeat-quiet
  mechanism and spam alerts.
- **G2:** `uv run pytest tests/test_heartbeat.py::test_is_heartbeat_ok_beyond_300_chars` — the
  300-char window stays in place under option 1. #712 is where it goes away.
- **G3 (negative control):** `uv run pytest
  tests/test_heartbeat.py::test_is_background_wake_ok_detects_sentinel` — this fix must not
  incidentally alter the parallel sentinel path.
- **G4 (added at freeze, not in the spec):** `uv run pytest
  tests/test_agent_loop_breaker.py::test_loop_break_note_comes_first_for_unwatched_turns` —
  this test already asserts `not is_heartbeat_ok(result.text)` on a real loop-broken heartbeat
  turn, so it is a behavioural guard over the same predicate from the production side. The
  spec did not list it; it passes today and must keep passing. Recorded as a guard rather than
  a criterion precisely because it passes.

Baselines observed at freeze — every guard confirmed **passing** before implementation:

- G1–G3 together:
  `uv run pytest tests/test_heartbeat.py -k "is_heartbeat_ok or is_background_wake_ok" -q`
  → `6 passed in 1.36s` (6 collected, 6 passed). Matches intake's recorded observation.
  (This `-k` selection predates the new check, which the expression does match — after the
  freeze it selects 7. Guard verdicts are read per-test by name at the gate, not from this
  aggregate.)
- G4: `uv run pytest
  tests/test_agent_loop_breaker.py::test_loop_break_note_comes_first_for_unwatched_turns -q`
  → `1 passed in 5.02s` (1 collected, 1 passed).
- Whole suite: `3581 passed, 2 skipped in 20.62s` before the freeze commit.

## Amendments

(Append-only. Empty — no amendment was made.)

## Clarification log

(Not an amendment: no criterion or guard wording changed, and no verdict changed.)

- **CL1 — the spec's `loop_breaker` measurement does not reproduce.** The intake correction
  claims a ~167-char loop-breaker note via `loop_breaker.last_signal()`; that method does not
  exist, and `_finalize_loop_break` appends an unconditional ~193-char handoff paragraph that
  floors the note at ~296 chars. Measured: note 303 chars → sentinel at 317 →
  `is_heartbeat_ok == False`. C1's loop-breaker half still fails at freeze as a pure-function
  assertion on a directly-constructed string, so the criterion stands and still discriminates;
  what changes is only the claim that the loop-breaker path is a *live* production bug. It is
  currently guarded by note length, and C1 makes that guard unnecessary. Full reasoning in
  `notes.md`.
- **CL2 — `file:line` drift.** The spec cites `agent.py:1080-1097` for the marker strings;
  they now live at `agent.py:1150-1178`. Strings unchanged.

## Pre-squash tamper verdict

**`clean`** — recorded 2026-07-27, immediately before the squash. This record *is* the
evidence: `git reset --soft origin/main` collapses the freeze commit, so `0d08b2d` stops being
an ancestor of the branch and is absent from `origin` — the command below is not reproducible
by a reviewer or by CI afterwards.

- `git diff 0d08b2d -- tests/test_heartbeat.py` → **empty**. The one frozen check file is
  byte-identical to the freeze.
- Broadened by the independent verifier to both collection roots,
  `git diff 0d08b2d --stat -- tests contrib` → **empty**. No test file anywhere in the suite
  changed since the freeze.
- `git diff 0d08b2d --stat` → 5 files: `checks.md` (this file), `notes.md`, `docs/heartbeat.md`,
  `docs/schedules.md`, `src/decafclaw/heartbeat.py`. No lockfile, no generated file, no test
  file. All on-subject.
- This file's only change since the freeze is the `Frozen at` sha line — a sanctioned append.
  No CRITERION line, CHECK command, or guard command differs from the freeze version.
  Independently confirmed by the verifier, which quoted the one-line diff.

Not `clean-by-substitute`: `Check files` is non-empty, so the real tamper command ran and
returned a genuine empty diff rather than an absent result.

## Verification results (independent verifier, fresh context, `checks.md` + repo only)

| id | command (args after `pytest`) | exit | collected / passed | verdict |
|---|---|---|---|---|
| C1 | `tests/test_heartbeat.py::test_is_heartbeat_ok_false_on_abnormal_termination` | 0 | 1 / 1 | **pass** |
| G1 | `tests/test_heartbeat.py::test_is_heartbeat_ok_present …_case_insensitive …_not_present` | 0 | 3 / 3 | **pass** |
| G2 | `tests/test_heartbeat.py::test_is_heartbeat_ok_beyond_300_chars` | 0 | 1 / 1 | **pass** |
| G3 | `tests/test_heartbeat.py::test_is_background_wake_ok_detects_sentinel` | 0 | 1 / 1 | **pass** |
| G4 | `tests/test_agent_loop_breaker.py::test_loop_break_note_comes_first_for_unwatched_turns` | 0 | 1 / 1 | **pass** |

No check or guard collected zero (exit 5 would have been a failed check). Whole suite:
`3582 passed, 2 skipped` — the freeze baseline of 3581 plus C1's now-passing check.

The verifier also stated affirmatively that the diff could not be passing for a reason other
than doing the work: zero test surface changed, no `skip`/`xfail` added, no assertion narrowed,
and the freeze-time predicate body provably returns `True` for both of the test's fixtures.

### Unasserted properties (verifier's observation, recorded not fixed)

Two properties of the implementation are measured by no criterion or guard here: marker matching
is **case-sensitive**, and markers match against the **whole response** rather than the first
300 characters. Both are deliberate and documented, but C1 does not discriminate on either.
Coverage was NOT added, because `tests/test_heartbeat.py` is the frozen check file and adding a
variant alongside a frozen check post-freeze is not sanctioned — it would also dirty the tamper
diff above. #712 rewrites this predicate and is the place to pin both.
