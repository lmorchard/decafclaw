# Workflow skill activation Implementation Plan

**Goal:** Make skill-bundled tools reachable from `wf.tool_call` inside workflows by auto-activating the always-loaded skill set on workflow-turn start plus honoring a new `@workflow(..., requires_skills=())` declaration. Land the `/research` workflow exercising `tabstack_research` end-to-end.

**Approach:** Foundation refactor (extract the existing USER-turn always-loaded activation loop into a shared helper) before any new behavior. Then add `requires_skills` to the workflow registry, wire the activation into `run_workflow_turn`, update `/research`, and smoke. TDD per phase with one explicit opt-out (Phase 1, pure refactor — existing USER-turn tests are the regression guard).

**Tech stack:** Python 3.12, asyncio. New code in `src/decafclaw/skills/__init__.py`, `src/decafclaw/workflow/{registry,resume,errors}.py`. Tests in `tests/test_skills.py`, `tests/test_workflow_registry.py`, `tests/test_workflow_resume.py`, `tests/test_workflow_research.py`.

---

## Phase 1: Extract `activate_always_loaded(ctx)` helper

**Pure refactor — TDD opt-out.** Moves the existing always-loaded activation loop from `_setup_turn_state` (`src/decafclaw/agent.py:395-411`) into a shared helper. No behavior change for USER turns. The existing USER-turn tests are the regression guard.

**Files:**
- Modify: `src/decafclaw/skills/__init__.py` — add `async def activate_always_loaded(ctx) -> None`.
- Modify: `src/decafclaw/agent.py` — `_setup_turn_state` calls the helper instead of inlining the loop.
- Test: `tests/test_skills.py` — add `test_activate_always_loaded_*` unit tests for the helper.

**Key changes:**

```python
# src/decafclaw/skills/__init__.py

async def activate_always_loaded(ctx) -> None:
    """Activate every always-loaded discovered skill against `ctx`.

    Fail-soft per skill — a failed activation logs but does not block
    the rest. Matches the existing USER-turn behavior verbatim. Skips
    workspace-tier skills (defense in depth; workspace skills can't
    self-mark always-loaded at discovery but the guard preserves the
    invariant). Skips already-activated skills (idempotent).
    """
    from .tools.skill_tools import activate_skill_internal  # break cycle

    for skill_info in ctx.config.discovered_skills:
        if not skill_info.always_loaded or skill_info.name in ctx.skills.activated:
            continue
        if skill_info.trust_tier == "workspace":
            continue
        try:
            await activate_skill_internal(ctx, skill_info)
            log.debug("Auto-activated always-loaded skill %r", skill_info.name)
        except Exception as exc:  # noqa: BLE001 — fail-soft per spec
            log.error("Failed to auto-activate skill %r: %s",
                      skill_info.name, exc)
```

In `src/decafclaw/agent.py`, replace the `for skill_info in discovered:` block at lines 395-411 (and its local `from .tools.skill_tools import activate_skill_internal`) with a single `await activate_always_loaded(ctx)`. Add `from .skills import activate_always_loaded` at module level.

**Tests (new in `tests/test_skills.py`):**

```python
async def test_activate_always_loaded_runs_each_skill_once(...):
    # Build a ctx with two always-loaded skills; call helper; assert
    # both names in ctx.skills.activated after the call.

async def test_activate_always_loaded_skips_workspace_tier(...):
    # Workspace-tier always-loaded skill is not activated.

async def test_activate_always_loaded_skips_already_activated(...):
    # Pre-mark one skill as activated; helper does not re-activate
    # (patch activate_skill_internal and assert it's not called for that
    # name).

async def test_activate_always_loaded_fails_soft(...):
    # Two always-loaded skills; activate_skill_internal raises for the
    # first, succeeds for the second. Assert: no exception bubbles out;
    # second skill still activated; log.error fires once.
```

**Verification — automated:**
- [ ] `cd .claude/worktrees/feat-580-workflow-skill-activation && make lint`
- [ ] `make check`
- [ ] `make test` (baseline 2914 + 4 new = 2918; USER-turn integration tests still green)
- [ ] `uv run pytest tests/test_skills.py -v -k always_loaded`

**Verification — manual:**
- [ ] `grep -n 'activate_skill_internal' src/decafclaw/agent.py` — the inline-loop usage is gone. Only the helper and `tool_activate_skill` still reference `activate_skill_internal`.

---

