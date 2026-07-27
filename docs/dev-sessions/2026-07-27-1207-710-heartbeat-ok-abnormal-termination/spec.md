# Spec — #710: `is_heartbeat_ok` can be fooled by a mid-turn preamble on an abnormally-terminated turn

**Source:** https://github.com/lmorchard/decafclaw/issues/710

Captured verbatim from the issue body (the `<!-- agent-session:spec -->` marker line is
stripped; everything else is the issue as filed and augmented at intake).

---

Split out of #707, where it was measured but deliberately not fixed.

## The problem

`is_heartbeat_ok()` (`heartbeat.py:115-124`) returns True if the case-insensitive
substring `heartbeat_ok` appears **anywhere in the first 300 characters** of the turn's
delivered text. `polling.py:63` explicitly instructs the agent: *"If there is nothing to
report, respond with HEARTBEAT_OK."* So a mid-turn preamble mentioning the sentinel is a
plausible utterance, not a contrived one.

Heartbeat and scheduled turns have no live transport subscriber, so
`_finalize_with_note` delivers the accumulated iteration preambles alongside the
termination note (that is deliberate — the consumer only ever sees `ToolResult.text`).
If such a turn thrashes and terminates abnormally, a sentinel inside a preamble can land
in the first 300 chars and silently suppress the alert that should have surfaced.
Consumers: `heartbeat.py:182`, `schedules.py:516`.

## Current state after #707

#707 put the note **first** in the unwatched-turn join as a partial mitigation. Measured
with a realistic sentinel-bearing preamble:

| note | length | `is_heartbeat_ok` | outcome |
|---|---|---|---|
| `_finalize_loop_break` | ~336 chars | `False` | alert surfaces correctly |
| `_finalize_max_iterations` | ~65 chars | `True` | **alert still suppressed** |

So the loop-breaker path is protected (the note alone exceeds the 300-char window) and the
`max_tool_iterations` path is not — its note is far too short to push a sentinel out of
range. This is pre-existing behavior, not a #707 regression: on `main`,
`delivered = accumulated + note` unconditionally for every turn kind, so the hazard
predates that branch entirely.

## Suggested fix

Fix it at the consumer rather than by padding notes or reordering text — the real predicate
is "did this turn end normally," not "how long is the prefix." An abnormally-terminated turn
should never count as OK regardless of what its text happens to contain. Options:

1. Have `is_heartbeat_ok()` return False when the text carries an abnormal-termination
   marker (`[loop-breaker] Stopped`, `reached max tool iterations`). Cheap, but
   string-matching a different string to fix a string-matching bug.
2. Better: plumb the termination reason out of the turn rather than re-deriving it from
   prose. `_finalize_with_note` already knows the turn ended abnormally; carrying that as
   structured state (on the `ToolResult`, or via the existing `loop_breaker` event) lets
   both `heartbeat.py:182` and `schedules.py:516` ask directly instead of guessing from a
   substring.

Option 2 also removes the 300-character magic number, which is the root fragility here.

---

## Design decisions (resolved at intake, 2026-07-27)

**D1 — fix at the consumer by detecting the abnormal-termination marker (the issue's option 1).
Option 2 is filed separately as #712.**

Option 1 fully closes the alert-suppression bug and is purely *additive*: every existing
`is_heartbeat_ok` assertion keeps holding, so all four become guards rather than casualties.

Rejected *for this issue* (not on the merits): option 2's structured-termination plumbing. It is the
better end state — it removes the 300-char window, which is the root fragility — but it changes
`is_heartbeat_ok`'s contract, so it rewrites `test_is_heartbeat_ok_beyond_300_chars` instead of
preserving it, and it should change `is_background_wake_ok` in the same pass to avoid leaving the two
asymmetric. That is a refactor with its own scope. See **#712**.

**Correction to the issue's own measurement, found at intake:** the table reports the
`_finalize_loop_break` path as safe (`False`) because that note ran ~336 chars. That safety is
**length-contingent, not structural**. With a shorter `loop_breaker.last_signal()` the note is ~167
chars and a sentinel-bearing preamble still lands inside the window — measured `is_heartbeat_ok ==
True`. **Both** abnormal-termination paths are exposed, so C1 covers both markers rather than only
the `max_tool_iterations` one.

## Acceptance criteria

**C1 — an abnormally-terminated turn is never reported OK, wherever the sentinel sits.**
IF a turn's delivered text carries an abnormal-termination marker, THEN `is_heartbeat_ok` SHALL
return `False`, regardless of whether the sentinel appears within the first 300 characters.
- Markers, verbatim from `agent.py:1080-1097`: `[Agent reached max tool iterations` and
  `[loop-breaker] Stopped`.
- CHECK: `uv run pytest tests/test_heartbeat.py::test_is_heartbeat_ok_false_on_abnormal_termination`
- The assertion must cover **both** markers. A test covering only `max tool iterations` would pass
  while the loop-breaker path stays broken.
- Demonstrated absent at intake, both paths, with the note placed first as #707 leaves it:
  - `max_tool_iterations` note (67 chars) + `"…nothing needs attention…, so HEARTBEAT_OK."` →
    sentinel at index 134, `is_heartbeat_ok == True`.
  - `loop_breaker` note (~167 chars) + same preamble → sentinel at ~index 243,
    `is_heartbeat_ok == True`.

### Regression guards (pass today; must keep passing — not criteria)

- **G1:** `tests/test_heartbeat.py::test_is_heartbeat_ok_present`, `::test_is_heartbeat_ok_case_insensitive`,
  `::test_is_heartbeat_ok_not_present` — normal sentinel detection must not regress. A fix that
  returns `False` too eagerly would defeat the whole heartbeat-quiet mechanism and spam alerts.
- **G2:** `tests/test_heartbeat.py::test_is_heartbeat_ok_beyond_300_chars` — the 300-char window
  stays in place under option 1. #712 is where it goes away.
- **G3 (negative control):** the `is_background_wake_ok` assertions in
  `tests/test_heartbeat.py::test_is_background_wake_ok_detects_sentinel` — this fix must not
  incidentally alter the parallel sentinel path.
- Observed at intake, all together: `6 passed in 1.72s`
  (`uv run pytest tests/test_heartbeat.py -k "is_heartbeat_ok or is_background_wake_ok"`).

## What we're NOT doing

- **Not removing the 300-char window** — that is #712, and G2 pins it here.
- **Not changing `is_background_wake_ok`** — G3 pins it. Same reason.
- **Not padding notes or reordering delivered text.** The issue is explicit that the fix belongs at
  the consumer; #707 already tried ordering and it is length-contingent.
- **Not changing what `_finalize_with_note` delivers.** Delivering accumulated preambles alongside
  the note is deliberate, since the consumer only ever sees `ToolResult.text`.

## Tier: `auto-ok`

The one criterion reduces to a pure-function assertion on `is_heartbeat_ok`, demonstrated failing
today on both paths; the oracle (`tests/test_heartbeat.py`) exists and already tests this function.
No risk-gated path: no auth/authorization, secrets, data migration or deletion, deploy/infra/CI
config, or dependency change — the diff is one predicate in `heartbeat.py`. The issue's two-option
choice was the only thing withholding a decision, and D1 resolves it.

---
*Decisions resolved and criteria added via `agent-session intake`. Every check was run at intake
time, not inferred; original issue text preserved verbatim above.*
