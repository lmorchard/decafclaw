# Unattended turns get the same allowlist as everyone else — Implementation Plan

**Goal:** Remove the two `heartbeat-admin` approval bypasses so unattended turns are subject to the
same allowlist as interactive users, and deny outright instead of waiting on a prompt nobody can answer.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/649 — **Tier:** `needs-review`
(risk-gated: an authorization control on unattended shell execution. The remediation policy is now
decided, so trigger 1 no longer fires; trigger 2 is decisive on its own.)

**Approach:** Introduce one shared predicate on `Context` for "no human can answer a prompt on this
turn", then use it at both approval sites. The shell site loses its blanket auto-approve and gains an
early denial at the fallthrough; the skill site loses `is_heartbeat` from its confirmation condition
and gains the same early denial. Keying off `ctx.task_mode` rather than a `user_id` string is what
makes the fix cover `heartbeat-workspace` and every `schedule-*` turn, which the old check missed.

**Criteria:** C1 unattended shell not auto-approved · C2 unattended shell denies without prompting ·
C3 unattended workspace-skill activation denied without prompting
(Full text + checks in `checks.md`. Frozen at `b53ac87`.)

---

## Phase 0: Freeze the acceptance checks — DONE

Written before this plan existed, per `references/frozen-checks.md`.

**Files:**
- Created: `{session-dir}/checks.md`
- Created: `tests/test_unattended_approval.py` — **frozen, read-only from Phase 1 onward**

**Verification — automated:**
- [x] Every criterion's check runs and fails for the expected reason — 3 failed on genuine
      behavioural assertions; signatures recorded in `checks.md`
- [x] Every guard runs and passes — G1–G4 (4 passed in the same file), G5 `29 passed`,
      G6 `130 passed`, G7 `make check` exit 0
- [x] 7 tests collected, exit 1 — **not exit 5**, so no check silently failed to run
- [x] Freeze commit `b53ac87`; sha recorded in `checks.md` in a follow-up commit

---

## Phase 1: One unattended predicate, and the shell approval path

Unattended shell commands stop being auto-approved, and stop waiting 60s on an unanswerable prompt.

**Advances:** C1, C2 — completely. Also builds the predicate Phase 2 reuses.

**Files:**
- Modify: `src/decafclaw/context.py` — add an `is_unattended` property to `Context`.
- Modify: `src/decafclaw/tools/shell_tools.py` — delete the `heartbeat-admin` auto-approve
  (lines 142-144); add an early denial immediately before `request_confirmation`.
- Test: none of its own. The frozen `tests/test_unattended_approval.py` already covers both criteria
  and is read-only; adding a parallel unit test for the same behaviour would be duplicate coverage.

**Key changes:**

`context.py` — after the `task_mode` assignment block, a property beside `for_task`:

```python
    # Turn kinds where no human is watching, so a confirmation prompt cannot be
    # answered: it is emitted to subscribers of an ephemeral conv_id and times
    # out into a denial 60s later. Deliberately NOT `task_mode != ""`:
    # child_agent inherits the parent's request_confirmation, so a child of an
    # interactive turn does have someone who can answer.
    UNATTENDED_TASK_MODES = frozenset({"heartbeat", "scheduled"})

    @property
    def is_unattended(self) -> bool:
        return self.task_mode in self.UNATTENDED_TASK_MODES
```

`shell_tools.py` — remove the first branch entirely:

```python
    if ctx.user_id == "heartbeat-admin":                        # DELETE
        log.info(f"[{tool_name}] auto-approved for heartbeat: {command}")   # DELETE
        return {"approved": True}                               # DELETE
```

and gate the confirmation fallthrough:

```python
    suggested_pattern = _suggest_pattern(command)
    if ctx.is_unattended:
        # No subscriber can answer this prompt; it would block 60s and then be
        # synthesized into a denial. Deny now and say why.
        log.warning(
            f"[{tool_name}] denied on unattended turn (task_mode={ctx.task_mode!r}): {command}")
        return {"approved": False,
                "reason": "unattended turn: command matches no allow pattern"}
    result = await request_confirmation(
```

Everything between — the `preapproved` check, the scoped-pattern check, and the persisted-allowlist
check — is untouched, which is the point: unattended turns now traverse exactly the branches an
interactive user does, and differ only in what happens on a miss.

