# Session notes — gating schedule preapproval on source tier (#731)

Branch: `sec-workspace-preapproval`. Closes #731.

This file is the durable record for the session. The SDD execution ledger
(`.superpowers/sdd/plan/progress.md`) is gitignored, so everything below was
carried forward from it deliberately — nothing else survives the merge.

## What shipped

`workspace/schedules/*.md` is agent-writable by design. `run_schedule_task`
was installing a task's frontmatter as *pre-approvals* on the turn's context
with no check on `task.source`, so the agent could grant itself un-confirmed
capability by writing a file it was already allowed to write. Scheduled turns
are unattended, so `check_shell_approval` denies rather than prompting — the
frontmatter was the entire decision, with no human on either path.

Frontmatter now does two separable things:

- **Restrict** what the task can see. Applies at every tier. Unchanged.
- **Grant** pre-approval past confirmation. Admin and bundled tier only.

The gate is one flag, `trusted = task.source in _PREAPPROVAL_TIERS`, where
`_PREAPPROVAL_TIERS = {"admin", "bundled"}`.

**It is an allowlist, and that is the point.** A denylist keyed off the
untrusted tiers would fail *open* for any tier nobody thought to enumerate.
`SCHEDULE_TIERS` declares all four tiers in one place and
`tests/test_schedule_tier_trust.py` pins the partition, so a fifth tier can't
be added without its trust being decided. `_UNTRUSTED_TIERS` exists only for
that completeness check and is deliberately not read by any runtime gate —
if production code consulted it, the guard would go false-negative.

**`extra` (contrib) is excluded for a different reason than `workspace`.**
Worth stating plainly, because the plan got this wrong once (see Process
signal below) and the wrong version is the intuitive one. `workspace` is
excluded because the agent writes those files. `extra` is excluded as defense
in depth: contrib schedules come from `extra_skill_paths` — third-party skill
directories, not anything the agent writes. A contrib `SCHEDULE.md` is
force-disabled at discovery, so it can't fire until a human opts in, and
opting in means copying the file to `data/{agent_id}/schedules/`, which makes
its source `admin` and restores pre-approval as a deliberate act.

Also shipped: the web UI's schedule metadata panel shows a note at untrusted
tier saying the permission fields don't pre-approve. Its condition mirrors the
allowlist (`if ['admin','bundled'].includes(source_tier) return nothing`) so it
fails closed on an unknown tier, the same way the runtime gate does.

## Five vectors, found across three rounds

The progression is the most useful thing in this file for whoever audits this
area next. **The spec was not the ceiling.** Each round of review against
already-fixed code found another way in.

| # | Vector | Found |
|---|--------|-------|
| 1 | `preapproved` (bare tool names from `allowed-tools`) | spec |
| 2 | `preapproved_shell_patterns` (`shell(...)` entries) | spec |
| 3 | `preapproved_email_recipients` | spec |
| 4 | `pre_script` — arbitrary Python as the bot process | Task 1 review |
| 5 | `required-skills` — activation execs the skill's `tools.py` | final whole-branch review |

Vectors 4 and 5 are both *worse* than the three in the spec: 1–3 at least route
through an approval function that denies on an unattended turn. 4 and 5 have no
approval path at all — they are straight code execution on the 60-second poll
tick.

Vector 5 is also the only one reachable **without an agent-written schedule**.
`workspace/skills/` is agent-writable *and* the highest-precedence skill scan
entry, so an agent-authored `workspace/skills/dream/` shadows the bundled
`dream` skill — and the bundled `dream`, `garden` and `newsletter`
`SCHEDULE.md` files all declare `required-skills` today. A *trusted* schedule
naming a skill the agent creates. Not latent.

Restricting `allowed-tools` is not a mitigation for vector 5: `tools.py` runs
at import, before any tool is called, and the agent controls that field anyway.

