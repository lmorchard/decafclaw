# Implementation Plan — every consumer of `discovered_skills` makes a tier decision

**Goal:** Close every path by which an agent-authored `workspace/skills/` entry grants
capability — pre-approved shell patterns, in-force prompt instructions, or executed
`tools.py` — and leave behind a forcing function so the next consumer of
`discovered_skills` can't skip the decision.

**Approach:** One shared allowlist predicate in `skills/` (`grants_capability`), applied
at each of the six untiered reads. The predicate is deliberately a *different* partition
from `schedules._PREAPPROVAL_TIERS` — see spec. Each guard lands with an attack test
that fails before the fix, a capability-tier positive control, and a teeth-probe.

**Tech stack:** Python 3.13, pytest (+xdist), pyright.

Closes #741, #739, #740, #737, #744. Out of scope: #738.

Baseline: `origin/main` @ `67abd96`, **3675 passed / 2 skipped**.

---

## Phase 1: Shared tier predicate + partition test

Adds the single source of truth every later phase consults. No behavior change on its
own — the predicate has no callers yet.

**Files:**
- Modify: `src/decafclaw/skills/__init__.py` — add `SKILL_TIERS`,
  `SKILL_CAPABILITY_TIERS`, `grants_capability()` immediately above
  `skill_scan_entries` (the tier source of truth they must agree with).
- Modify: `src/decafclaw/schedules.py` — add the paired back-reference comment on
  `_PREAPPROVAL_TIERS` saying why it is a different partition.
- Test: `tests/test_skill_tier_trust.py` — new.

**Key changes:**

```python
# Skill trust tiers, matching what `skill_scan_entries` yields.
SKILL_TIERS = ("workspace", "admin", "bundled", "extra")

# Tiers whose skills may GRANT capability: pre-approve tools, anchor a
# `$SKILL_DIR` that a pre-approval expands, have their body injected as
# instructions in force, or have their `tools.py` imported (which execs
# module-level code).
#
# An allowlist on purpose. A denylist keyed off the untrusted tiers would fail
# OPEN for any tier nobody thought to enumerate.
#
# NOT the same partition as `schedules._PREAPPROVAL_TIERS`, and the difference
# is load-bearing. That set is {"admin", "bundled"} and describes a *schedule's*
# `task.source`; it excludes "extra" as cheap defense in depth, because a
# contrib SCHEDULE.md is force-disabled at discovery and opting in means copying
# it to the admin dir (which makes its source "admin" anyway). Excluding "extra"
# there costs nothing. Excluding it HERE would cost real function: "extra" is
# where contrib skills live, and six ship user-invocable `allowed-tools`
# containing `shell($SKILL_DIR/fetch.sh*)`. Keep both comments in sync.
SKILL_CAPABILITY_TIERS = frozenset({"admin", "bundled", "extra"})


def grants_capability(info: SkillInfo) -> bool:
    """True when `info`'s placement makes it trusted to grant capability.

    Placement IS the trust signal: bundled ships with the project, admin was
    placed by hand, extra was configured in. `workspace/skills/` is
    agent-writable, so a workspace skill may be agent-authored.

    Fails closed — an unrecognized tier is simply not in the allowlist.
    """
    return info.trust_tier in SKILL_CAPABILITY_TIERS
```

Paired comment to add at `schedules.py` `_PREAPPROVAL_TIERS`:

```python
# Distinct from `skills.SKILL_CAPABILITY_TIERS`, which includes "extra".
# This set is about a schedule's `task.source`; that one is about a skill's
# `trust_tier`. See the comment there for why the partitions differ.
```

Tests:

```python
def test_skill_tiers_covers_every_scan_entry_tier(config, tmp_path):
    """A new scan entry can't be added without declaring its tier."""
    extra = tmp_path / "extra-skills"
    extra.mkdir()
    config.extra_skill_paths = [str(extra)]
    tiers = {tier for tier, _ in skill_scan_entries(config)}
    assert tiers == set(SKILL_TIERS)


def test_capability_tiers_partition_is_decided():
    """Pins which tiers are untrusted, so a NEW tier forces a decision.

    Adding a tier to SKILL_TIERS makes it implicitly untrusted (fail-closed,
    which is safe) but silent. This assertion makes it loud.
    """
    assert SKILL_CAPABILITY_TIERS <= set(SKILL_TIERS)
    assert set(SKILL_TIERS) - SKILL_CAPABILITY_TIERS == {"workspace"}


@pytest.mark.parametrize("tier,expected", [
    ("workspace", False), ("admin", True), ("bundled", True), ("extra", True),
    ("", False), ("Workspace", False), ("plugin", False),
])
def test_grants_capability(tier, expected):
    """Unrecognized tiers fail closed."""
    assert grants_capability(_info(tier)) is expected
```

