# Session notes — tier-checking every consumer of `discovered_skills`

Branch: `fix/739-workspace-skill-trust`. Closes #741, #739, #740, #737, #744.
Baseline `origin/main` @ `67abd96` (3675 passed) → **3710 passed, 2 skipped**.

This file is the durable record. The plan's per-phase checkboxes carry the
evidence; this carries the reasoning and the findings.

## What shipped

The #731/#742 wave gated the *schedule* (`task.source`) and *activation*
(`trust_tier` in the `required-skills` loop). Everything else read past those
gates. One predicate now covers the lot:

```python
SKILL_CAPABILITY_TIERS = frozenset({"admin", "bundled", "extra"})
def grants_capability(info) -> bool: return info.trust_tier in SKILL_CAPABILITY_TIERS
```

Applied at six reads, plus a forcing-function test over all 23.

## Six exits, five of them filed

| # | Consumer | Effect | Issue |
|---|---|---|---|
| 1 | `_resolve_skill_dir` | `$SKILL_DIR` repointed → trusted schedule pre-approves an agent-written script | #739 |
| 2 | `_render_required_skill_bodies` | agent-authored instructions in a trusted turn's prompt | #740 |
| 3 | `execute_command` pre-approvals | agent's own command pre-approves its shell | #737 |
| 4 | `execute_command` `requires_skills` | activates a dependency the human never named | #737 |
| 5 | `build_skill_tool_owners` + `collect_all_tool_defs` preload | **execs `tools.py`** with no gate | #744 |
| 6 | `eval/tool_choice/loadout.py` | same exec, `make eval-tools` only | #744 |

**#744 was not filed when this session started.** It was found by auditing every
consumer instead of only the three #741's table lists, and it is the sharpest of
the set: no schedule, no human, no name collision. The agent writes
`workspace/skills/x/tools.py` and calls `refresh_skills` — which
`skill-creator`'s SKILL.md explicitly tells it to do. Confirmed by execution
before it was filed.

Its root cause is the shape #742's notes predicted: **the gate is on the caller,
not the callee.** `activate_skill_internal`'s own docstring records the split —
"Shared by tool_activate_skill (with permission checks) and command execution
(without permission checks)" — so the confirmation lives in the wrapper and any
caller that skips the wrapper skips the gate. `build_skill_tool_owners`'s comment
said the quiet part: importing here is "the same work… just front-loaded." It is
the same work, run *before* the confirmation instead of after.

## Two issue claims this branch rejected

Both are recorded because the issues are wrong on the record and someone will
read them again.

**#741 direction B ("stop workspace skills shadowing trusted ones") does not
close the class.** It is offered there as the option that "closes the class
rather than the instances." It doesn't:

- #739 and #740 resolve by **name**, not by shadowing. If a trusted schedule
  names a skill with no trusted-tier entry, there is no collision to block and
  the agent's workspace skill wins by default. Reachable on the documented path:
  copying a contrib `SCHEDULE.md` to the admin dir and adding the skill dir to
  `extra_skill_paths` are separate steps, and only the first is needed to fire.
  `tests/test_schedule_skill_dir_tier.py::
  test_admin_schedule_skill_dir_skips_workspace_only_skill` pins exactly this
  shape, precisely because B would not have caught it.
- #737's base case never collides — a novel command name shadows nothing.
- #744 involves no collision at all.

Non-shadowing remains worthwhile as defense in depth, but it is the change that
needs a collision policy, and it closes nothing this branch doesn't.

**#737's "reuse `_PREAPPROVAL_TIERS`" would break six shipping skills.** The
issue suggests it to avoid "two tier-trust models that can drift apart." They
are not the same policy:

- `_PREAPPROVAL_TIERS = {"admin","bundled"}` describes a **schedule's
  `task.source`**. Excluding `extra` is free: a contrib `SCHEDULE.md` is
  force-disabled at discovery, so opting in means copying it to the admin dir,
  which makes the source `admin` anyway.
- A **skill's `trust_tier`** has its boundary at `workspace` alone — as
  `discover_skills`, `prompts/__init__.py:101` and `tool_activate_skill:636`
  already decided independently.

Six `extra`-tier skills ship user-invocable `allowed-tools` with
`shell($SKILL_DIR/fetch.sh*)`: `rss-ingest`, `mastodon-ingest`,
`linkding-ingest`, `meta-ingest`, `kindle`, `blog-develop`. A teeth-probe
swapping the guard for `_PREAPPROVAL_TIERS` fails
`test_extra_tier_command_still_preapproves` and logs *"Command 'doit' is extra
tier: its allowed-tools/shell patterns restrict but do not pre-approve"* — which
is what `!rss-ingest` would have printed. The argument is demonstrated, not
asserted. Paired comments at both constants say why they must not be collapsed.

## `trust_tier` now defaults to `"workspace"`

An allowlist only fails closed for an *unrecognized* tier, and the old default
`"bundled"` was recognized and trusted. Latent (no runtime constructor leaves it
unset; `parse_skill_md` has test callers only) but wrong for a fail-closed gate.

