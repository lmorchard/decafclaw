# Spec — every consumer of `discovered_skills` makes a trust-tier decision

Closes #741 (structural), #739, #740, #737, #744.
Explicitly **not** in scope: #738 (different root cause — path resolution order, not a `discovered_skills` read).

Branch: `fix/739-workspace-skill-trust`. Baseline: `origin/main` at `67abd96`, 3675 passed / 2 skipped.

## The problem

`workspace/skills/` is agent-writable **and** the highest-precedence skill scan entry
(`skills/__init__.py:363`). Every consumer of `config.discovered_skills` reads from it.
The #731/#742 fix wave placed gates on the *schedule* (`task.source`) and on
*activation* (`trust_tier` in the `required-skills` loop). Everything else reads past
them.

Five confirmed exits, four of them filed, one found while scoping this branch:

| # | Consumer | Field / effect | Issue |
|---|---|---|---|
| 1 | `_resolve_skill_dir` (`schedules.py:579`) | `info.location` → a trusted schedule pre-approves an agent-written script | #739 |
| 2 | `_render_required_skill_bodies` (`schedules.py:614`) | `info.body` → agent-authored instructions in a trusted turn's prompt | #740 |
| 3 | `execute_command` (`commands.py:384`) | `allowed_tools` / `shell_patterns` → pre-approvals via `!name` | #737 |
| 4 | `execute_command` (`commands.py:401`) | `requires_skills` → activation of a dependency the human never named | #737 |
| 5 | `build_skill_tool_owners` (`skills/__init__.py:555`) **and** the skill-def preload in `build_tool_list` (`tool_definitions.py:105`) | both call `_load_native_tools`, which **execs `tools.py` module-level code** with no tier check | #744 |
| 6 | `eval/tool_choice/loadout.py:44` | third untiered `_load_native_tools` call | #744 (found during spec gap review) |

Site 6 is not agent-reachable — it runs only under `make eval-tools`, on a developer's
machine. It is in scope anyway because the fix is one line, because a dev's real config
points at a real workspace that may hold agent-authored skills, and because leaving one
untiered caller behind would make the forcing-function test's registry dishonest.

## Two claims that the issues get wrong

Both matter because they change what gets built.

### #741's direction B does not close the class

#741 offers "stop letting workspace skills shadow trusted ones" as the option that
"closes the class rather than the instances." It doesn't:

- **#739 and #740 resolve by *name*, not by shadowing.** If a trusted schedule names a
  skill with no trusted-tier entry, there is no collision to block and the agent's
  workspace skill wins by default. That state is reachable on the documented path:
  copying a contrib `SCHEDULE.md` into `data/{agent_id}/schedules/` and adding the
  skill dir to `extra_skill_paths` are two separate steps, and only the first is
  required for the schedule to fire.
- **#737's base case never collides.** A novel command name has nothing to shadow.
- **#744 is untouched by it.** No collision is involved at all.

So a tier check at the consumers (direction A) is *necessary*. Non-shadowing is
defence-in-depth on top, and it is the change that needs a collision-policy decision.
It is out of scope here; recorded as a follow-up.

### #737's "reuse `_PREAPPROVAL_TIERS`" would break six shipping skills

#737 suggests reusing the schedules constant "rather than inventing a second spelling
of the same policy." They are **not** the same policy, because they describe different
objects with different threat models:

- `_PREAPPROVAL_TIERS = {"admin", "bundled"}` is about a **schedule's `task.source`**.
  It excludes `extra` as defence in depth — contrib `SCHEDULE.md` is force-disabled at
  discovery, so opting in means copying to the admin dir, which makes the source
  `admin` anyway. Excluding `extra` there costs nothing.
- A **skill's `trust_tier`** has its trust boundary at `workspace` alone. The codebase
  has already decided this three times: `discover_skills` strips `auto-approve` /
  `always-loaded` for `workspace` only; `prompts/__init__.py:101` skips `workspace`
  only; `tool_activate_skill:636` is literally `trust_tier != "workspace"`.

Applying the schedules set to skills would strip pre-approvals from every
`extra`-tier user-invocable skill. Verified — six ship today with
`allowed-tools` that include `shell($SKILL_DIR/fetch.sh*)`: `rss-ingest`,
`mastodon-ingest`, `linkding-ingest`, `meta-ingest`, `kindle`, `blog-develop`.
`!rss-ingest` would stop working.

**Decision:** a separate, explicitly documented skill-tier predicate, with a
bidirectional comment at both sites stating why the partitions differ. The #742
notes record that "extra is excluded for a *different* reason than workspace" was
already gotten wrong once by a plan brief; conflating the two constants is the same
mistake with teeth.

## What ships

### 1. One shared predicate, in `skills/`

```python
SKILL_TIERS = ("workspace", "admin", "bundled", "extra")
SKILL_CAPABILITY_TIERS = frozenset({"admin", "bundled", "extra"})

def grants_capability(info) -> bool: ...
```

An **allowlist**, fails closed on an unrecognized tier — same shape and same reasoning
as `_PREAPPROVAL_TIERS` (a denylist would fail *open* for a tier nobody enumerated).
A partition test pins `SKILL_TIERS` against the tiers `skill_scan_entries` actually
yields, so a fifth scan entry can't be added without its trust being decided.

