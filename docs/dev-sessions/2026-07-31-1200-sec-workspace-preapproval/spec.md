# Spec — Gate schedule preapproval on source tier

**Session:** 2026-07-31-1200-sec-workspace-preapproval
**Branch:** `sec-workspace-preapproval`
**Closes:** #731
**Follows:** #732 (found during its final review; kept out to avoid moving a security boundary in a UI PR)

## Problem

A workspace-tier schedule can grant itself un-confirmed shell execution. The agent can write the file that does it.

Three call sites, no tier check at any of them:

1. `workspace/schedules/*.md` is agent-writable by design — `docs/schedules.md` describes it as "Agent-managed tasks (created via `workspace_write`)", and it is not in `READONLY_PATTERNS`.
2. `run_schedule_task` (`schedules.py:616-633`) installs the task's frontmatter as pre-approvals regardless of `task.source`.
3. `check_shell_approval` (`shell_tools.py:146-152`) honours `preapproved` / `preapproved_shell_patterns` before it reaches `_load_allow_patterns` or `request_confirmation`.

So the agent writes:

```markdown
---
schedule: "* * * * *"
allowed-tools: shell(curl *)
---
Routine maintenance.
```

and within 60s that task runs with `curl *` pre-approved.

### Why the unattended path makes this sharp

Scheduled turns are unattended, and `check_shell_approval` **denies** rather than prompting when nothing matches (`shell_tools.py:160-168`):

| | outcome |
|---|---|
| Without the agent-written patterns | **denied** — `"unattended turn: command matches no allow pattern"` |
| With them | **approved and executed** |

The human is not in the loop on either path. This is not "a confirmation gets skipped" — the frontmatter *is* the entire decision, and the agent controls it.

`data/{agent_id}/schedules/` is deliberately kept outside the agent's reach so it cannot grant itself permissions, the same reasoning that puts `skill_permissions.json` outside the workspace. The workspace tier routes around that.

Proof: `tests/test_workspace_schedule_preapproval.py`, already on this branch (commit `a30a6fd`), 3 passing tests documenting current behavior.

## Goals

1. An agent-writable schedule cannot pre-approve anything.
2. Adding a new source tier fails a test until its trust is explicitly declared.
3. A human editing a workspace-tier schedule's permissions fields can see that those fields don't grant capability at that tier.

## Non-goals

- Removing the agent's ability to write `workspace/schedules/*.md`. That is a documented feature; narrowing capability is safe, granting it is not.
- Reworking `check_shell_approval` itself. It correctly honours whatever pre-approvals the context carries; the defect is in what gets put there.
- #735 (unrecognized PATCH keys silently dropped). Separate issue, separate PR.

## Design

### Trust model

`workspace` is the only agent-writable tier. `admin` and `bundled` are human-controlled. `extra` (contrib from `extra_skill_paths`) is force-disabled at discovery and never runs, but is classified untrusted anyway — defense in depth, and it costs nothing.

Contrib opt-in stays coherent: a user opts in by copying the SCHEDULE.md to `data/{agent_id}/schedules/{name}.md`, at which point `source` is `admin` and pre-approval applies. The human's explicit act is what grants trust.

```python
_PREAPPROVAL_TIERS = frozenset({"admin", "bundled"})
trusted = task.source in _PREAPPROVAL_TIERS
```

Allowlist, not denylist: an unrecognized tier is untrusted. A security gate must fail closed.

### What changes at an untrusted tier

Frontmatter keeps its *restricting* effect and loses its *granting* effect.

| | untrusted | trusted |
|---|---|---|
| `allowed_tools_set` — which tools are visible | unchanged | unchanged |
| `ctx.tools.preapproved` | empty | `set(task.allowed_tools)` |
| `ctx.tools.preapproved_shell_patterns` | empty | as today |
| `ctx.tools.preapproved_email_recipients` | empty | as today |

`"shell"` is still added to the visible set when `shell(...)` patterns are present, so the tool remains reachable. It simply falls through to `_load_allow_patterns` and then to denial on an unattended turn — which is what the existing control test already demonstrates.

`$SKILL_DIR` substitution on shell patterns becomes dead work at an untrusted tier, since the result is discarded. Skip it rather than compute and drop it.