`_info(tier)` is a local helper building a minimal `SkillInfo(name="x",
description="d", location=Path("/tmp/x"), trust_tier=tier)`; exact required fields to
be read off the dataclass at implementation time.

**Verification — automated:**
- [x] `.venv/bin/python -m pytest tests/test_skill_tier_trust.py -q` passes — **9 passed**
- [x] `make check` passes — **ruff: all checks passed; pyright: 0 errors, 0 warnings**
- [!] `make test` passes — still 3675 passed / 2 skipped (no behavior change yet) —
      **got 3684 passed, 2 skipped.** The assertion was mis-written, not the work: the
      phase adds 9 tests of its own, so the total necessarily moves by +9. Production
      behavior is unchanged (the predicate has no callers yet). 3675 + 9 = 3684.
- [x] Teeth probe: set `SKILL_CAPABILITY_TIERS` to include `"workspace"` →
      `test_capability_tiers_partition_is_decided` fails. Revert by re-editing the
      constant (never `git checkout` the file). — **2 failed, 7 passed under the probe**
      (`test_capability_tiers_partition_is_decided` and
      `test_grants_capability[workspace-False]`); reverted by re-editing, back to 9 passed.

**Verification — manual:**
- [x] The two paired comments read as a matched pair and each names the other. —
      **verified in the final diff.** `SKILL_CAPABILITY_TIERS` says "Keep this comment
      and the one on `_PREAPPROVAL_TIERS` in sync"; `_PREAPPROVAL_TIERS` says "Distinct
      from `skills.SKILL_CAPABILITY_TIERS`, which DOES include `extra` … see the comment
      on `SKILL_CAPABILITY_TIERS` for the full reasoning." Each names the other by name.

---

## Phase 2: Fail closed on an unassigned tier

`SkillInfo.trust_tier` defaults to `"bundled"` — trusted. Latent (no runtime
constructor leaves it unset) but wrong for this branch's thesis.

**Files:**
- Modify: `src/decafclaw/skills/__init__.py:70` — default `"bundled"` → `"workspace"`,
  and update the surrounding tier comment to say the default is deliberately the
  untrusted tier.
- Modify: whichever tests break — measured, not guessed.
- Test: `tests/test_skill_tier_trust.py` — add the default assertion.

**Key changes:**

```python
    # Defaults to the UNTRUSTED tier on purpose: `discover_skills` always
    # assigns the real tier from placement, so anything reaching a consumer
    # without one was built off the discovery path and has no placement to
    # vouch for it. Failing closed here means a future constructor that forgets
    # to set the tier loses capability rather than silently gaining it.
    trust_tier: str = "workspace"
```

```python
def test_unassigned_trust_tier_is_untrusted():
    info = parse_skill_md(...)  # the one constructor that leaves it default
    assert info.trust_tier == "workspace"
    assert grants_capability(info) is False
```

**Procedure (the measurement is the point):**
1. Make the one-line change.
2. `make test`, record the exact failure count and the list of failing tests.
3. Fix each failing test to set the tier explicitly (per CLAUDE.md: rewrite tests to
   the new path).
4. If the count is large enough to swamp the security diff, revert the default and
   record the measured number as a follow-up in `notes.md`. Decide from the number.

**Verification — automated:**
- [x] Failure count from step 2 recorded in `notes.md` (a number, not "a few") —
      **exactly 1 failure**: `tests/test_prompts.py::TestSkillSections::
      test_skill_name_xml_attribute_escaped`. It builds a `SkillInfo` directly with
      `always_loaded=True` and relied on the default tier being trusted, so
      `prompts/__init__.py:101` now skips its body. Its subject is XML escaping, not
      tier policy, so it gets an explicit `trust_tier="bundled"`. Its stale comment
      ("any bundled path for the trust check") was corrected too — `location` never
      drove that check, `trust_tier` does.
