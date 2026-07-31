# Gate Schedule Preapproval on Source Tier — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop an agent-writable (workspace-tier) schedule from pre-approving its own shell commands, email recipients, or tools, while leaving its ability to *restrict* itself intact.

**Architecture:** One allowlist of human-controlled tiers, consulted once in `run_schedule_task`. Frontmatter keeps its restricting effect at every tier and loses its granting effect at untrusted ones. Two tests keep the tier classification from rotting.

**Tech Stack:** Python 3.13, pytest (+xdist), Starlette; Lit 3 web components, vitest.

**Spec:** [`spec.md`](./spec.md) · **Closes:** #731

## Global Constraints

- Worktree: `.claude/worktrees/sec-workspace-preapproval`, branch `sec-workspace-preapproval`. Use an **absolute** `cd` in every command — a stray `cd` to the main clone makes tests pass in the wrong tree.
- Python venv is `.venv` (`uv sync`). Single-file pytest runs need `-n0`; do NOT pass `-p no:xdist`, it errors.
- `make check`, `make test`, `make test-js` must all be green before each commit.
- Do NOT push. Commit only.
- **This is a security gate. It must fail closed** — an unrecognized tier gets no pre-approval. Never restructure the check into a denylist.
- Bug fix = test first. Every task writes the test, watches it fail for the right reason, then implements.
- Stdlib imports at module level; no bare `except: pass`.

## File Structure

| File | Responsibility |
|---|---|
| `src/decafclaw/schedules.py` | `SCHEDULE_TIERS`, the trust sets, and the gate inside `run_schedule_task` |
| `tests/test_workspace_schedule_preapproval.py` | Existing proof tests, **inverted** to assert the fixed behavior, plus trusted-tier and visibility coverage |
| `tests/test_schedule_tier_trust.py` | **New.** The two guards keeping the tier classification honest |
| `src/decafclaw/web/static/components/schedule-metadata.js` | Workspace-tier note in the permissions group |
| `src/decafclaw/web/static/components/schedule-metadata.test.js` | Coverage for that note |
| `docs/schedules.md`, `docs/email.md`, `CLAUDE.md` | Tier semantics and the migration note |

---

### Task 1: Gate preapproval on source tier

