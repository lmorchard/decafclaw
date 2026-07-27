# Notes — #710 `is_heartbeat_ok` vs. abnormal termination

Mode: `agent-session express`. Tier `auto-ok`. Started 2026-07-27 12:07.

## Setup

- Worktree `.claude/worktrees/fix-710-heartbeat-ok-abnormal-termination`, branch
  `fix/710-heartbeat-ok-abnormal-termination` off `origin/main` @ `076f89e`.
- Baseline `make test`: `3581 passed, 2 skipped in 20.62s`.
- Guards baseline: `uv run pytest tests/test_heartbeat.py -k "is_heartbeat_ok or
  is_background_wake_ok" -q` → `6 passed in 1.36s`. Matches intake's recorded observation.
- Board: #710 → In progress.
- Deviation: appending `HTTP_PORT` to the worktree `.env` was blocked by the sandbox. No
  server is started in this run, so it costs nothing here.

## Readiness gate (express Phase 0)

Applied the **augmented existing issue** variant of the readiness checklist — the body is the
original author's text with `## Design decisions` / `## Acceptance criteria` / guards / tier
appended by `intake` below a `---`, not a spec written to the template. Items 1–5 and 7 pass;
item 6 passes in its variant form (an explicit "What we're NOT doing" section bounds scope).

## Re-confirming the spec's evidence (plan step 4)

The spec's `file:line` refs had drifted — `agent.py:1080-1097` now reads
`agent.py:1150-1178` (`_finalize_max_iterations` / `_finalize_loop_break`). The marker
strings themselves are unchanged and still verbatim correct.

### `max_tool_iterations` path — reproduces as claimed

Note `[Agent reached max tool iterations (25) without a final response]` (65 chars after
`.strip()`, spec said 67) + `"Nothing needs attention on the feed right now, so
HEARTBEAT_OK."` → sentinel at index **117**, `is_heartbeat_ok == True`. The bug is live.

### `loop_breaker` path — the spec's measurement does NOT reproduce

The intake correction claims that with "a shorter `loop_breaker.last_signal()`" the note runs
~167 chars, putting a sentinel-bearing preamble back inside the 300-char window
(`is_heartbeat_ok == True`). Measured against current code, that is not reachable:

- There is no `last_signal()` on `LoopBreaker`. `_finalize_loop_break` uses `offense()`
  (`loop_breaker.py:277`).
- `_finalize_loop_break` appends an **unconditional** ~193-char handoff paragraph ("I stopped
  rather than retry again. …"). With the 69 chars of fixed framing around `off.reason`, the
  note has a hard floor of ~264 + `len(reason)` chars — ~296 at the very shortest reason the
  two `Offense` builders can produce (`called <3-char-tool> 3× with the same args`).
- Measured with a realistic short reason (`called check_feed 3× with the same args`, no
  `error_text`): note = 303 chars, sentinel at 317 (long preamble) / 317 (short preamble),
  `is_heartbeat_ok == False` both ways.

So in production, under #707's note-first ordering, the loop-breaker path is currently
**not** exposed for any plausible preamble — the sentinel can only land inside the window if
the note lands under ~298 chars AND the first preamble begins with the sentinel within 2
characters. The issue's *original* table was right, and right for a structural reason the
intake correction talked itself out of: the unconditional handoff paragraph is the floor.

**What this changes, and what it doesn't.** It does not change C1, its check, or the tier:

- C1 asserts a contract on the pure predicate `is_heartbeat_ok`, not on what
  `_finalize_with_note` happens to emit. As a pure-function assertion **both** halves fail at
  freeze — a directly-constructed string carrying `[loop-breaker] Stopped` with the sentinel
  inside the window returns `True` today.
- Covering the loop-breaker marker is therefore *defensive* rather than a live-bug fix: it
  stops the loop-breaker path's safety from depending on the note-length floor, which is the
  exact length-contingency the issue set out to remove. It is not a criterion that already
  passes, so `frozen-checks.md`'s "a check passes at freeze → stop" does not fire.
- No check or guard wording changes, so no verdict changes: this is a clarification of the
  record, not an amendment. Tier stays `auto-ok`.

Recorded here and in the PR body so the next reader doesn't inherit the wrong measurement.

## Freeze

See `checks.md`. Freeze commit `0d08b2d`, per-criterion `AT FREEZE` output recorded there.

## Execute

One phase, as planned: the marker tuple + early return in `is_heartbeat_ok`, plus the two docs
pages that state the predicate's contract (`docs/heartbeat.md` for heartbeat,
`docs/schedules.md` for the scheduler). No implementer subagent — the slice is one predicate.

All five gate commands pass individually: C1 `1 passed`; G1 `3 passed`; G2 `1 passed`; G3
`1 passed`; G4 `1 passed`. Full suite `3582 passed, 2 skipped` against the `3581 passed,
2 skipped` baseline — the +1 is C1's check.