- [x] `make test` green after fixes — **3685 passed, 2 skipped**
- [x] `make check` passes — **ruff clean; pyright 0 errors, 0 warnings**

**Verification — manual:**
- [x] Decision recorded: kept, or reverted with the measured number and why. —
      **kept.** 1 failing test is nowhere near swamping the security diff.

---

## Phase 3: `tools.py` no longer execs at workspace tier (#744)

The sharpest vector: no schedule, no human, no name collision. Three untiered
`_load_native_tools` callers. Confirmed by execution during spec research.

**Files:**
- Modify: `src/decafclaw/skills/__init__.py` — `build_skill_tool_owners`: skip
  non-capability tiers; fix the now-wrong "just front-loaded" docstring line.
- Modify: `src/decafclaw/tool_definitions.py` — skill-def preload (~line 105).
- Modify: `src/decafclaw/eval/tool_choice/loadout.py` (~line 44).
- Test: `tests/test_skill_native_tools_tier.py` — new.

**Key changes:**

`build_skill_tool_owners`:

```python
    for skill in skills:
        if not skill.has_native_tools:
            continue
        if not grants_capability(skill):
            # Importing tools.py execs its module-level code. The activation
            # path gates that behind a confirmation for workspace tier (#649)
            # and refuses it outright on an unattended turn; indexing here
            # would front-run both (#744).
            log.debug("Not indexing tools for %s-tier skill %r",
                      skill.trust_tier, skill.name)
            continue
```

Docstring line to correct — it currently reads "same work as `_load_native_tools`
(which the activation path runs eventually anyway), just front-loaded." The activation
path runs it *after* a confirmation this path skipped; say so.

`tool_definitions.py`:

```python
        for skill_info in ctx.config.discovered_skills:
            # grants_capability: importing tools.py execs module-level code, and
            # workspace/skills/ is agent-writable (#744).
            if skill_info.has_native_tools and grants_capability(skill_info):
```

Import `grants_capability` at module level; if that introduces a cycle, use a
function-level import with the cycle named in a comment (the only sanctioned reason
per CLAUDE.md).

`eval/tool_choice/loadout.py`:

```python
    for skill in discover_skills(config):
        if not skill.has_native_tools or not grants_capability(skill):
            continue
```

Tests — all three assert on the planted `tools.py`'s **import-time side effect** (a
marker file), because the marker is what proves code did not run:

```python
def _write_skill(skill_dir, name, marker):
    """A skill whose tools.py writes `marker` at import."""
    # SKILL.md + tools.py with `open(marker, 'w').close()` at module level,
    # plus a valid TOOLS / TOOL_DEFINITIONS pair so the contract check passes.

def test_catalog_build_does_not_exec_workspace_tools_py(config): ...
def test_refresh_skills_does_not_exec_workspace_tools_py(config): ...
def test_build_tool_list_does_not_exec_workspace_tools_py(config): ...
def test_admin_tier_tools_py_still_loads(config): ...          # positive control
def test_extra_tier_tools_py_still_loads(config): ...          # contrib must keep working
def test_workspace_tool_absent_from_deferred_catalog(config): ...
```

`build_tool_list` returns a tuple — unpack it rather than iterating the return value
directly (cost me a probe iteration). `_skill_def_cache` is keyed by `id(config)`, so
clear it in tests that build a tool list twice.

**Verification — automated:**
- [x] Each attack test fails pre-fix and passes after — **4 attack tests red pre-fix**
      (catalog build, refresh_skills, build_tool_list, eval loadout) with the 3
      positive controls green; **8 passed** after.
- [x] `.venv/bin/python -m pytest tests/test_skill_native_tools_tier.py -q` — **8 passed**
- [x] `make test` passes — **3693 passed, 2 skipped**
- [x] `make check` passes — **ruff clean; pyright 0 errors, 0 warnings**
- [x] Teeth probe: delete the `grants_capability` guard in `tool_definitions.py` →
      **2 failed** (`test_build_tool_list_does_not_exec_workspace_tools_py`,
      `test_collect_all_tool_defs_omits_unactivated_workspace_tool`); restored by
      re-editing, diff verified clean.
- [x] Teeth probe: same for `build_skill_tool_owners` → **2 failed**
      (`test_catalog_build_...`, `test_refresh_skills_...`); restored, diff verified.