**The churn measurement is the interesting part, and the first number was
misleading.** Phase 2 measured **exactly 1** failing test. By the end of the
branch it was **6**, across four files. The reason: each later phase adds a
consumer that rejects workspace tier, and every pre-existing test that built a
`SkillInfo` without a tier fails at the *first* such consumer it touches. So the
blast radius grows as the branch proceeds, and "1" was accurate when measured
and useless as a forecast.

Every one was a test whose subject was something else (XML escaping, inline/fork
dispatch, overlay resolution) with the tier incidental; each now sets it
explicitly with a comment. One carried a stale claim worth correcting:
`test_skill_name_xml_attribute_escaped` said `location` drove the always-loaded
trust check. It never did — `trust_tier` does.

**Operational lesson:** a per-phase test run on the phase's own file would have
missed all six. Only `make test` finds them.

## Two false-green tests caught before they shipped

The #742 branch had four tests that looked like coverage and couldn't fail. Two
of the same shape appeared here and were caught by the discipline of running
every attack test against the unfixed code first.

1. **`test_build_tool_list_omits_unactivated_workspace_tool`** asserted on
   `build_tool_list`'s *active* list — but an unactivated skill's tools are
   *deferred*, not active, so it passed before the fix as well. Replaced with
   `test_collect_all_tool_defs_omits_unactivated_workspace_tool`, which asserts
   at the layer the preload actually feeds.
2. **`test_workspace_required_skill_not_activated`** passed pre-fix because
   `execute_command` resolves `requires_skills` through
   `ctx.config.discovered_skills`, which the `config` fixture leaves empty — the
   dependency was unresolvable for reasons having nothing to do with tier. It
   would have passed against the vulnerable code.

The second was surfaced by its **positive control** failing when it had no
business failing. That is the argument for pairing every attack test with one:
the positive control is what tells you your harness is wired up.

## Verification standard applied

- Every vector reproduced **by execution** before any fix. #739's pre-fix run
  printed the exploit itself: `['.../workspace/skills/feeds/fetch.sh*']`
  pre-approved. #744's tests assert on a planted `tools.py`'s import-time marker
  file, not on activation state — the marker is what proves code didn't run.
- Every guard **teeth-probed** by neutering it and confirming the right test
  fails, then restored by re-editing and re-reading the diff. Never
  `git checkout` — it would discard unrelated uncommitted work in the same file.
- Both forcing-function probes ran: an unregistered consumer fails the test by
  name, and a deleted registry entry fails it by name.
- `make check` and `make test` green at every phase.

## The forcing function, and what it can't do

`tests/test_discovered_skills_consumers.py` walks `src/` with `ast`, finds every
**read** of `discovered_skills` (23 unique consumers / 29 reads), and requires
each enclosing function to appear in a registry with a written reason.

Stated in its own docstring rather than left implied:

- It proves a decision was **recorded**, not that it is **correct**. Same bound
  as `test_schedule_tier_trust.py`, which pins a partition rather than a policy.
- **Reads only.** Writes can't leak, so `child_config.discovered_skills = []` in
  `delegate.py` isn't a listed entry.
- **Named blind spot:** the scan keys on the *name*, so a helper receiving the
  list under another parameter name is invisible —
  `build_skill_tool_owners(skills)` is exactly that. It's reached via a call site
  that *does* read `config.discovered_skills`, and that call site is registered,
  so reasons must name where the decision lands ("delegates to X"). The chain is
  the mitigation and it isn't automatic.

A third guard rejects reasons under 40 chars or matching a vague-label set, so
the registry can't decay into `"safe"`.

## Follow-ups

