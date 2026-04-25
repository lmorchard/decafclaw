# Narrative summaries for scheduled skills — retrospective notes

Session: 2026-04-24, ~16:44 → ~18:00 PT
Scope: Issue #362 — scheduled skills emitting bare `HEARTBEAT_OK` as final assistant message instead of narrative.
PR: https://github.com/lmorchard/decafclaw/pull/368

## Recap

1. Filed #362 during retro of #356 (newsletter) as a follow-up.
2. Started dev session in `.claude/worktrees/scheduled-narrative-summaries/`.
3. Brainstormed: chose option (A) — drop `HEARTBEAT_OK` entirely. Wrote spec + plan.
4. Executed plan via single-implementer dispatch (proportional to small scope).
5. **Round 1 correction** — branch self-review caught `heartbeat.py::is_heartbeat_ok()` consumes the token to gate alert-vs-OK notification priority. Rescoped to branch `build_task_preamble` on `task_type` so heartbeat keeps the token; scheduled drops it.
6. Squashed, pushed, opened PR #368 with Copilot reviewer.
7. **Round 2 correction** — Copilot caught a SECOND consumer: `schedules.py::run_schedule_task` also calls `is_heartbeat_ok()` to drive a tidy log line for quiet scheduled cycles. Rescoped again to option (A-prime): always-narrate AND preserve `HEARTBEAT_OK` as a leading marker for quiet cycles in the scheduled branch. Both consumers now intact.
8. **Round 3 self-review** — caught residual round-1 wording in spec, plan, and a test comment. Cleaned up before final merge.

## Divergences from original plan

The original plan said "drop HEARTBEAT_OK entirely; nothing consumes it." That was factually wrong — TWO consumers existed:

- `heartbeat.py::is_heartbeat_ok()` called from `heartbeat.py::run_section_turn` for alert-vs-OK notification gating.
- The same function called from `schedules.py::run_schedule_task` for log-line tidiness.

I missed both during the Explore phase. The Explore subagent's report mentioned the newsletter's `_is_status_token` filter as a reader but didn't surface either of the `is_heartbeat_ok` callers. I should have explicitly asked "grep for `is_heartbeat_ok` call sites" rather than relying on a more general "what consumes the token?" question.

## Key insights

### Two-round scope correction is a smell

Two consecutive "oh wait, that consumer exists" findings on the same fix is a pattern that says "the discovery phase was incomplete." For internal refactors that REMOVE a signal, the discovery question should always be:

> "Grep every caller of every function that observes this signal. Each caller is a potential constraint."

Not just: "What consumes it?"

The latter relies on the explore agent's understanding of "consumer," which can be implementation-detail-shaped. The former is mechanical and exhaustive.

### The third self-review caught only doc drift, not new bugs

The third self-review (after the Copilot pivot) found NO additional consumers, NO additional code regressions. What it found was prose-shaped: stale comments + spec/plan still talking about the dropped-token framing. That's a reassuring signal that we'd actually converged on a correct design — the remaining issues were all "the docs lag the code."

The lesson: after a pivot, mechanically re-read every doc/spec/plan/comment touched by the original framing. The pivot's commit message captures the change but the design docs frequently lag.

### Reframing > deleting

The eventual fix wasn't "remove the cargo cult." It was "keep the signal but reframe how it's emitted." The bare-token-as-only-message was the actual problem, not the token itself. The leading-marker pattern preserves all signal consumers AND fixes the symptom.

This is a cleaner outcome than my original "rip it out" framing. Sometimes the right answer to "this convention is messy" is "tighten how it's used," not "delete it."

### Copilot review earned its keep

Copilot's review was the second-line catch on consumer #2. The branch self-review caught consumer #1 but didn't grep widely enough to find #2. Worth noting: my Opus-model branch self-review should have been doing the exhaustive `is_heartbeat_ok` callsite grep, and I gave that agent a specific mandate to look for it. It still missed it — probably because I framed the prompt with "the heartbeat consumer is known; check for OTHER consumers" which biased away from re-checking heartbeat-related machinery.

The general lesson: don't tell self-reviewers what's already known to be the issue. Let them re-derive the problem.

## Process observations

- **One implementer dispatch covered all tasks.** Proportional to scope. Spec + per-task spec/code review ceremony would have been overkill for what turned out to be ~5 production lines + ~20 test lines + 4 SKILL.md edits + 2 doc edits.
- **Three review rounds compressed into one PR.** Each round was small enough that re-squashing into the single PR commit was easy. The PR's commit message captures the journey; the dev-session docs in this directory provide the deeper retrospective.
- **Total session length** ~75 minutes including the two pivots. Comparable to context-fork-manager (~60 min) and faster than newsletter (~3 hours).

## Process improvements to carry forward

1. **For removal-pattern fixes:** the discovery question must be "grep every caller of every function that observes this signal," not "what consumes it?" Mechanical exhaustiveness beats conceptual understanding.
2. **After any pivot:** re-read every doc/spec/plan/comment that the original framing touched. The pivot's commit message captures the change but design docs and inline comments routinely lag.
3. **Self-reviewer prompts:** describe the symptom space, not the known answer. Let the reviewer re-derive what's wrong rather than priming them with the answer-so-far.

## Counts

- Plan tasks: 3 (executed in one dispatch)
- Pre-PR commits: 6 → 1 squash
- Post-PR force-push amends: 2 (round-3 cleanup, then this notes commit)
- Subagent dispatches: 5 (1 explore, 1 implementer, 2 self-reviewers, 1 to interpret Copilot)
- Copilot inline comments: 7 (all variations on the same scheduled-consumer point)
- Production lines changed: ~30 (mostly the preamble branch)
- Test lines changed: ~25
- Docs lines changed: ~50 (spec/plan + 2 live docs + this retro)
- Times main advanced during session: 2 (pre-rebase + post-Copilot rebase)
- Conversation turns: ~30 (estimate)
