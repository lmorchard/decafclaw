# `is_heartbeat_ok` vs. abnormal termination — Implementation Plan

**Goal:** An abnormally-terminated heartbeat or scheduled turn is never reported as OK, no
matter where the HEARTBEAT_OK sentinel lands in its delivered text.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/710 — **Tier:** `auto-ok`
(the one criterion reduces to a pure-function assertion on `is_heartbeat_ok`, the oracle
already exists in `tests/test_heartbeat.py`, and the diff touches no auth, secret, migration,
deploy/CI, or dependency surface)

**Approach:** D1 from the spec — fix at the consumer. `is_heartbeat_ok` returns `False` when
the response carries an abnormal-termination marker, before the sentinel window is consulted at
all. The structured-termination plumbing that would remove the 300-char window is deliberately
out of scope and filed as #712; guard G2 pins the window in place here.

**Criteria:** C1 — an abnormally-terminated turn is never reported OK, wherever the sentinel
sits, for **both** markers. (Full text + check in `checks.md`.)

---

## Phase 0: Freeze the acceptance checks — DONE

Written per `references/frozen-checks.md`. No implementation in this phase.

**Files:**
- Create: `docs/dev-sessions/2026-07-27-1207-710-heartbeat-ok-abnormal-termination/checks.md`
- Modify: `tests/test_heartbeat.py` — adds
  `test_is_heartbeat_ok_false_on_abnormal_termination`, authored by a check-author subagent
  that was given the criterion but not the implementation approach

**Verification — automated:**
- [x] C1's check runs and **fails for the expected reason**: `AssertionError: max-iterations:
      abnormal termination reported as OK / assert True is False`. pytest exit 1, 1 collected,
      1 failed — not exit 5. Both marker halves independently verified failing (see
      `checks.md` AT FREEZE), so a one-marker fix cannot pass.
- [x] Every guard runs and **passes**: G1–G3 `6 passed in 1.36s`; G4 `1 passed in 5.02s`.
- [x] Freeze commit made; sha recorded in `checks.md` in a follow-up commit.

---

## Phase 1: Reject abnormal termination in `is_heartbeat_ok`

The whole fix, end to end: the predicate learns that an abnormally-terminated turn is never
OK, the docs that describe the predicate's contract are updated to match, and C1's frozen check
goes green.

**Advances:** C1 — fully; nothing remains for a later phase.

**Files:**
- Modify: `src/decafclaw/heartbeat.py` — add the marker constant and the early return in
  `is_heartbeat_ok`
- Modify: `docs/heartbeat.md` — the `## HEARTBEAT_OK` section (line 78) states the contract as
  "case-insensitive, within the first 300 characters" with no mention of the override
- Modify: `docs/schedules.md` — line 124 describes the same predicate from the scheduler's side
- Test: none of the implementer's own. C1's frozen check in `tests/test_heartbeat.py` is the
  test for this slice, and it is **read-only from here on** — the whole file is frozen

**Key changes:**

`src/decafclaw/heartbeat.py`, above `is_heartbeat_ok`:

```python
# Substrings that mark a turn as abnormally terminated, emitted verbatim by
# agent.py's _finalize_max_iterations / _finalize_loop_break. A turn that ended
# this way is never "nothing to report", whatever its text happens to contain —
# see is_heartbeat_ok.
_ABNORMAL_TERMINATION_MARKERS = (
    "[Agent reached max tool iterations",
    "[loop-breaker] Stopped",
)
```

and the predicate becomes:

```python
def is_heartbeat_ok(response: str | None) -> bool:
    """Check if a response indicates nothing to report.

    Returns True if HEARTBEAT_OK appears (case-insensitive) within the first
    300 characters — but always False for an abnormally terminated turn.

    The override exists because heartbeat and scheduled turns have no live
    transport subscriber, so agent.py's _finalize_with_note delivers the
    turn's accumulated mid-turn preambles alongside the termination note. Since
    polling.py tells the agent to say HEARTBEAT_OK when there is nothing to
    report, a preamble mentioning the sentinel is a plausible utterance, and one
    landing inside the 300-char window used to suppress the very alert the
    abnormal termination should have raised (#710). The real predicate is "did
    this turn end normally", not "how long is the prefix".

    Markers are matched against the whole response, not the first 300
    characters: #707 puts the note first, but scoping the marker check to the
    window would quietly re-couple this to that ordering. #712 tracks replacing
    the substring match with a structured termination signal.
    """
    if not response:
        return False
    if any(marker in response for marker in _ABNORMAL_TERMINATION_MARKERS):
        return False
    return "heartbeat_ok" in response[:300].lower()
```

Marker matching is case-**sensitive** deliberately: these are literal strings the code emits,
not user prose, and an exact match is the narrower reading. The sentinel search stays
case-insensitive, unchanged.

`docs/heartbeat.md` — extend the contract sentence at line 78 with the override and a pointer
to why it exists. `docs/schedules.md` — same, at line 124.

**Verification — automated:**
- [ ] C1's check passes: `uv run pytest
      tests/test_heartbeat.py::test_is_heartbeat_ok_false_on_abnormal_termination`
- [ ] G1 still passes: `uv run pytest tests/test_heartbeat.py::test_is_heartbeat_ok_present
      tests/test_heartbeat.py::test_is_heartbeat_ok_case_insensitive
      tests/test_heartbeat.py::test_is_heartbeat_ok_not_present`
- [ ] G2 still passes: `uv run pytest
      tests/test_heartbeat.py::test_is_heartbeat_ok_beyond_300_chars`
- [ ] G3 still passes: `uv run pytest
      tests/test_heartbeat.py::test_is_background_wake_ok_detects_sentinel`
- [ ] G4 still passes: `uv run pytest
      tests/test_agent_loop_breaker.py::test_loop_break_note_comes_first_for_unwatched_turns`
- [ ] `make check` passes (lint + typecheck, Python and JS)
- [ ] `make test` passes with no regression against the `3581 passed, 2 skipped` baseline

**Verification — manual:**
- [ ] None. C1 is a pure-function assertion with no human-judgment component, which is what
      makes this run `auto-ok`. No live Mattermost / web-UI check is meaningful for a predicate
      change of this shape — reproducing a real thrashing heartbeat turn on a live bot to watch
      an alert *not* get suppressed is what G4 already does deterministically in-process.

---

## Not in this plan

- **No eval case.** `make eval-tools` and `evals/*.yaml` cover LLM-visible behaviour — tool
  descriptions, routing, prompt changes. This is a pure predicate on text the code itself
  emits; nothing about the model's choices changes. Per CLAUDE.md's "skip evals for
  non-LLM-visible work".
- **No change to `is_background_wake_ok`** (G3 pins it) and **no removal of the 300-char
  window** (G2 pins it). Both belong to #712.
- **No change to `_finalize_with_note`, note text, or delivery ordering.** The spec rules all
  three out, and G4 depends on the current ordering.