### `make check` is blocked by a pre-existing lockfile guard

`make check` fails at its first step, `install-js`, before reaching anything this branch
touches:

```
ERROR: npm install rewrote package-lock.json during a check.
```

That is #709's guard (added to catch #706's silent lockfile rewrites) firing correctly on a
condition that predates this branch. `npm install` deterministically prunes 27 nested
`node_modules/vitest/node_modules/@esbuild/*` platform-optional entries — 512 lines — from the
committed lockfile, every run, with `node_modules` already present. This branch modifies no JS
and no dependency file, and the failure reproduces with the lockfile at its committed
`origin/main` content, so it is not caused by this work. **Worth a separate issue:** in a fresh
worktree `make check` / `make check-js` / `make test-js` cannot pass at all until the lockfile
is regenerated, which makes the guard un-actionable rather than protective there.

Ran all four of `check`'s steps natively instead, and all four pass:

- `make check-message-types` → clean (no diff)
- `uv run ruff check src/ tests/` → `All checks passed!`
- `uv run pyright` → `0 errors, 0 warnings, 0 informations`
- `npx tsc --noEmit` in `src/decafclaw/web/static` → clean

Also worth recording as a near-miss: `git commit -a` swept the npm-rewritten lockfile into the
Phase 1 commit (512 deletions). Caught by reading the commit's `--stat`, amended out. The
lockfile guard's failure mode and `-a`'s breadth compound each other.

## Independent verification

Dispatched with `checks.md` and the repo only — no plan, no notes, no rationale. Reported:
C1 `pass` (exit 0, 1 collected / 1 passed); G1 `pass` (3/3); G2 `pass` (1/1); G3 `pass` (1/1);
G4 `pass` (1/1); no check or guard collected zero. Tamper diff
`git diff 0d08b2d -- tests/test_heartbeat.py` **empty**; broadened to `-- tests contrib`, also
empty. `checks.md`'s only change since the freeze is the `Frozen at` sha line — no CRITERION,
CHECK, or guard command differs. It confirmed the diff could not be passing for a reason other
than doing the work: zero test surface changed, no skip/xfail added, the freeze-time body
provably returns `True` for both of the test's fixtures.

### Known limit the verifier flagged, recorded rather than fixed

Two properties of the implementation are asserted by no criterion or guard: marker matching is
**case-sensitive**, and markers are matched against the **whole response** rather than the
first 300 characters. Both are deliberate and documented in the docstring and
`docs/heartbeat.md`, but C1 doesn't discriminate on either — its fixtures use exact-case
literals with the markers inside the window.

Not fixed here on purpose: `tests/test_heartbeat.py` is the frozen check file, so adding
coverage to it after the freeze would make the tamper diff non-empty, and `frozen-checks.md` is
explicit that adding a passing variant alongside a frozen check is not sanctioned. The honest
handling is to record the gap. #712 rewrites this predicate anyway, and that is the natural
place to pin both properties.

## Rebase (initial, pre-PR)

`origin/main` had not advanced (`git log HEAD..origin/main` empty), so no rebase was needed and
the freeze sha `0d08b2d` stays reachable — no re-anchoring, and the verifier's report describes
the current tree.

## Resume rebase (post-PR, after host crash)

Session resumed after host crashed mid-review-cycle. `origin/main` had advanced to `3af093d`
(#716/#717 — the lockfile install-js fix). Rebased the branch:
- `git rebase origin/main` → success, no conflicts
- New HEAD: `3be3b07`
- Freeze sha re-anchoring: `git diff 0d08b2d HEAD -- tests/test_heartbeat.py` → empty, check file
  byte-identical. The old freeze sha is still valid for the tamper verdict.
- Re-ran criteria and guards post-rebase: `make test` → `3582 passed, 2 skipped` (unchanged)
- `make check` now passes (the #716/#717 fix changed install-js to use `npm ci`)
- CI re-triggered on force-push, watched with `gh pr checks 714 --watch`
- CI: 2/2 pass (lint-and-test, js-test)
- Review threads: 0 unresolved (copilot review landed with COMMENTED state, 0 inline comments)

## Self-review of `git diff origin/main..HEAD`

No bugs, no incomplete changes, no convention violations. Specifically checked: both consumers
(`heartbeat.py:182`, `schedules.py:516`) call through the predicate, so neither needs its own
change; the `None`/empty guard still runs first; the module-level constant follows the
stdlib-imports-and-constants-at-module-level convention; no new config option and no new module,
so CLAUDE.md's key-files list is unaffected (`heartbeat.py` is already listed). Long single-line
paragraphs in the two docs edits match those files' existing style. Left `docs/loop-breaker.md`
alone — it documents the producer's escalation behaviour, which this doesn't change.