- [!] One planned test had no teeth and was replaced.
      `test_build_tool_list_omits_unactivated_workspace_tool` asserted on
      `build_tool_list`'s **active** list and passed *before* the fix — an unactivated
      skill's tools are deferred, not active, so the property held either way. This is
      the "looks like coverage, cannot fail" shape reviewers caught four times on #742.
      Replaced with `test_collect_all_tool_defs_omits_unactivated_workspace_tool`,
      which asserts at the layer the preload actually feeds and does fail pre-fix
      (confirmed in the probe above).

**Verification — manual:**
- [x] `make eval-tools` still assembles a loadout (the eval path change didn't break
      tool-choice fixtures). Note: per prior sessions its failure *set* shifts
      run-to-run, so the check is "it runs and assembles", not a pass count. —
      **run twice: 26/32 (6 fails) then 25/32 (7 fails), different sets**, consistent
      with the known instability. All failures are vault / notes / tabstack / canvas
      tool-choice cases; none involve skills or tiers.
      **The pass count is not the evidence, though.** A read-only audit of skill
      discovery + tool-owner indexing against the real `data/decafclaw/` is
      **byte-identical** between `origin/main` and this branch (130 skills, 47 owners),
      so the eval sees the same tool definitions either way and this change cannot move
      its results. Reason: the two workspace-tier skills present (`hello`, `weather`)
      have no `tools.py`, so all three `_load_native_tools` guards are no-ops here.
      `make eval-history` has no records — it tracks `make eval`, not `eval-tools`.

---

## Phase 4: `$SKILL_DIR` refuses workspace tier (#739)

A trusted schedule's `shell_patterns: ["$SKILL_DIR/fetch.sh*"]` must not expand to an
agent-writable directory.

**Files:**
- Modify: `src/decafclaw/schedules.py` — `_resolve_skill_dir` (~line 579) and its
  docstring.
- Test: `tests/test_schedule_skill_dir_tier.py` — new.

**Key changes:** replace the two-step lookup with one loop over the same candidate
order (task name first, then each `required-skills` entry), so a rejected candidate
falls through to the next exactly as an unresolvable one does today:

```python
    all_skills = {s.name: s for s in (config.discovered_skills or [])}
    for candidate in (task.name, *task.required_skills):
        info = all_skills.get(candidate)
        if info is None:
            continue
        if not grants_capability(info):
            # workspace/skills/ is agent-writable AND the highest-precedence
            # scan entry, so an agent-planted skill would repoint $SKILL_DIR —
            # and a trusted schedule expands it into a *pre-approved* shell
            # pattern on an unattended turn (#739).
            log.warning(
                "Scheduled task %r: ignoring %s-tier skill %r when resolving "
                "$SKILL_DIR", task.name, info.trust_tier, candidate)
            continue
        return str(info.location.resolve())
    return str(task.path.parent.resolve())
```

Docstring additions: the tier rule, and why it is **unconditional** rather than gated
on `task.source` — `skill_dir` feeds both the shell pattern and `substitute_body`, and
the existing docstring's whole point is that those two stay in sync. Gating only the
grant path would desync them.

Tests:

```python
def test_admin_schedule_skill_dir_skips_workspace_shadow(config):
    """The shadowing case: an admin-tier skill of the same name also exists."""

def test_admin_schedule_skill_dir_skips_workspace_only_skill(config):
    """The case direction B would NOT have fixed: no trusted skill of that
    name exists at all, so there is no collision to block."""

def test_skill_dir_and_body_stay_in_sync(config):
    """Assert the prompt body's $SKILL_DIR expansion equals the value the
    shell pattern expanded to — the invariant the docstring exists for."""

def test_admin_tier_skill_dir_unchanged(config): ...   # positive control
def test_extra_tier_skill_dir_unchanged(config): ...   # the contrib overlay case
```

Attack tests assert on `ctx.tools.preapproved_shell_patterns` — that no entry contains
the workspace skills path — using the `_ctx_for` stubbed-turn helper pattern from
`tests/test_schedule_required_skills_tier.py`.

**Verification — automated:**
- [x] Both attack tests fail pre-fix, pass after — **2 failed pre-fix**, and the
      failure output showed the exploit itself:
      `['.../workspace/skills/feeds/fetch.sh*']` pre-approved. **5 passed** after.