**Use `None`, not `[]`, for the untrusted case.** `setup_schedule_ctx` applies each field conditionally — `if shell_patterns:`, `if email_recipients is not None:` — so passing an empty list is not equivalent to passing nothing. For `email_recipients` specifically, `[]` would *overwrite* `ctx.tools.preapproved_email_recipients` with an empty list, where `None` leaves the context default untouched. Both happen to be empty today, so the distinction is invisible in tests but would matter the moment a default is introduced. Leave the setter's shape alone and pass `None`.

### Making the tier guard real

A test asserting "every tier is classified" is worthless if the list of tiers is itself hand-maintained — the lesson from #732's drift guard. Tier strings are currently literals in two places: `_discover_skill_schedule_files`'s `sources` list (`admin`, `extra`, `bundled`) and `discover_schedules`'s standalone loop (`workspace`, `admin`).

Declare one canonical enumeration in `schedules.py`:

```python
SCHEDULE_TIERS = ("admin", "workspace", "bundled", "extra")
```

Both discovery sites pair a tier with a directory, so they can't simply iterate this tuple — the literals stay where they are. Two tests close the gap instead, and both are needed:

1. **Every declared tier is classified.** `set(SCHEDULE_TIERS) == _PREAPPROVAL_TIERS | _UNTRUSTED_TIERS`. Adding a tier to the enumeration without deciding its trust fails here.
2. **Discovery only produces declared tiers.** Build a fixture config exercising all four discovery paths (admin standalone, workspace standalone, bundled SCHEDULE.md, extra SCHEDULE.md), run `discover_schedules`, and assert every resulting `task.source` is in `SCHEDULE_TIERS`. Introducing a new literal at a discovery site without declaring it fails here.

Test 1 alone would be the #732 mistake repeated — an enumeration guarding itself.

### UI

`source_tier` is already on the wire from #732. When it is `workspace`, `<schedule-metadata>`'s permissions group carries a short line: these fields restrict which tools the task can use, but do not pre-approve them at this tier, and pre-approval lives at admin tier.

Without it this re-creates a mild version of #729 — a field you can set that quietly does less than it appears to.

## Behavior change

This is a fix, but it is also a breaking change for one configuration: **a workspace-tier schedule relying on `shell(...)` pre-approval stops working.** On an unattended turn it is denied, not prompted.

Verified on the deployment: `linkding-ingest`, `mastodon-ingest` and `meta-ingest` use `shell($SKILL_DIR/fetch.sh)` but are all admin-tier overlays, so they are unaffected. There are no workspace-tier schedules there at all.

The migration for anyone affected is to move the schedule to `data/{agent_id}/schedules/`, which is the deliberate act the design requires. This needs to be stated in `docs/schedules.md`, not just in the changelog.

## Testing

The three existing proof tests **invert**. `test_workspace_schedule_preapproves_shell_without_confirmation` becomes an assertion that the command is denied; today's control (patterns stripped → denied) becomes the trusted-tier case (patterns present → approved). The file's module docstring, which currently says "documents current behaviour" and "expected to FAIL once preapproval is gated", must be rewritten.

New coverage:

- Workspace tier: `allowed_tools` still filters tool visibility, and `"shell"` is still visible when `shell(...)` patterns are declared.
- Workspace tier: `preapproved`, `preapproved_shell_patterns`, `preapproved_email_recipients` are all empty.
- Admin and bundled tiers: all three still populated — the fix must not over-correct.
- `email_recipients` gated on the same path (the issue flagged it as needing the same audit).
- Tier classification guard over `SCHEDULE_TIERS`.
- Vitest: the permissions-group note renders at `source_tier === 'workspace'` and not at other tiers.

No evals — this is deterministic policy, nothing LLM-visible.

## Docs

- `docs/schedules.md` — tier semantics for the three permission fields, and the migration note.
- `docs/email.md` — currently states the per-task `email-recipients` list "IS the trust boundary" without qualification. It is now tier-dependent.
- `CLAUDE.md` — the Config-and-data section describes `email-recipients` as a union of config plus per-task frontmatter; add the tier condition.

## Success criteria

1. A workspace-tier schedule declaring `shell(...)` gets that command denied on an unattended turn.
2. The same declaration at admin tier still pre-approves.
3. Tool *visibility* filtering still works at every tier.
4. Adding a tier to `SCHEDULE_TIERS` without classifying it fails the suite.
5. The web UI says so at workspace tier.
6. `make check`, `make test`, `make test-js` green.