## Phase 2: Add `activate_skills_for_workflow(ctx, names)` helper

Fail-loud sibling to Phase 1's helper. Used by workflow turns to activate explicitly-declared skills. Raises `WorkflowSkillActivationFailed` on any failure (unknown skill name or activation exception). Does NOT skip workspace-tier skills — the workflow author opted in by name.

**Files:**
- Modify: `src/decafclaw/workflow/errors.py` — add `WorkflowSkillActivationFailed` extending `WorkflowError`.
- Modify: `src/decafclaw/skills/__init__.py` — add `async def activate_skills_for_workflow(ctx, names) -> None`.
- Test: `tests/test_skills.py` — add `test_activate_skills_for_workflow_*` tests.

**Key changes:**

```python
# src/decafclaw/workflow/errors.py
# (Append below the existing exception classes.)

class WorkflowSkillActivationFailed(WorkflowError):
    """Raised when an explicitly-requested skill (from
    @workflow(requires_skills=...)) fails to activate at workflow-turn
    start. Surfaces before run_workflow runs the orchestrator so the
    workflow author hears about it loudly.
    """
    pass
```

```python
# src/decafclaw/skills/__init__.py
# (Add alongside activate_always_loaded.)

from collections.abc import Sequence

async def activate_skills_for_workflow(
    ctx, names: Sequence[str],
) -> None:
    """Activate each named skill against `ctx`. Fail-loud:
    - Unknown name → WorkflowSkillActivationFailed.
    - Skill's init() raises → WorkflowSkillActivationFailed (wraps the
      underlying exception via `from exc`).
    Idempotent: skills already in ctx.skills.activated are skipped.
    Workspace-tier skills ARE permitted here — the workflow author
    opted in explicitly by name."""
    from .tools.skill_tools import activate_skill_internal  # break cycle
    from decafclaw.workflow.errors import WorkflowSkillActivationFailed

    by_name = {s.name: s for s in ctx.config.discovered_skills}
    for name in names:
        if name in ctx.skills.activated:
            continue
        skill_info = by_name.get(name)
        if skill_info is None:
            raise WorkflowSkillActivationFailed(
                f"requires_skills entry {name!r} is not a discovered skill")
        try:
            await activate_skill_internal(ctx, skill_info)
            log.debug("Workflow-activated skill %r", name)
        except Exception as exc:
            raise WorkflowSkillActivationFailed(
                f"Skill {name!r} failed to activate: {exc}") from exc
```

**Tests (new in `tests/test_skills.py`):**

```python
async def test_activate_skills_for_workflow_succeeds(...):
    # Two declared skills, both discoverable, both activate. Both
    # names land in ctx.skills.activated.

async def test_activate_skills_for_workflow_unknown_skill_raises(...):
    # names=["bogus-skill"]; raises WorkflowSkillActivationFailed with
    # message containing "bogus-skill".

async def test_activate_skills_for_workflow_init_failure_raises(...):
    # Skill's init() raises RuntimeError("boom"); helper raises
    # WorkflowSkillActivationFailed; __cause__ is the RuntimeError.

async def test_activate_skills_for_workflow_idempotent(...):
    # Pre-mark the skill as activated; helper does NOT call
    # activate_skill_internal (patch it with a sabotage stub).

async def test_activate_skills_for_workflow_permits_workspace_tier(...):
    # A workspace-tier skill declared by name DOES activate (unlike
    # the always-loaded helper, which forbids workspace).
```

**Verification — automated:**
- [ ] `make lint`
- [ ] `make check`
- [ ] `make test` (2918 + 5 = 2923)
- [ ] `uv run pytest tests/test_skills.py -v -k for_workflow`

**Verification — manual:**
- [ ] Error message for unknown skill mentions the name: `"requires_skills entry 'bogus-skill' is not a discovered skill"`.
- [ ] `WorkflowSkillActivationFailed.__cause__` is set to the underlying exception (verifiable in the init-failure test by asserting `exc_info.value.__cause__ is the_runtime_error`).

---

## Phase 3: `WorkflowSpec.requires_skills` field + decorator update

Add the declaration surface to the workflow registry. No activation wiring yet — that's Phase 4.

**Files:**
- Modify: `src/decafclaw/workflow/registry.py` — add `requires_skills: tuple[str, ...] = ()` to `WorkflowSpec`; extend `workflow(...)` decorator signature.
- Test: `tests/test_workflow_registry.py` — add tests for the new field.