- [x] `.venv/bin/python -m pytest tests/test_schedule_skill_dir_tier.py -q` — **5 passed**
- [x] `make test` passes — **3698 passed, 2 skipped** (after the cross-file fix below)
- [x] `make check` passes — **ruff clean; pyright 0 errors, 0 warnings**
- [x] Teeth probe: remove the `grants_capability` branch → **2 failed**
      (both attack tests); restored, diff verified.
- [x] Teeth probe: gut resolution so it always falls back to `task.path.parent` →
      **2 failed** (`test_admin_tier_skill_dir_unchanged`,
      `test_extra_tier_skill_dir_unchanged`). Confirms the positive controls test
      resolution, not merely the absence of the workspace path. Restored.
- [x] **Cross-file breakage caught only by the full suite.** Two pre-existing tests in
      `tests/test_schedules.py` failed:
      `test_overlay_resolves_skill_dir_via_required_skill` and
      `test_skill_dir_iterates_required_skills_for_first_resolvable`. Both construct
      `SkillInfo(...)` without `trust_tier`, so Phase 2's flipped default made them
      workspace-tier and this phase's gate rejected them. Their fixtures use
      `/real/contrib/skills/...` locations, so `trust_tier="extra"` is the tier they
      model; set explicitly with a comment. **This is a correction to Phase 2's
      measurement:** "exactly 1 failure" was true when measured, but the default
      flip's blast radius grows as each later phase adds a consumer that rejects
      workspace tier. Running only the phase's own test file would have missed this.

**Verification — manual:**
- [x] The docstring still explains the overlay anchor problem it originally solved. —
      **verified in the diff**: the original three paragraphs are untouched; the tier
      rule and the "why unconditional" note are appended after them.

---

## Phase 5: workspace bodies out of trusted prompts (#740)

**Files:**
- Modify: `src/decafclaw/schedules.py` — `_render_required_skill_bodies` (~line 614).
- Test: `tests/test_schedule_required_skill_body_tier.py` — new.

**Key changes:**

```python
        if not grants_capability(info):
            # <loaded_skills> is not the catalog — it is the full body,
            # presented as instructions currently in force, on a turn that
            # installs real pre-approvals. Mirrors prompts/__init__.py:101.
            log.warning(
                "Schedule %r: skipping body injection for %s-tier skill %r",
                ...)
            continue
```

Tests assert on the **prompt text passed to `enqueue_turn`**:

```python
def test_admin_schedule_omits_workspace_skill_body(config):
    """Body text absent, and no <skill name="evil"> block."""

def test_admin_schedule_includes_admin_skill_body(config): ...   # positive control
```

**Verification — automated:**
- [x] Attack test fails pre-fix, passes after — **1 failed pre-fix, 3 passed after.**
      The pre-fix run's captured log is the incoherence #740 describes: both the
      Phase 4 `$SKILL_DIR` gate and #742's activation gate fired for `helper`, and the
      body still landed in the prompt.
- [x] `.venv/bin/python -m pytest tests/test_schedule_required_skill_body_tier.py -q`
      — **3 passed**
- [x] `make test` passes — **3701 passed, 2 skipped** (after the cross-file fix below)
- [x] `make check` passes — **ruff clean; pyright 0 errors, 0 warnings**
- [x] Teeth probe: remove the guard → **1 failed**
      (`test_admin_schedule_omits_workspace_skill_body`); restored.
- [x] **Third instance of the Phase 2 ripple.**
      `tests/test_schedules.py::TestRunScheduleTask::
      test_required_skill_body_injected_into_prompt` built `SkillInfo` with no
      `trust_tier`; its task is `source="extra"`, so `trust_tier="extra"` is what it
      models. The pattern is now established: **the flipped default surfaces roughly
      one pre-existing test per newly-added consumer**, and only a full-suite run
      finds them. Carried into `notes.md`.

**Verification — manual:**
- [x] Warning text names both the skill and the task, so the omission is diagnosable
      from logs alone. — **partially: it names the skill and its tier, not the task.**
      `_render_required_skill_bodies` receives only `(config, skill_names)`, so the
      task name isn't in scope. Threading it in for a log line was judged not worth
      widening the signature; the `$SKILL_DIR` warning on the same turn does name the
      task, so the pair is diagnosable together. Recorded rather than silently
      dropped.