- **#738** — `_resolve_pre_script_path` resolves workspace-first with no
  existence check. Left out deliberately (Les's call): different root cause, and
  latent today since no bundled or contrib `SCHEDULE.md` declares a
  `pre_script`. `docs/schedules.md`'s frontmatter table still documents
  resolution the code doesn't implement.
- **Non-shadowing (#741 direction B)** — still worth doing as defense in depth.
  Needs a call on collisions: error, warn-and-ignore, or namespace. Note it
  closes nothing this branch didn't.
- **Push the tier rule into the callee.** #744 suggests moving it into
  `activate_skill_internal` / `_load_native_tools`, and #742's notes flagged the
  caller-side shape as the recurring defect. It **cannot be unconditional**:
  `commands.py` legitimately activates a human-named workspace command, so the
  callee needs a "a human approved this specific skill" parameter. That's a
  design change, not a gate — its own PR.
- **#746 — `restore_skills` re-executes a rewritten `tools.py`.** Also recorded in
  the consumer registry. `ctx.skills.activated` persists across turns and server
  restarts, so a workspace skill approved once, then rewritten by the agent, gets
  its *new* code imported on restore with no fresh confirmation. **Confirmed by
  execution** before filing: import v1, rewrite the file, next turn executes v2.
  A standing `always` grant makes it worse but isn't required — the name being in
  `activated` is enough. Note the "tools already loaded, skip" guard at
  `skill_tools.py:551` sits *after* the `_load_native_tools` call at 549, so even
  the skip path has already run the module. Out of scope for this branch: scoping
  an approval to *code* rather than to a *name* is a design change, not a gate.
- **`_skill_def_cache` is keyed by `id(config)`.** Pre-existing, unrelated to
  this branch, noted only because the phase-3 tests have to clear it.
- **#747 — `commands.py` resolves `$SKILL_DIR` two ways.** Found during the
  branch self-review. The pre-approval patterns expand `$SKILL_DIR` to
  `str(skill.location)` while `substitute_body` a few lines later uses
  `str(skill.location.resolve())`. So the agent can be told a resolved path
  while the pre-approved pattern holds an unresolved one — the same desync class
  `_resolve_skill_dir`'s docstring exists to prevent, and the reason this branch
  made #739's tier check unconditional. Latent in practice: `discover_skills`
  builds locations from absolute scan roots, so the two agree unless a symlink is
  involved. Deliberately **not** fixed here — it is outside this spec, and
  changing a pre-approval matching path with no test for it is the drive-by the
  plan forbids. Wants its own PR with a test that pins the two expansions equal.

## Live verification against the real config

Run after the PR was opened, with no bot instance running.

**`make eval-tools`, twice:** 26/32 (6 failures), then 25/32 (7 failures), with
*different* failure sets — consistent with the known run-to-run instability. All
failures are vault / notes / tabstack / canvas tool-choice cases; none involve
skills or tiers.

**The pass count is not the evidence.** A read-only audit of skill discovery and
tool-owner indexing against the real `data/decafclaw/` is **byte-identical**
between `origin/main` and this branch — 130 skills, 47 tool owners. The eval
therefore sees the same tool definitions either way and this change cannot move
its results, which is a far stronger claim than comparing noisy counts. The
reason it's a no-op: the only two workspace-tier skills present (`hello`,
`weather`) have no `tools.py`, so all three `_load_native_tools` guards never
fire on this config.

Worth keeping as a technique: **when a change is provably a no-op on the data an
unstable suite runs against, prove the no-op instead of re-running the suite.**
`make eval-history` tracks `make eval`, not `eval-tools`, so it had nothing.

**Startup path clean:** `load_system_prompt` → `discover_skills` →
`build_skill_tool_owners` against the real config produced no warnings, no
errors, no tracebacks.

**Commands exercised at all three tiers on real data**, via `execute_command`
directly (no LLM needed):

| Command | Tier | Result |
|---|---|---|
| `!hello` | workspace | runs (`mode=inline`, body substituted), `preapproved=[]` |
| `!linkding-ingest` | admin | all 9 tools + the real `.../linkding-ingest/fetch.sh*` pattern |
| `!health` | bundled | `health_status` pre-approved |

That third row is the one that mattered: it confirms on live data the thing the
`_PREAPPROVAL_TIERS` argument was only asserting — a real configured ingest
command still gets its shell pattern. `!hello` logs nothing, because it declares
no `allowed-tools`, so the new `elif` branch doesn't fire.

Incidentally this also confirms #747 is latent here: the pre-approved pattern
came out absolute, so the two `$SKILL_DIR` spellings agree on this config.

### One behavior change on the real config: the `hello` fixture

`workspace/schedules/hello.md` is `source=workspace`, **`enabled: false`**, and
declares `required-skills: [weather]`. `workspace/skills/weather/` is prose-only
(no `tools.py`).

- **Before:** `weather`'s body was injected into that schedule's prompt. Its
  *activation* was already blocked by #742.
- **After:** the body is not injected, and two warnings fire when it runs.
- **Impact today: none** — the schedule is disabled. If enabled, its prompt loses
  the weather instructions and is just "Say hello and tell a cat joke."

Stated plainly because it is the one case where this branch's Phase 5 gate buys
nothing: #740's threat needs a **trusted** schedule, a workspace schedule gets no
pre-approvals anyway, and since `weather` has no `tools.py` there is no
"instructions for tools that weren't loaded" incoherence either — the body *is*
the whole skill.

**Decision (Les's call): keep the gate unconditional.** It fails closed, it
matches the `prompts/__init__.py:101` precedent, and narrowing it would mean
threading the schedule's trust into `_render_required_skill_bodies` — the same
plumbing deliberately kept out of `_resolve_skill_dir`. If the fixture is ever
enabled and the body is wanted, moving `weather` to `data/{agent_id}/skills/`
restores it, and that move is the deliberate human act the boundary asks for.

## Not done

- No new eval cases. Nothing here changes LLM-visible routing or tool
  descriptions. The one LLM-visible edge — an unactivated workspace skill's tools
  no longer appearing in the deferred catalog — is pinned by a unit test at the
  `collect_all_tool_defs` layer.
- Not exercised through a running server (Mattermost websocket / web UI browser
  session). The diff touches no JS and no transport code, and the command paths
  were exercised directly against the real config instead.