**Key changes:**

```python
# src/decafclaw/workflow/registry.py

@dataclasses.dataclass
class WorkflowSpec:
    name: str
    fn: Callable[[Any], Awaitable[Any]]
    model: str = "vertex-gemini-flash"
    requires_skills: tuple[str, ...] = ()


def workflow(
    name: str,
    *,
    model: str = "vertex-gemini-flash",
    requires_skills: tuple[str, ...] | list[str] = (),
):
    def deco(fn):
        if name in REGISTRY:
            raise ValueError(f"workflow {name!r} already registered")
        REGISTRY[name] = WorkflowSpec(
            name=name, fn=fn, model=model,
            requires_skills=tuple(requires_skills),
        )
        return fn
    return deco
```

`tuple(requires_skills)` normalizes — callers passing a list still end up with a tuple in the spec.

**Tests (new or extended in `tests/test_workflow_registry.py`):**

```python
def test_workflow_decorator_default_requires_skills_empty():
    @workflow("test-default")
    async def f(wf): pass
    try:
        spec = get_workflow("test-default")
        assert spec is not None and spec.requires_skills == ()
    finally:
        REGISTRY.pop("test-default", None)


def test_workflow_decorator_accepts_requires_skills():
    @workflow("test-with-skills", requires_skills=("tabstack",))
    async def f(wf): pass
    try:
        spec = get_workflow("test-with-skills")
        assert spec is not None and spec.requires_skills == ("tabstack",)
    finally:
        REGISTRY.pop("test-with-skills", None)


def test_workflow_decorator_normalizes_list_to_tuple():
    @workflow("test-list", requires_skills=["a", "b"])
    async def f(wf): pass
    try:
        spec = get_workflow("test-list")
        assert spec.requires_skills == ("a", "b")
        assert isinstance(spec.requires_skills, tuple)
    finally:
        REGISTRY.pop("test-list", None)
```

If `tests/test_workflow_registry.py` already has a per-test cleanup fixture, reuse it instead of inlining the `try/finally`.

**Verification — automated:**
- [ ] `make lint`
- [ ] `make check` (pyright checks the dataclass type and the decorator signature)
- [ ] `make test` (2923 + 3 = 2926)
- [ ] `uv run pytest tests/test_workflow_registry.py -v`

**Verification — manual:**
- [ ] Existing workflows (`interview`, `research`) still load and register — module-import-time decoration didn't break (`uv run python -c "import decafclaw.workflow.workflows; from decafclaw.workflow.registry import REGISTRY; print(sorted(REGISTRY))"` shows both names).

---

## Phase 4: `run_workflow_turn` activates before `run_workflow`

Wire both helpers into the workflow turn entry. Activation runs AFTER journal load and BEFORE `run_workflow`. Activation failure surfaces as an error `ToolResult` and marks the journal status as `"error"`.

**Files:**
- Modify: `src/decafclaw/workflow/resume.py` — call `activate_always_loaded` + `activate_skills_for_workflow` inside `run_workflow_turn`; catch `WorkflowSkillActivationFailed` and convert to error `ToolResult`.
- Test: `tests/test_workflow_resume.py` — add activation tests at the `run_workflow_turn` level.

**Key changes:**

```python
# src/decafclaw/workflow/resume.py
# Add at the top with the existing imports:

from decafclaw.skills import (
    activate_always_loaded,
    activate_skills_for_workflow,
)
from .errors import WorkflowSkillActivationFailed
```

Replace the body of `run_workflow_turn` between the existing `journal = Journal(...)` fallback and the `await ctx.publish("tool_status", ...)` call. Insert the activation block:

```python
async def run_workflow_turn(ctx, manager, *,
                            workflow_name: str, resume: bool) -> ToolResult:
    spec = get_workflow(workflow_name)
    if spec is None:
        return ToolResult(text=f"[error: unknown workflow {workflow_name!r}]")

    journal = load_journal(ctx.config, ctx.conv_id)
    if journal is None:
        if resume:
            return ToolResult(text="[error: no workflow journal to resume]")
        journal = Journal(workflow_name=workflow_name)

    # Skill activation: always-loaded for every workflow turn (matching
    # USER-turn behavior); requires_skills as declared by the workflow.
    # Activation failure surfaces before run_workflow runs.
    try:
        await activate_always_loaded(ctx)
        if spec.requires_skills:
            await activate_skills_for_workflow(ctx, spec.requires_skills)
    except WorkflowSkillActivationFailed as exc:
        journal.status = "error"
        save_journal(ctx.config, ctx.conv_id, journal)
        log.error("Workflow %r: skill activation failed: %s",
                  workflow_name, exc)
        return ToolResult(text=f"[error: skill activation failed: {exc}]")

    await ctx.publish("tool_status", tool="workflow",
                      message=f"[workflow: {workflow_name}] running")
    outcome = await run_workflow(ctx, spec.fn, journal, model=spec.model)
    # ... rest of the function (outcome handling) unchanged ...
```