---

## Phase 6: `!name` pre-approvals and dependencies (#737)

Two independent gates in `execute_command`. Restriction is unchanged at every tier —
only granting moves.

**Files:**
- Modify: `src/decafclaw/commands.py` — `execute_command` (~lines 384-388 and 399-417).
- Test: `tests/test_command_tier_trust.py` — new.

**Key changes:**

```python
    # Frontmatter RESTRICTS at every tier; it only GRANTS pre-approval at a
    # capability tier. `workspace/skills/` is agent-writable, so a skill the
    # agent wrote must not pre-approve its own shell commands (#737).
    # Fork-mode's hard restriction (`ctx.tools.allowed`, below) is unaffected.
    if grants_capability(skill):
        ctx.tools.preapproved = set(skill.allowed_tools)
        ctx.tools.preapproved_shell_patterns = [
            p.replace("$SKILL_DIR", str(skill.location))
            for p in skill.shell_patterns
        ]
    elif skill.allowed_tools or skill.shell_patterns:
        log.warning(
            "Command %r is %s tier: its allowed-tools/shell patterns restrict "
            "but do not pre-approve", skill.name, skill.trust_tier)
```

Leaving the attributes unassigned (rather than assigning empty) keeps the ctx defaults
and avoids implying a grant was computed.

```python
            req_info = skill_map.get(req_name)
            if req_info is not None and not grants_capability(req_info):
                # Typing `!name` approves THAT skill, not a dependency list the
                # agent controls. Activation execs the dependency's tools.py.
                log.warning(
                    "Command %r: skipping %s-tier required skill %r",
                    skill.name, req_info.trust_tier, req_name)
                continue
```

The command's **own** activation (line 394) stays ungated: the human named it, and
`activate_skill_internal` is documented as the without-permission-checks path for
exactly this caller.

Tests:

```python
def test_workspace_command_does_not_preapprove(config): ...
def test_workspace_command_shell_patterns_not_preapproved(config): ...
def test_extra_tier_command_still_preapproves(config):
    """Guards the six shipping contrib commands."""
def test_admin_tier_command_still_preapproves(config): ...
def test_workspace_required_skill_not_activated(config):
    """Marker-file assertion: the dependency's tools.py must not run."""
def test_workspace_command_still_runs(config):
    """Not over-corrected — restriction and body substitution still work."""
```

**Verification — automated:**
- [x] Each attack test fails pre-fix, passes after — **2 failed pre-fix** (the
      pre-approval leak showed `preapproved={'vault_read'}` on a workspace command);
      **6 passed** after.
- [x] `.venv/bin/python -m pytest tests/test_command_tier_trust.py -q` — **6 passed**
- [x] `make test` passes — **3707 passed, 2 skipped** (after the cross-file fixes below)
- [x] `make check` passes — **ruff clean; pyright 0 errors, 0 warnings**
- [x] Teeth probe A: force the pre-approval branch always-on → **1 failed**
      (`test_workspace_command_does_not_preapprove`); restored.
- [x] Teeth probe B: change the guard to `_PREAPPROVAL_TIERS` (the thing #737
      suggested) → **1 failed** (`test_extra_tier_command_still_preapproves`), with the
      log line `Command 'doit' is extra tier: its allowed-tools/shell patterns restrict
      but do not pre-approve` — exactly what `!rss-ingest` would print. The spec's
      argument is now demonstrated by construction, not just asserted. Restored.
- [x] Teeth probe C: remove the `requires_skills` gate → **1 failed**
      (`test_workspace_required_skill_not_activated`); restored. Final diff re-read to
      confirm all three probes were fully reverted.
- [x] **A false green in my own test harness, caught before it shipped.**
      `test_workspace_required_skill_not_activated` initially passed *pre-fix*.
      Cause: `execute_command` resolves `requires_skills` via
      `ctx.config.discovered_skills`, which the `config` fixture leaves empty — so the
      dependency was never resolvable and nothing was activated for a reason unrelated
      to tier. It would have passed against the vulnerable code. Fixed by having the
      `_skill()` helper publish `config.discovered_skills`, with the reason recorded in
      its docstring. Surfaced because the *positive control*
      (`test_admin_required_skill_still_activated`) failed pre-fix, which it never
      should have — the positive control is what exposed the broken harness.