**Verification — automated:**
- [x] C1's check passes (exit 0): `uv run pytest tests/test_unattended_approval.py::test_unattended_shell_not_auto_approved -q`
- [x] C2's check passes (exit 0): `uv run pytest tests/test_unattended_approval.py::test_unattended_shell_denies_without_prompting -q`
- [x] G1 still passes (exit 0): `uv run pytest tests/test_unattended_approval.py::test_unattended_shell_allows_matching_pattern -q`
- [x] G4 still passes (exit 0): `uv run pytest tests/test_unattended_approval.py::test_interactive_still_prompts -q`
- [x] G5 still passes — observed `29 passed`: `uv run pytest tests/test_shell_allowlist.py -q` — 29 collected, none lost/skipped
- [x] G6 still passes — observed `130 passed`: `uv run pytest tests/test_skills.py tests/test_background_tools.py -q` — 130 collected
- [x] `make check` exit 0 — observed
- [x] No check reports exit 5 — all four named checks returned 0

**Verification — manual:** none. No human-judgment criterion in this spec.

---


**Phase 1 note:** `make test` is red at this point — `1 failed, 3423 passed, 2 skipped`,
the single failure being C3, which Phase 2 implements. Baseline was `3417 passed` plus the 7
new frozen tests = 3424 total, so nothing previously passing was lost. This is why the guards
are targeted commands rather than the aggregate: mid-plan, a later phase's criterion is
*supposed* to be failing, and a full-suite gate would either block Phase 1 or teach you to
ignore it.
## Phase 2: The skill-activation path

The second bypass of the same identity. An unattended turn can no longer activate a workspace-tier
skill it was never granted.

**Advances:** C3 — completely. Reuses `Context.is_unattended` from Phase 1.

**Files:**
- Modify: `src/decafclaw/tools/skill_tools.py` — drop `is_heartbeat` from the confirmation
  condition (lines 281, 286); deny before prompting when the turn is unattended.
- Test: none of its own, for the same reason as Phase 1.

**Key changes:**

Delete the `is_heartbeat` local (line 281) and its clause at 286, then guard the confirmation:

```python
    is_trusted_tier = skill_info.trust_tier != "workspace"
    perms = _load_permissions(ctx.config)
    if perms.get(name) == "deny":
        return ToolResult(text=f"[error: activation of skill '{name}' was denied by user]")
    if (not is_trusted_tier
            and perms.get(name) != "always"
            and not skill_info.auto_approve):
        # Workspace tier only at this point.
        if ctx.is_unattended:
            log.warning(
                f"[tool:activate_skill] denied on unattended turn "
                f"(task_mode={ctx.task_mode!r}): workspace-tier skill '{name}' has no standing grant")
            return ToolResult(
                text=f"[error: activation of skill '{name}' was denied by user]")
        approved, always = await _request_skill_confirmation(ctx, name)
```

Note what is deliberately *not* changed: the `"deny"` precedence at the top, the trusted-tier skip,
the `"always"` grant, and `auto_approve`. A bundled/admin/extra skill still activates on an
unattended turn with no prompt (guard G2), and a human's standing `"always"` grant still works
(guard G3) — that grant is the intended way to let an unattended turn use a workspace skill.

**On the denial message:** the frozen check asserts the returned text contains
`"was denied by user"`, so this reuses the existing denial string rather than a more accurate
"denied: unattended turn" wording. That is the frozen check constraining the implementation, which
is its job — and no amendment is warranted, because the existing 60s-timeout path already returns
the same slightly-loose phrasing for a denial no user actually issued. Worth a follow-up issue for
the message across both paths, not a change smuggled in here.

**Verification — automated:**
- [x] C3's check passes (exit 0): `uv run pytest tests/test_unattended_approval.py::test_unattended_workspace_skill_denied_without_prompting -q`
- [x] G2 still passes (negative control, exit 0): `uv run pytest tests/test_unattended_approval.py::test_unattended_bundled_skill_still_activates -q`
- [x] G3 still passes (exit 0): `uv run pytest tests/test_unattended_approval.py::test_always_grant_authorizes_unattended -q`
- [x] Whole frozen file — observed `7 passed`, 7 collected: `uv run pytest tests/test_unattended_approval.py -q` — 7 collected, 7 passed
- [x] G5 `29 passed`, G6 `130 passed`; `make check` exit 0. Full suite `3424 passed, 2 skipped` = baseline 3417 + 7 new, nothing lost
- [x] No check reports exit 5 — every named check returned 0

**Verification — manual:** none.

---

## Self-review notes

- **Criteria coverage, both directions:** C1+C2 → Phase 1; C3 → Phase 2. Every phase advances at
  least one criterion (Phase 0 is the freeze, which the template exempts). No criterion is
  unadvanced.
- **Checks cited by exact command** in every phase, taken from `checks.md`.
- **Type consistency:** `is_unattended` is defined in Phase 1 and referenced in Phase 2 under the
  same name; `UNATTENDED_TASK_MODES` is a class attribute on `Context`, referenced via `self`.
- **Scope discipline:** `_suggest_pattern`'s wildcarding, the denial-message wording, and
  `child_agent`/`background_wake` are all out of scope per the issue's "What we're NOT doing".