`activate_always_loaded` is fail-soft (Phase 1) — it doesn't raise `WorkflowSkillActivationFailed`. The single `try/except` only catches what `activate_skills_for_workflow` raises.

**Tests (new in `tests/test_workflow_resume.py`):**

```python
async def test_run_workflow_turn_activates_always_loaded_before_orchestrator():
    # Configure ctx.config.discovered_skills with one always-loaded
    # fake skill exposing a tool "fake_tool". Register a workflow whose
    # orchestrator returns 1. Patch run_workflow to capture
    # ctx.tools.extra at call time. Assert "fake_tool" is present in
    # the captured dict.

async def test_run_workflow_turn_activates_requires_skills():
    # Register a workflow with requires_skills=("tabstack-like",).
    # Discovered skills include "tabstack-like" exposing "fake_tool".
    # Patch run_workflow. Assert the declared skill activated and
    # "fake_tool" lands in ctx.tools.extra before the orchestrator.

async def test_run_workflow_turn_returns_error_on_activation_failure():
    # Register a workflow with requires_skills=("missing-skill",).
    # Patch run_workflow with a sabotage mock (raises if called).
    # Call run_workflow_turn(resume=False).
    # Assert: returns ToolResult whose text starts "[error: skill
    # activation failed:" and mentions "missing-skill".
    # Assert: journal status persisted as "error" (load via load_journal).
    # Assert: the sabotage mock was NOT called.

async def test_run_workflow_turn_activation_idempotent_on_resume():
    # Pre-populate ctx.skills.activated = {"foo-skill"}.
    # Register a workflow with requires_skills=("foo-skill",).
    # Patch activate_skill_internal with a sabotage mock.
    # Call run_workflow_turn.
    # Assert: sabotage mock was NOT called, orchestrator ran normally.
```

**Verification — automated:**
- [ ] `make lint`
- [ ] `make check`
- [ ] `make test` (2926 + 4 = 2930)
- [ ] `uv run pytest tests/test_workflow_resume.py -v`

**Verification — manual:**
- [ ] On the happy path, the only log lines from activation are `log.debug` (no `log.error`, no `log.warning`).

---

## Phase 5: `/research` declares `requires_skills=("tabstack",)`

Land the load-bearing unblock — the orchestrator that PR #579's smoke (Finding 1) blocked on.

**Files:**
- Modify: `src/decafclaw/workflow/workflows/research.py` — change the `@workflow` decorator to declare `requires_skills`.
- Modify: `tests/test_workflow_research.py` — add a registry-level assertion.

**Key changes:**

```python
# src/decafclaw/workflow/workflows/research.py
# Replace the existing @workflow line with:

@workflow("research", requires_skills=("tabstack",))
async def research(wf):
    ...
```

The orchestrator body doesn't change — `wf.tool_call("tabstack_research", query=q)` now reaches a real tool.

**Tests (extend `tests/test_workflow_research.py`):**

```python
def test_research_declares_tabstack_requirement():
    """The /research workflow declares the tabstack skill so its
    tool_call invocations of `tabstack_research` reach a real tool."""
    from decafclaw.workflow.workflows.research import _SEARCH_TOOL
    spec = get_workflow("research")
    assert spec is not None
    assert "tabstack" in spec.requires_skills
    # Sanity: the declared skill owns the search tool the orchestrator
    # uses. Guards against a typo splitting the declaration from the
    # tool name.
    assert _SEARCH_TOOL.startswith("tabstack_")
```

Existing `test_research_orchestrator_walks_to_completion` and `test_research_orchestrator_resumes_from_journal` continue to pass — they mock `execute_tool` directly and don't depend on skill activation. The new declaration is orthogonal to those tests' mocks.