- [x] **Three more Phase 2 ripples** in `tests/test_commands.py`: `test_inline_mode`,
      `test_fork_mode`, `test_fork_required_skills_activated`. All build `SkillInfo`
      without `trust_tier`; all now set `trust_tier="bundled"` since their subjects are
      inline/fork dispatch, not tier policy.

**Verification — manual:**
- [x] `!rss-ingest`-shaped case: an `extra`-tier skill with
      `shell($SKILL_DIR/fetch.sh*)` still lands that pattern pre-approved. —
      **covered by an automated test**, `test_extra_tier_command_still_preapproves`,
      which asserts `{skill_dir}/fetch.sh*` is in `preapproved_shell_patterns`. Probe B
      proves it can fail.

---

## Phase 7: The forcing function (#741)

An AST test that fails when a new consumer of `discovered_skills` appears without a
recorded tier decision. This is the deliverable that makes #741 closable rather than
just its instances.

**Files:**
- Test: `tests/test_discovered_skills_consumers.py` — new.

**Key changes:**

```python
# (relative path, enclosing function) -> why this read is safe.
REVIEWED_CONSUMERS = {
    ("schedules.py", "_resolve_skill_dir"):
        "grants_capability filter; workspace candidates fall through (#739)",
    ("schedules.py", "_render_required_skill_bodies"):
        "grants_capability skip; body is instructions-in-force (#740)",
    ("commands.py", "execute_command"):
        "grants_capability gates pre-approvals and requires_skills (#737)",
    ("tool_definitions.py", "build_tool_list"):
        "grants_capability before _load_native_tools, which execs (#744)",
    ("interactive_terminal.py", "run_interactive"):
        "prints names only; no grant",
    # ... one entry per read, each naming where the decision lands
}
```

Scan rules, stated in the test's docstring because they bound what it can prove:

- **Reads only** — `ast.Attribute` with `attr == "discovered_skills"` and
  `isinstance(node.ctx, ast.Load)`, plus function parameters named
  `discovered_skills`. Writes (`config.discovered_skills = ...`) can't leak, so they
  are excluded; that removes the several assignment sites in `__init__.py`,
  `delegate.py`, `skill_tools.py` and `eval/runner.py`.
- String literals are `ast.Constant`, so `config_cli.py`'s `"discovered_skills"` entry
  is not a consumer.
- **Known limitation, stated in the docstring:** a helper that receives the list under
  a different parameter name (e.g. `build_skill_tool_owners(skills)`) is invisible to
  the scan. It is reached via its *call site*, which does read
  `config.discovered_skills` and therefore is in the registry — so registry reasons
  must name where the decision lands, including "delegates to X". That chain is the
  mitigation, not an accident.
- **What it proves:** a decision was *recorded*, not that it is *correct* — the same
  bound as `test_schedule_tier_trust.py`, which pins a partition rather than a policy.