The fix for 5 has precedent in the codebase. `activate_skill` already treats
workspace tier as untrusted in three places — activation confirmation with an
unattended denial (#649), `auto-approve` ignored, `always-loaded` ignored
(`activate_always_loaded` skips the tier entirely). The schedule path routed
around all three. It now skips `trust_tier == "workspace"` and logs a warning
naming the skill and the task.

Both attack tests for vector 5 (`tests/test_schedule_required_skills_tier.py`)
assert on the skill's **import-time side effect**, not on `ctx.skills.activated`
— the marker file is what proves the code did not run. The admin-tier positive
control passes before and after.

## Architectural note — the gate is on the caller, not the callee

`trusted` is computed once in `run_schedule_task` and applied at several call
sites. The dangerous functions themselves don't know about tiers:
`activate_skill_internal` will happily import any skill it is handed, and
`_run_pre_script` (until the last commit of this session) would run any script
it was handed. **The next caller of either gets no protection by default.**

That is not hypothetical — it already bit us once inside this session. The
`pre_script` gate as first shipped sat at the call site *ahead of*
`_run_pre_script`, which is where `config.pre_script.enabled` is checked. So
with the feature globally disabled, a workspace schedule declaring a
`pre_script` still got `[pre_script error: ignored — not permitted at this
tier]` in its prompt plus a warning on every fire, stating a reason that wasn't
true — the feature was simply off. Fixed by moving the gate *inside*
`_run_pre_script` rather than reordering at the call site, which both fixes the
ordering and puts the check where a second caller can't route around it.

`activate_skill_internal` still has the caller-side shape. Pushing the tier
rule down into it is the obvious follow-up, and was left out of this branch to
keep the security fix small.

## Known gaps and follow-ups

- **#737 — `commands.py`, same bug class.** `commands.py:384-388` installs a
  skill's `allowed_tools` / `shell_patterns` as pre-approvals with no tier
  check, and `workspace/skills/` is agent-writable and highest-precedence.
  Weaker than #731 because it needs a human to type `!name`. Filed P1/S/Ready;
  Les chose to keep it out of this PR.
- **#738 — `_resolve_pre_script_path` resolves workspace-first with no
  existence check.** It returns on the first root the path is *contained by*,
  so `data/{agent_id}/` is unreachable for a relative path. A **trusted**
  schedule's `pre_script` therefore resolves to agent-writable code by
  construction: the agent plants `workspace/scripts/fetch.py` and an admin
  schedule executes it. Latent today (no bundled or contrib `SCHEDULE.md`
  declares a `pre_script`). `docs/schedules.md`'s frontmatter table documents
  the resolution order the code *doesn't* implement; this branch left the
  wording alone and added a pointer to #738 rather than describing the broken
  behaviour as if it were intended.
- **"Reset to default" can land on an agent-authored file.** On a bundled
  schedule, `delete_overlay` can return `source='workspace'` with the agent's
  body presented as the default. Pre-existing, out of scope, and it *chains
  with vector 5*: the "default" a user resets to may be agent-written, and that
  file's `required-skills` is the thing the tier gate now has to hold against.
- **`_render_required_skill_bodies` is not tier-gated.** A workspace skill
  named in `required-skills` no longer *activates*, but its `SKILL.md` body is
  still rendered into the prompt's `<loaded_skills>` block. That's prompt
  content, not a capability grant — roughly the same trust level as the skill
  catalog the agent can already write into — so it was left alone. It is an
  inconsistency someone will trip over.
- **`test_trust_sets_are_disjoint` can't fail** as written. Flagged in the
  final review, not fixed here.
- **No test pins `_PREAPPROVAL_TIERS`'s exact value.** Deliberate. The JS in
  `schedule-metadata.js` duplicates `['admin','bundled']` for display, and a
  test asserting the Python constant equals `{"admin","bundled"}` would be an
  enumeration guarding itself. A bidirectional back-reference comment between
  the two sites is the mitigation instead.

## Behaviour change worth knowing about

`check_email_approval` has **no `is_unattended` short-circuit**, unlike
`check_shell_approval`. So a workspace schedule that previously pre-approved
its recipients now blocks on a persisted confirmation rather than being denied
outright. Arguably the correct outcome — a human can come back and answer it,
and nothing is sent meanwhile — but it is a different shape of failure from the
shell path, and a scheduled task can now sit pending indefinitely where before
it would have sent.

## Process signal

Three things went wrong in a way worth recording, because none of them were
caught by tests.

1. **Two plan-authored errors reached implementation.** The plan's prose said
   `extra` is excluded "for the same reason" as workspace (agent-writability —
   false), and the UI note text named only `admin` as trusted (omitting
   `bundled`, misstating the boundary). Both were *my* text, in the brief, and
   in both cases the implementer followed the brief verbatim over a dispatch
   message that had corrected it. **A brief is read as authoritative; a
   correction sent alongside it is not.** Fix the brief, don't append to it.
2. **A task report asserted a verification that hadn't happened.** Task 3's
   report claimed it had used the corrected "different reason" wording; grep
   showed "for the same reason" had shipped. A report asserting a check that
   did not run is its own defect, independent of the underlying bug.
3. **Controller staged broadly in a worktree with a live subagent.**
   `git add -A && git commit` for a docs amendment swept the implementer's
   in-progress `pre_script` gate (schedules.py +11, tests +42) into a commit
   labelled `docs:`. The code was correct and the repo squash-merges, so no
   history damage — but the implementer had to independently verify code it
   never wrote appearing under its feet. Never stage broadly in a shared
   worktree.

## Verification

`make check`, `make test` (3675 passed, 2 skipped), `make test-js` (82 passed)
all green at the final commit. Every test added in this session was confirmed
to fail against the code immediately before its fix.