**`SkillInfo.trust_tier` defaults to `"bundled"` — that default is fail-open.** An
allowlist only fails closed for an *unrecognized* tier, and `"bundled"` is recognized
and trusted. `discover_skills` always assigns the real tier, and the one constructor
that leaves it at the default (`parse_skill_md`) has no runtime callers today — tests
only. So this is latent, not live. It is still the wrong default for a branch whose
whole thesis is failing closed, so: **change it to `"workspace"`**.

The risk is test churn, since `parse_skill_md` and direct `SkillInfo(...)` construction
appear across `tests/test_skills.py`, `test_scoped_shell.py`, `test_model.py`, and
`contrib/skills/kindle/test_tools.py`. Most of those assert on parsed fields, not on
tier-sensitive behavior, so churn should be small. Plan: make the change, measure the
actual failure count, and fix the tests (per CLAUDE.md, tests get rewritten to the new
path rather than the code accommodating them). If it turns out to be broad enough to
swamp the security diff, revert the default and record it as a follow-up with the
measured number — but decide that from the number, not from a guess.

### 2. Per-consumer changes

**`_resolve_skill_dir` (#739).** Skip non-capability-tier candidates when resolving,
falling through to the existing `task.path.parent` fallback; log a warning naming the
skill and the task.

The check is **unconditional** — not conditioned on the task's own tier. `skill_dir`
feeds both the shell pattern *and* `substitute_body`, and the docstring's whole point
is that those two stay in sync. Gating only the pre-approval path would desync them:
the body would point the agent at the workspace dir while the pattern pointed
elsewhere. One rule keeps the invariant and keeps the trust decision out of the
helper's signature.

The contrib-overlay case the docstring exists to protect is unaffected: when the named
skill is at a capability tier, resolution is byte-identical to today.

**`_render_required_skill_bodies` (#740).** Skip non-capability-tier skills; log the
omission so it isn't silent. Mirrors `prompts/__init__.py:101`.

**`execute_command` (#737).** Two separate changes:
- Pre-approvals (`preapproved`, `preapproved_shell_patterns`) only at a capability
  tier. Restriction still applies at every tier — same "restrict always, grant only at
  a trusted tier" split the schedules fix established. A workspace-tier command still
  runs; it just doesn't pre-approve.
- `requires_skills` activation only at a capability tier. Typing `!name` approves *that
  skill*, not a dependency list the agent controls. The command's **own** activation
  stays ungated — the human named it, and `activate_skill_internal` is documented as
  the without-permission-checks path for exactly this caller.

**All three untiered `_load_native_tools` callers (#744).** `build_skill_tool_owners`,
the `build_tool_list` skill-def preload, and `eval/tool_choice/loadout.py`. Skip
non-capability-tier skills before calling it. Accepted consequences, both verified:
- A workspace skill's tool names stop appearing in the **deferred** catalog until
  activation. Confirmed by probe that they are deferred, not active — nothing leaves
  the LLM's live tool list.
- The "unknown tool name → owning skill" hint won't fire for an unactivated workspace
  skill. `tools/__init__.py:346` already has a workspace-specific branch telling the
  agent to call `activate_skill`, which is the correct next step regardless.

### 3. A forcing-function test

#741 asks for "a test that enumerates the consumers of `discovered_skills` and asserts
each has made a tier decision." Shape: walk `src/` with `ast`, find every read of
`discovered_skills`, resolve the enclosing function, and require each one to appear in
an in-test registry mapping function → recorded decision + reason. A new consumer
fails the test until someone writes the decision down.

**Stated limitation, in the test's own docstring:** it verifies a decision was
*recorded*, not that the decision is *correct* — the same bound as
`test_schedule_tier_trust.py`, which pins a partition rather than a policy.

### 4. Regression tests

Every guard gets an attack test that **fails against the code immediately before its
fix**, plus a capability-tier positive control so the gate can't over-correct. The
#744 tests assert on the planted `tools.py`'s **import-time side effect** (a marker
file), not on activation state — the marker is what proves code didn't run. This
follows `tests/test_schedule_required_skills_tier.py`.

Each guard is teeth-probed by neutering it and confirming the right test fails, per the
standard the #742 branch established after reviewers found four tests there that
looked like coverage and couldn't fail.

### 5. Docs

- `docs/skills.md` — the skill trust-tier model and what `workspace` tier cannot do.
- `docs/schedules.md` — `$SKILL_DIR` resolution now refuses workspace tier.
- `CLAUDE.md` — one convention line: reads of `discovered_skills` that grant capability
  must consult the shared predicate.

## Out of scope, recorded as follow-ups

- **#738** — `pre_script` resolution order. Different root cause; Les's call to keep it separate.
- **Non-shadowing (#741 direction B)** — needs a collision policy (error / warn-and-ignore / namespace). Closes nothing this branch doesn't.
- **Pushing the tier rule into `activate_skill_internal` / `_load_native_tools`.** The
  #742 notes call the caller-side shape out as the recurring defect, and #744 suggests
  the push-down. It can't be unconditional: `commands.py` legitimately activates a
  human-named workspace command, so the callee needs a "a human approved this specific
  skill" parameter. That is a design change, not a gate, and it belongs in its own PR.

## Verification

- `make check`, `make test`, `make test-js` green.
- Every new test confirmed red before its fix and green after.
- Every guard teeth-probed by neutering it.
- No eval cases: none of this changes LLM-visible routing or tool descriptions. The
  workspace-skill deferred-catalog change is the one LLM-visible edge, and it is
  covered by unit tests at the tool-list layer.