**Files:**
- Modify: `src/decafclaw/schedules.py` (constants near the top; `run_schedule_task` at ~616-638)
- Modify: `tests/test_workspace_schedule_preapproval.py` (invert the existing three)
- Create: `tests/test_schedule_tier_trust.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `schedules.SCHEDULE_TIERS: tuple[str, ...]` — `("admin", "workspace", "bundled", "extra")`
  - `schedules._PREAPPROVAL_TIERS: frozenset[str]` — `{"admin", "bundled"}`
  - `schedules._UNTRUSTED_TIERS: frozenset[str]` — `{"workspace", "extra"}`

- [ ] **Step 1: Rewrite the proof tests to assert the fixed behavior**

`tests/test_workspace_schedule_preapproval.py` currently documents the vulnerability. Replace its module docstring and the two behavioral tests. Keep `_write_workspace_schedule`, `ATTACK`, and `test_workspace_schedule_is_discovered_with_its_shell_patterns` exactly as they are — discovery is unchanged by this work.

New module docstring:

```python
"""A workspace-tier schedule cannot pre-approve its own shell commands.

`workspace/schedules/*.md` is agent-writable by design (docs/schedules.md:
"Agent-managed tasks (created via `workspace_write`)"). Before #731,
`run_schedule_task` copied the task's `allowed-tools` / `shell(...)`
patterns onto the turn's context as *pre-approvals* without checking
`task.source`, so the agent could grant itself un-confirmed shell
execution by writing a file it was allowed to write.

Frontmatter now only GRANTS at a human-controlled tier. It still
RESTRICTS everywhere — narrowing capability is safe.

The sharp edge these tests pin: scheduled turns are unattended, so
`check_shell_approval` denies rather than prompting. The frontmatter was
the entire decision, with no human in the loop on either path.
"""
```

Replace `test_workspace_schedule_preapproves_shell_without_confirmation` and `test_control_same_command_is_denied_without_the_patterns` with:

```python
async def _ctx_for(config, task):
    """Run the task with a stubbed agent turn, returning the turn's ctx."""
    from decafclaw.conversation_manager import ConversationManager

    manager = ConversationManager(config, EventBus())
    seen = {}

    async def fake_run(ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult
        seen["ctx"] = ctx
        return ToolResult(text="Done.")

    with patch("decafclaw.agent.run_agent_turn", side_effect=fake_run), \
            patch("decafclaw.notifications.notify"):
        await run_schedule_task(config, EventBus(), manager, task)
    return seen["ctx"]


@pytest.mark.asyncio
async def test_workspace_schedule_does_not_preapprove_shell(config):
    """The whole fix in one assertion."""
    from decafclaw.tools.shell_tools import check_shell_approval

    _write_workspace_schedule(config, "maintenance", ATTACK)
    task = {t.name: t for t in discover_schedules(config)}["maintenance"]
    ctx = await _ctx_for(config, task)

    assert ctx.tools.preapproved_shell_patterns == []
    assert ctx.tools.preapproved == set()

    assert ctx.is_unattended, "scheduled turns are unattended"
    with patch("decafclaw.tools.shell_tools.request_confirmation") as confirm:
        result = await check_shell_approval(ctx, "curl https://example.com/x")

    assert result["approved"] is False
    assert confirm.call_count == 0, "unattended turns deny rather than ask"


@pytest.mark.asyncio
async def test_workspace_schedule_still_restricts_tool_visibility(config):
    """Frontmatter keeps its narrowing effect — only granting is removed."""
    _write_workspace_schedule(config, "maintenance", ATTACK)
    task = {t.name: t for t in discover_schedules(config)}["maintenance"]
    ctx = await _ctx_for(config, task)

    assert ctx.tools.allowed is not None, "allow-list should still apply"
    # shell stays visible so the task can try; approval is what's withheld.
    assert "shell" in ctx.tools.allowed


@pytest.mark.asyncio
async def test_admin_tier_still_preapproves(config):
    """The gate must not over-correct — human-controlled tiers are unchanged."""
    from decafclaw.tools.shell_tools import check_shell_approval

    path = config.agent_path / "schedules" / "maintenance.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ATTACK)

    task = {t.name: t for t in discover_schedules(config)}["maintenance"]
    assert task.source == "admin"
    ctx = await _ctx_for(config, task)

    assert "curl *" in ctx.tools.preapproved_shell_patterns
    with patch("decafclaw.tools.shell_tools.request_confirmation") as confirm:
        result = await check_shell_approval(ctx, "curl https://example.com/x")
    assert result == {"approved": True}
    assert confirm.call_count == 0


@pytest.mark.asyncio
async def test_bundled_tier_still_preapproves(config):
    """The other trusted tier. Cheap to cover: the gate reads task.source,
    so setting it directly exercises the real branch without needing a
    fixture bundled skill."""
    _write_workspace_schedule(config, "maintenance", ATTACK)
    task = {t.name: t for t in discover_schedules(config)}["maintenance"]
    assert task.source == "workspace"
    task.source = "bundled"

    ctx = await _ctx_for(config, task)
    assert "curl *" in ctx.tools.preapproved_shell_patterns


@pytest.mark.asyncio
async def test_unknown_tier_fails_closed(config):
    """An unrecognized tier must get nothing — the allowlist's whole point."""
    _write_workspace_schedule(config, "maintenance", ATTACK)
    task = {t.name: t for t in discover_schedules(config)}["maintenance"]
    task.source = "some-future-tier"

    ctx = await _ctx_for(config, task)
    assert ctx.tools.preapproved_shell_patterns == []
    assert ctx.tools.preapproved == set()


EMAIL_TASK = """\
---
schedule: "* * * * *"
email-recipients:
  - ops@example.com
---

Send the report.
"""


@pytest.mark.asyncio
async def test_workspace_schedule_does_not_preapprove_email(config):
    """#731 flagged email_recipients as needing the same audit."""
    _write_workspace_schedule(config, "reporter", EMAIL_TASK)
    task = {t.name: t for t in discover_schedules(config)}["reporter"]
    ctx = await _ctx_for(config, task)

    assert ctx.tools.preapproved_email_recipients == []


@pytest.mark.asyncio
async def test_admin_schedule_does_preapprove_email(config):
    _write_admin_schedule = config.agent_path / "schedules" / "reporter.md"
    _write_admin_schedule.parent.mkdir(parents=True, exist_ok=True)
    _write_admin_schedule.write_text(EMAIL_TASK)

    task = {t.name: t for t in discover_schedules(config)}["reporter"]
    ctx = await _ctx_for(config, task)

    assert ctx.tools.preapproved_email_recipients == ["ops@example.com"]
```

- [ ] **Step 2: Run and confirm they fail for the right reasons**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/sec-workspace-preapproval
.venv/bin/python -m pytest tests/test_workspace_schedule_preapproval.py -q -n0
```

Expected: the two `workspace_..._does_not_preapprove_*` tests FAIL (patterns and recipients are still installed), and `test_workspace_schedule_still_restricts_tool_visibility` plus both `admin` tests PASS already. Record the exact output — if a test you expect to fail passes, stop and work out why before implementing.

- [ ] **Step 3: Declare the tiers and the trust classification**

In `src/decafclaw/schedules.py`, below `log = logging.getLogger(__name__)`:

```python
# Every source tier `discover_schedules` can assign. Declared in one place
# so the trust classification below can be checked for completeness.
SCHEDULE_TIERS = ("admin", "workspace", "bundled", "extra")

# Tiers whose frontmatter may GRANT capability — pre-approve tools, shell
# patterns, or email recipients. An allowlist, not a denylist: an
# unrecognized tier gets no pre-approval, because a security gate must
# fail closed.
#
# `workspace` is excluded because `workspace/schedules/*.md` is
# agent-writable by design, so an agent could otherwise approve its own
# shell commands (#731). `extra` (contrib) is excluded as defense in
# depth — it is force-disabled at discovery today, and a user opts in by
# copying the file to the admin dir, which makes its source `admin`.
_PREAPPROVAL_TIERS = frozenset({"admin", "bundled"})
_UNTRUSTED_TIERS = frozenset({"workspace", "extra"})
```

- [ ] **Step 4: Apply the gate**

In `run_schedule_task`, replace the block currently at ~616-638 (from `allowed_tools_set = None` through `email_recipients = task.email_recipients or None`) with:

```python
    # Frontmatter always RESTRICTS which tools are visible. It only GRANTS
    # pre-approval at a human-controlled tier — an agent-writable schedule
    # must not be able to approve its own shell commands (#731).
    trusted = task.source in _PREAPPROVAL_TIERS

    allowed_tools_set = None
    preapproved = set()
    if task.allowed_tools or task.shell_patterns:
        allowed_tools_set = set(task.allowed_tools)
        if task.shell_patterns:
            allowed_tools_set.add("shell")  # ensure shell tool is visible
        # Keep tool_search / activate_skill reachable so the model has
        # an escape hatch if the task is under-spec'd. They don't grant
        # capabilities on their own.
        allowed_tools_set |= _SCHEDULE_ESCAPE_HATCH_TOOLS
        if trusted:
            preapproved = set(task.allowed_tools)

    skill_dir = _resolve_skill_dir(config, task)
    shell_patterns = None
    if trusted and task.shell_patterns:
        shell_patterns = [
            p.replace("$SKILL_DIR", skill_dir) for p in task.shell_patterns
        ]

    # Per-task settings applied after the manager creates the context
    required_skills = list(task.required_skills)
    task_model = task.model
    email_recipients = (task.email_recipients or None) if trusted else None
```

Two things to get right:

- **`skill_dir` stays outside the `if`.** It is also used further down for `substitute_body(task.body, skill_dir=skill_dir)` (~line 672). Moving it inside the guard breaks `$SKILL_DIR` substitution in the prompt body.
- **`None`, not `[]`, for the untrusted case.** `setup_schedule_ctx` applies these conditionally (`if shell_patterns:`, `if email_recipients is not None:`). Passing `[]` for `email_recipients` would *overwrite* the context default instead of leaving it alone. Both are empty today so tests can't tell, but the shapes are not equivalent. Leave `setup_schedule_ctx` untouched.

- [ ] **Step 5: Run and confirm they pass**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/sec-workspace-preapproval
.venv/bin/python -m pytest tests/test_workspace_schedule_preapproval.py -q -n0
```

Expected: PASS.

- [ ] **Step 6: Write the two tier guards**

Create `tests/test_schedule_tier_trust.py`:

```python
"""Guards keeping the schedule tier trust classification honest.

The gate in `run_schedule_task` asks whether `task.source` is in
`_PREAPPROVAL_TIERS`. That is only safe if every tier a schedule can
actually be discovered with has had its trust decided deliberately.

Both tests below are needed. The first alone would be an enumeration
guarding itself — the mistake #732's drift guard was hardened against.
"""

from pathlib import Path

from decafclaw.schedules import (
    _PREAPPROVAL_TIERS,
    _UNTRUSTED_TIERS,
    SCHEDULE_TIERS,
    discover_schedules,
)

MINIMAL = '---\nschedule: "0 3 * * *"\n---\n\nBody.\n'


def test_every_declared_tier_is_classified():
    """Adding a tier without deciding its trust fails here."""
    classified = _PREAPPROVAL_TIERS | _UNTRUSTED_TIERS
    assert set(SCHEDULE_TIERS) == classified, (
        f"unclassified: {set(SCHEDULE_TIERS) - classified}; "
        f"stale: {classified - set(SCHEDULE_TIERS)}"
    )


def test_trust_sets_are_disjoint():
    assert not (_PREAPPROVAL_TIERS & _UNTRUSTED_TIERS)


def test_discovery_only_produces_declared_tiers(config, tmp_path, monkeypatch):
    """Using a new tier literal at a discovery site fails here.

    Exercises all four discovery paths so no tier can appear at runtime
    that `SCHEDULE_TIERS` does not know about.
    """
    # admin standalone
    admin = config.agent_path / "schedules" / "admin-standalone.md"
    admin.parent.mkdir(parents=True, exist_ok=True)
    admin.write_text(MINIMAL)

    # workspace standalone
    ws = config.workspace_path / "schedules" / "ws-standalone.md"
    ws.parent.mkdir(parents=True, exist_ok=True)
    ws.write_text(MINIMAL)

    # admin-level skill SCHEDULE.md
    admin_skill = config.agent_path / "skills" / "adminskill"
    admin_skill.mkdir(parents=True, exist_ok=True)
    (admin_skill / "SCHEDULE.md").write_text(MINIMAL)

    # extra-path (contrib) skill SCHEDULE.md
    extra_root = tmp_path / "extra-skills"
    extra_skill = extra_root / "contribskill"
    extra_skill.mkdir(parents=True, exist_ok=True)
    (extra_skill / "SCHEDULE.md").write_text(MINIMAL)
    monkeypatch.setattr(
        "decafclaw.schedules._resolve_extra_skill_paths",
        lambda _config: [Path(extra_root)],
    )

    tasks = discover_schedules(config)
    found = {t.source for t in tasks}

    # Bundled skills (dream, garden, newsletter) ship SCHEDULE.md, so the
    # bundled tier is exercised without any fixture setup.
    assert {"admin", "workspace", "extra", "bundled"} <= found, (
        f"fixture did not exercise every discovery path; got {found}"
    )
    assert found <= set(SCHEDULE_TIERS), (
        f"discovery produced undeclared tier(s): {found - set(SCHEDULE_TIERS)}"
    )
```

- [ ] **Step 7: Run the guards**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/sec-workspace-preapproval
.venv/bin/python -m pytest tests/test_schedule_tier_trust.py -q -n0
```

Expected: PASS. If `test_discovery_only_produces_declared_tiers` reports a missing tier, the fixture isn't reaching that discovery path — fix the fixture, do not weaken the assertion.

- [ ] **Step 8: Prove both guards have teeth**

Each probe is a temporary edit; **restore with your editor, never `git checkout`** (there is uncommitted work in this tree).

1. Add `"newtier"` to `SCHEDULE_TIERS` → `test_every_declared_tier_is_classified` must fail naming it. Restore.
2. In `discover_schedules`, change the `("workspace", ...)` literal to `("workspace2", ...)` → `test_discovery_only_produces_declared_tiers` must fail. Restore.

Record both outputs.

- [ ] **Step 9: Full verification and commit**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/sec-workspace-preapproval
make check && make test
git add src/decafclaw/schedules.py tests/test_workspace_schedule_preapproval.py tests/test_schedule_tier_trust.py
git commit -m "fix(#731): gate schedule preapproval on source tier

workspace/schedules/*.md is agent-writable by design, and
run_schedule_task installed its allowed-tools / shell(...) /
email-recipients frontmatter as pre-approvals without checking
task.source. check_shell_approval honours those before it reaches
confirmation, so the agent could grant itself un-confirmed shell
execution by writing a file it was allowed to write.

Scheduled turns are unattended, so the same command without those
patterns is denied rather than prompted — the frontmatter was the
entire decision, with no human in the loop on either path.

Frontmatter now only GRANTS at admin/bundled tier. It still RESTRICTS
everywhere: tool visibility is unchanged, and shell stays reachable so
it falls through to the normal allow-pattern check and then to denial.

The tier allowlist fails closed, and two guards keep it honest — one
that every declared tier is classified, one that discovery cannot
produce an undeclared tier."
```

---

### Task 2: Say so in the UI

**Files:**
- Modify: `src/decafclaw/web/static/components/schedule-metadata.js`
- Modify: `src/decafclaw/web/static/components/schedule-metadata.test.js`
- Modify: `src/decafclaw/web/static/styles/schedule-metadata.css`

**Interfaces:**
- Consumes: `data.source_tier` (already on the wire from #732; values are `SCHEDULE_TIERS` members).
- Produces: nothing downstream.

Without this, a human editing a workspace-tier schedule sets permission fields that quietly grant nothing — a mild replay of #729.

- [ ] **Step 1: Write the failing tests**

Append inside the existing `describe('schedule-metadata', ...)` block in `schedule-metadata.test.js`:

```js
  it('notes that permissions do not pre-approve at workspace tier', async () => {
    const el = mount({ source_tier: 'workspace' });
    await el.updateComplete;
    const note = el.querySelector('.sched-md-permissions-note');
    expect(note).toBeTruthy();
    expect(note?.textContent).toMatch(/admin/i);
  });

  it('shows no such note at admin tier', async () => {
    const el = mount({ source_tier: 'admin' });
    await el.updateComplete;
    expect(el.querySelector('.sched-md-permissions-note')).toBeNull();
  });

  it('shows no such note at bundled tier', async () => {
    const el = mount({ source_tier: 'bundled' });
    await el.updateComplete;
    expect(el.querySelector('.sched-md-permissions-note')).toBeNull();
  });
```

`BASE` (top of the file, ~lines 5-18) does **not** currently define `source_tier`, and `mount()` spreads overrides onto it. Add `source_tier: 'admin'` to `BASE` so every pre-existing test keeps exercising the no-note path and the overrides above are meaningful.

- [ ] **Step 2: Run and confirm they fail**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/sec-workspace-preapproval/src/decafclaw/web/static
npx vitest run schedule-metadata
```

Expected: the workspace test fails (`note` is `null`); the admin and bundled tests pass trivially. That asymmetry is expected — the negatives guard against a note that renders unconditionally.

- [ ] **Step 3: Render the note**

In `schedule-metadata.js`, inside the permissions group, directly after the `sched-md-permissions-title` div:

```js
          ${this.data?.source_tier === 'workspace' ? html`
            <div class="sched-md-permissions-note">
              At workspace tier these restrict which tools the task may use,
              but do not pre-approve them — this file is agent-writable.
              Pre-approval requires admin tier.
            </div>
          ` : nothing}
```

`nothing` is already imported in this file.

- [ ] **Step 4: Style it**

Append to `src/decafclaw/web/static/styles/schedule-metadata.css`:

```css
.sched-md-permissions-note {
  margin-bottom: 0.375rem;
  color: var(--pico-muted-color);
  font-size: 0.7rem;
}
```

- [ ] **Step 5: Run and confirm they pass**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/sec-workspace-preapproval
make test-js && make check-js
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/sec-workspace-preapproval
git add src/decafclaw/web/static/components/schedule-metadata.js \
        src/decafclaw/web/static/components/schedule-metadata.test.js \
        src/decafclaw/web/static/styles/schedule-metadata.css
git commit -m "feat(web): note that workspace-tier permissions do not pre-approve

The three permission fields still restrict tool visibility at every
tier, but only grant pre-approval at admin/bundled. Without saying so,
a human editing a workspace-tier schedule sets fields that quietly do
less than they appear to."
```

---

### Task 3: Documentation

**Files:**
- Modify: `docs/schedules.md`, `docs/email.md`, `CLAUDE.md`

- [ ] **Step 1: Document the tier rule in `docs/schedules.md`**

Add a subsection under the task-configuration material, near where `allowed-tools` is described:

```markdown
### Permissions are tier-dependent

`allowed-tools`, its `shell(...)` entries, `email-recipients`, and
`pre_script` do two separate things:

- **Restrict** which tools the task can see. This applies at every tier.
- **Pre-approve** those tools, shell commands and recipients so they
  bypass confirmation. This applies **only at admin and bundled tier**.

`workspace/schedules/*.md` is agent-writable, so honouring its
pre-approvals would let the agent grant itself un-confirmed shell
execution — see #731. Contrib (`extra`) is excluded for the same reason;
opting a contrib schedule in means copying it to
`data/{agent_id}/schedules/`, which makes it admin tier and restores
pre-approval as a deliberate human act.

Scheduled turns are unattended, so a shell command that matches no
pre-approval and no entry in `shell_allow_patterns.json` is **denied**,
not prompted. At workspace tier, `shell(...)` in frontmatter therefore
narrows what the task may attempt without granting anything.

`pre_script` is not run at all at an untrusted tier. Unlike the others it
has no approval path to fall through to — it executes arbitrary Python as
the bot process — so the script is skipped and the prompt receives
`[pre_script error: ignored — not permitted at this tier]` in place of its
output. The task still runs; it just doesn't get the script's data.

**Migration:** a workspace-tier schedule that relied on `shell(...)`
pre-approval stops working. Move it to `data/{agent_id}/schedules/` to
restore it — that move is the deliberate act the boundary requires.
```

- [ ] **Step 2: Correct `docs/email.md`**

Line ~127 currently reads that `email-recipients` entries "merge with `config.email.allowed_recipients` only for this task's run — they populate `ctx.tools.preapproved_email_recipients`." That is now conditional. Append to that paragraph:

```markdown
This applies only to schedules at admin or bundled tier. `email-recipients`
on a workspace-tier schedule is ignored for pre-approval, because that file
is agent-writable — see [Schedules](schedules.md#permissions-are-tier-dependent)
and #731.
```

Read the surrounding paragraph first and match its voice; do not restate what it already says.

- [ ] **Step 3: Correct `CLAUDE.md`**

The Config-and-data bullet at line 82 currently reads:

```markdown
- **Email** ([docs/email.md](docs/email.md)) is dual-surface: the `send_email` tool (allowlist-gated, falls through to confirmation; allowlist is a union of config + per-task `email-recipients` frontmatter) and the email notification channel (its `recipient_addresses` config IS the trust boundary).
```

Change the parenthetical so the per-task half carries its tier condition:

```markdown
- **Email** ([docs/email.md](docs/email.md)) is dual-surface: the `send_email` tool (allowlist-gated, falls through to confirmation; allowlist is a union of config + per-task `email-recipients` frontmatter, the latter only at admin/bundled schedule tier — see [docs/schedules.md](docs/schedules.md#permissions-are-tier-dependent)) and the email notification channel (its `recipient_addresses` config IS the trust boundary).
```

One clause only — CLAUDE.md is a conventions file and the detail belongs in `docs/`.

- [ ] **Step 4: Check for other stale claims**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/sec-workspace-preapproval
grep -rn "preapprove\|pre-approv\|bypass confirmation" docs/ CLAUDE.md
```

Judge each hit: does it describe pre-approval as unconditional? Search by concept, not by a guessed phrase — a previous session's stale-claim grep missed a whole section because it searched for wording that didn't exist. Report what you found and what you judged safe to leave.

- [ ] **Step 5: Verify and commit**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/sec-workspace-preapproval
make check && make test && make test-js
git add docs/ CLAUDE.md
git commit -m "docs: schedule permissions are tier-dependent (#731)"
```

---

## Final verification

- [ ] `make check` — ruff, pyright, tsc, message-type drift
- [ ] `make test` — green, count above the current baseline (3657 passed / 2 skipped on `main`)
- [ ] `make test-js` — green
- [ ] `.venv/bin/python -m pytest --durations=25 2>&1 | head -30` — no new test in the top 25
- [ ] Re-read the final diff and confirm the gate is an allowlist, not a denylist
- [ ] Open the PR with `Closes #731`, and state the breaking change plainly: workspace-tier schedules relying on `shell(...)` pre-approval will be denied on unattended turns

## Notes for the implementer

- The security property is the deliverable. If a test passes and you cannot explain *why* it would fail against the unfixed code, treat that as a defect and say so — several tests in the preceding session shipped green while verifying nothing.
- Do not "simplify" the two trust sets into one denylist. `_UNTRUSTED_TIERS` exists so the classification test can detect an unclassified tier; the production check reads `_PREAPPROVAL_TIERS` only, and must stay that way so an unrecognized tier fails closed.
- #735 (unrecognized PATCH keys silently dropped) is a separate issue. Do not fold it in.
