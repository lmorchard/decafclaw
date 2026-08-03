# Session notes — #139 web UI command autocomplete

**Mode:** `agent-session express`, unattended (board-driver invocation).
**Branch:** `feat/139-command-autocomplete`, worktree `.claude/worktrees/feat-139-command-autocomplete`,
branched from `origin/main` @ `2fab896`.

## Status: PARKED at the freeze, before the freeze commit

Phase 0 and Phase 1 completed. Phase 2 stopped at step 2a (plan + freeze), at the check-reviewer step,
on an open design decision. See **Blocking decision** at the top of `checks.md`. No `plan.md` was
written and no implementation code exists.

This is a sanctioned stop, not a failure: `phases/express.md` lists "plan self-review uncovers a design
decision the spec doesn't cover" as a break-out condition, and the run has no human to ask.

## What got done

- **Phase 0.** Marker present. Readiness gate passed under the *augmented existing issue* variant (the
  body was triage-augmented in place on 2026-07-29, so template sections it never had aren't failures).
  Size: the body's own note downgrades M → S. Tier `auto-ok` read from the body's `## Tier:` heading;
  the issue carries no tier label, which is absent rather than conflicting.
- **Phase 1.** Worktree + venv + npm deps; session dir; `spec.md` captured from the issue; board moved
  Ready → In progress. Baseline green: Python `3717 passed, 2 skipped`; JS `10 files / 87 tests`.
- **Freeze, steps 1–4.** `checks.md` written; three check files authored by a check-author subagent
  that was given the criteria but no implementation plan; every check run and observed to fail for the
  right reason; an independent read-only check-reviewer graded each check and guard; every
  strengthening applied. Step 5 (the freeze commit) deliberately NOT taken.

## The decision that needs a human

**When does the client request the command list?** The spec settles the transport (new WebSocket
message type) but not the trigger. C3 as authored says "on socket `open`", which collides with #704's
guard `sends nothing on the socket when no conversation is selected`
(`lib/conversation-store.test.js:238`), merged as `2fab896` — the most recent commit on `origin/main`.

Three options with real, different costs are laid out in `checks.md`. The short version: option A edits
another issue's regression guard to fit this feature; option B leaves the menu empty for the first
message of a fresh session (verified reachable — `sendMessage` creates a conversation when none
exists); option C adds a round-trip on the first `/` keypress.

The collision is prospective — today C3 fails and #704's guard passes, so they coexist. That is exactly
why it has to be settled *before* the freeze: after it, changing C3 costs an amendment plus an
`auto-ok` → `needs-review` downgrade.

## Worth carrying forward

- **The check-reviewer earned its keep.** It found four gameable holes in C1, three in C2, and one
  escalation that neither file could close — the observation that both criteria could be fully green
  with a feature dead in the browser, because nothing asserted the client ever sends the request or
  that the reply reaches the component. That became C3. Every fix was free because it landed inside the
  review window; after the freeze commit each would have cost a tier downgrade.
- **Two guards claimed coverage they did not have.** G3 said it protected the `!` / `/` transport split
  while its only web test monkeypatched `dispatch_command` away entirely; narrowing the web call site
  to `prefixes=["!"]` would have killed every `/command` in the browser at `30 passed`. Probed by
  applying that exact regression — the strengthened guard goes red — then reverted with a targeted
  edit. G4 could not catch a server→client wire type that never entered the manifest, because
  regeneration produces no diff when the manifest never changed.
- **Recorded baselines go stale between the measurement and the freeze.** G1's and G5's numbers were
  both captured before the check files existed and were unreproducible in the check-bearing tree. Both
  now record the pre-existing baseline, the check-bearing measurement, and the post-implementation
  target as three separate numbers.
- `HTTP_PORT` could not be appended to the worktree `.env` (sandbox denied the write). Not load-bearing
  — this issue's checks are vitest + pytest and need no running server.

## Resuming

Answer the blocking decision, adjust C3's `requests the command list when the socket opens` if the
answer is B or C, re-run the three checks, then take the freeze commit (plus the follow-up commit that
records its sha) and continue from `phases/plan.md` step 6 (vertical slices).