`test_research_fails_fast_when_search_tool_returns_all_errors` (added in #579's Copilot fix-up) also continues to pass — the orchestrator's `_is_error_result` check still works regardless of whether the tool reaches a real implementation.

**Verification — automated:**
- [ ] `make lint`
- [ ] `make check`
- [ ] `make test` (2930 + 1 = 2931)
- [ ] `uv run pytest tests/test_workflow_research.py -v`

**Verification — manual:**
- [ ] `grep -n requires_skills src/decafclaw/workflow/workflows/research.py` — declaration is present and matches `("tabstack",)`.

---

## Phase 6: Live smoke + docs update

Walk `/research` end-to-end on `vertex-gemini-flash` with the tabstack skill actually reachable. Document `requires_skills` in `docs/workflows.md`.

**Files:**
- Modify: `docs/workflows.md` — add a "Skill activation" subsection under workflows.
- Create: `docs/dev-sessions/2026-06-12-1435-580-workflow-skill-activation/smoke.md` — capture the smoke transcript.
- Modify: `docs/dev-sessions/2026-06-12-1435-580-workflow-skill-activation/notes.md` — append per-phase notes and retro.

**Key changes (docs/workflows.md additions, inserted near the existing `@workflow` description):**

```markdown
### Skill activation

Workflows can declare additional skills they need at decoration time:

\`\`\`python
@workflow("research", requires_skills=("tabstack",))
async def research(wf):
    ...
    await wf.tool_call("tabstack_research", query=q)
\`\`\`

At workflow-turn start, the engine activates:

1. **Always-loaded skills** (`vault`, `background`, `mcp`) — the same set the agent loop auto-activates. Tools from these are reachable from `wf.tool_call` without explicit declaration.
2. **`requires_skills` entries** — declared per-workflow. Activated against the same code path as the agent loop's `activate_skill` tool. Workspace-tier skills ARE permitted here (unlike always-loaded, where workspace skills can't self-mark).

**Failure mode.** A missing skill name in `requires_skills`, or a skill whose `init()` raises, surfaces as `WorkflowSkillActivationFailed` BEFORE the orchestrator runs. The turn returns an error `ToolResult`; the journal status is marked `"error"`.

**Idempotency.** Activation re-runs on every workflow turn (including post-`user_input` resumes), but the `ctx.skills.activated` set short-circuits already-activated skills — no observable difference.
```

Also remove or update any line in `docs/workflows.md` that still describes the smoke Finding 1 from #574 as an open gap (verify with `grep -n 'skill.*workflow\|tabstack\|Finding 1' docs/workflows.md`).

**Smoke walk:**

1. `cd /Users/lorchard/devel/decafclaw/.claude/worktrees/feat-580-workflow-skill-activation`
2. `nohup uv run decafclaw > /tmp/decafclaw-580-smoke.log 2>&1 &`
3. Wait for `Uvicorn running on http://0.0.0.0:18893`.
4. `export DECAFCLAW_TOKEN=$(jq -r 'keys[0]' /Users/lorchard/devel/decafclaw/data/decafclaw/web_tokens.json) DECAFCLAW_HOST=http://localhost:18893`
5. `uv run decafclaw-client send --prompt "/research kelp forest restoration" --format jsonl`
6. Capture the `confirmation_id` from the suspension; respond with a topic.
7. Capture the next `confirmation_id`; respond with scope.
8. Watch the journal at `data/decafclaw/workspace/conversations/<conv_id>/workflow.json` evolve. Verify: tool_call children at `(3, 0..N, 0)` no longer contain `[error: unknown tool 'tabstack_research']`. Real markdown lands in `result.text`.
9. Pipeline summarize stages run — journal grows with `(4, i, 0)` entries.
10. Subagent synthesis runs; final report dict lands.
11. Capture transcript + journal snapshot in `smoke.md`.

**Verification — automated:**
- [ ] `make lint`
- [ ] `make check`
- [ ] `make test` (2931 — no new tests this phase)

**Verification — manual:**
- [ ] Live `/research` walk completes through to a final report (no fail-fast on error-text input).
- [ ] Inspect `workflow.json` on disk: tool_call result.text contains real tabstack output (not `[error:`).
- [ ] `docs/workflows.md` reads coherently: the `requires_skills` example matches the implementation; #580's gap is no longer mentioned anywhere as "deferred" or "open."
- [ ] `grep -rn 'tabstack.*workflow\|workflow.*tabstack' docs/` — only the new descriptive mentions; no leftover "TODO / not reachable" language.