The test asserts set equality both ways: an unregistered consumer fails ("decide its
tier behavior and add it"), and a stale registry entry fails too ("this consumer no
longer exists — remove it"), so the registry can't rot into a pile of dead entries.

**Verification — automated:**
- [x] `.venv/bin/python -m pytest tests/test_discovered_skills_consumers.py -q` —
      **3 passed**. The scan finds **23 unique consumers** across 29 reads; all 23 are
      registered.
- [x] `make test` passes — **3710 passed, 2 skipped**
- [x] `make check` passes — **ruff clean; pyright 0 errors, 0 warnings**
- [x] Teeth probe: add a throwaway consumer to `src/decafclaw/util.py` → **1 failed**,
      naming `[('util.py', '_probe_new_consumer')]` and quoting the instruction to route
      through `grants_capability`. Removed by deleting exactly the appended lines and
      confirming `git diff --stat src/decafclaw/util.py` was empty — not `git checkout`,
      which would discard unrelated uncommitted work in that file.
- [x] Teeth probe: delete one registry entry (`interactive_terminal._print_banner`) →
      **1 failed**, naming that consumer. Restored.
- [x] A third guard was added beyond the plan: `test_registry_reasons_are_substantive`
      rejects reasons under 40 chars or matching a vague-label set
      (`safe` / `ok` / `n/a` / …), so the registry can't degrade into labels. It also
      states in its own docstring that it is not a correctness check.

**Verification — manual:**
- [x] Read the registry end to end. Every reason is specific enough to re-audit from,
      and none says only "safe". — **read all 23.** Each names either the mechanism
      (`grants_capability` filter / skip), the delegate (`build_skill_tool_owners`), the
      upstream decision (`ctx.skills.activated`), or the reason no grant is possible.
      Two carry findings rather than reassurance: `tool_activate_skill` records that the
      gate is in the wrapper and not in `activate_skill_internal` (the shape #744
      exploited), and `restore_skills` records the standing-`always`-grant caveat below.

---

## Phase 8: Docs

**Files:**
- Modify: `docs/skills.md` — the trust-tier model; what `workspace` tier cannot do
  (activate unattended, always-load, auto-approve, pre-approve, anchor `$SKILL_DIR`,
  inject a body into a trusted prompt, have `tools.py` imported pre-activation).
- Modify: `docs/schedules.md` — `$SKILL_DIR` resolution refuses workspace tier, and
  what a schedule sees when it happens (fallback + warning).
- Modify: `CLAUDE.md` — one line under Skills: reads of `discovered_skills` that grant
  capability must consult `grants_capability`; `tests/test_discovered_skills_consumers.py`
  enforces it.
- Modify: session `notes.md` — the durable record (see below).

`notes.md` must carry, since nothing else survives the squash: the six-exit table with
how each was found; the two issue claims the spec rejected and why (direction B's gap,
`_PREAPPROVAL_TIERS` reuse breaking six contrib skills); the Phase 2 measured number;
the follow-ups (#738, non-shadowing, pushing the rule into
`activate_skill_internal`/`_load_native_tools` and why it needs a parameter).

TDD opt-out, stated explicitly: this phase is docs only, no behavior.

**Verification — automated:**
- [x] `make check` passes — **All checks passed** (incl. the message-types drift check)
- [x] `make test` passes — **3710 passed, 2 skipped**
- [x] `make test-js` passes (no JS touched; run once to confirm the branch is clean) —
      **9 files, 83 passed**

**Verification — manual:**
- [x] `docs/skills.md` and `docs/schedules.md` describe the shipped behavior, not the
      intended behavior — reread against the diff. — **found and fixed one stale claim
      while doing this:** `docs/schedules.md`'s "Pre-activated skills" section said "The
      full `SKILL.md` body of each listed skill is rendered", which stopped being true
      in Phase 5. Now says *capability-tier* and states that a workspace-tier skill is
      neither activated nor injected, so a thin trigger naming one finds no instructions.
- [x] No doc claims #738 is fixed. — **verified**: no doc mentions `pre_script`
      resolution changing. `notes.md` lists #738 as an explicit out-of-scope follow-up,
      including that `docs/schedules.md`'s frontmatter table still documents resolution
      the code doesn't implement.

---

## Plan self-review

**Spec coverage.** Predicate → P1. Fail-closed default → P2. All three
`_load_native_tools` callers → P3. `_resolve_skill_dir` → P4.
`_render_required_skill_bodies` → P5. `commands.py` both gates → P6. Forcing-function
test → P7. Docs → P8. Attack + positive-control + teeth-probe are per-phase
checkboxes, not a separate phase, so a phase can't be called done without them.

**Placeholders.** None. Two things are deliberately deferred to implementation with the
resolution method named rather than the answer: `SkillInfo`'s exact required fields for
the `_info()` test helper (read off the dataclass), and whether
`tool_definitions.py`'s `grants_capability` import needs to be function-level (only if
there's a real cycle, which is the sole sanctioned reason).

**Type consistency.** One predicate name throughout: `grants_capability(info)` taking a
`SkillInfo`, returning `bool`. Constants `SKILL_TIERS` / `SKILL_CAPABILITY_TIERS`,
distinct from the existing `SCHEDULE_TIERS` / `_PREAPPROVAL_TIERS`.

**Ordering risk.** P2 is the one phase that can balloon; it is independent of P1 and of
every later phase, so if the measured churn is bad it can be dropped without disturbing
the security work.

**One check I deliberately did not write as an assertion.** "`make test` is still
3675 passed" appears only in Phase 1, where no behavior changes. Later phases add
tests, so the number moves; asserting a specific total per phase would be a
single-observation fact.
