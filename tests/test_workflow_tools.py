"""Tests for workflow engine tools."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from decafclaw.media import EndTurnConfirm, ToolResult
from decafclaw.tools.workflow_tools import (
    build_phase_advance_definition,
    tool_phase_advance,
    tool_workflow_artifact_read,
    tool_workflow_artifact_write,
    tool_workflow_list,
    tool_workflow_start,
    tool_workflow_status,
    tool_workflow_switch,
)
from decafclaw.workflow import registry
from decafclaw.workflow.types import (
    EdgeDef,
    GateDef,
    PhaseDef,
    PhaseKind,
    WorkflowDef,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


def _ctx_for(tmp_path: Path) -> SimpleNamespace:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(workspace_path=workspace)
    skills = SimpleNamespace(data={})
    return SimpleNamespace(config=config, skills=skills)


def _two_phase_wf() -> WorkflowDef:
    return WorkflowDef(
        name="demo", description="d", initial_phase="a",
        phases={
            "a": PhaseDef(
                id="a", kind=PhaseKind.INLINE, prompt="A",
                tools=[],
                next_phases=[
                    EdgeDef(id="b", when="when ready", gate=None),
                    EdgeDef(id="c", when="when stuck", gate=None),
                ],
                gate=None, outputs=(), subagent_skill=None,
                context_profile={},
            ),
            "b": PhaseDef(
                id="b", kind=PhaseKind.INLINE, prompt="",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
            "c": PhaseDef(
                id="c", kind=PhaseKind.INLINE, prompt="",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=True, argument_hint="",
    )


@pytest.mark.asyncio
async def test_workflow_start_creates_run(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    result = await tool_workflow_start(ctx, name="demo", slug="t1")
    assert isinstance(result, (str, ToolResult))
    text = result.text if isinstance(result, ToolResult) else result
    assert "demo" in text
    assert ctx.skills.data["current_workflow_run"]


@pytest.mark.asyncio
async def test_workflow_start_unknown_workflow(tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    result = await tool_workflow_start(ctx, name="ghost", slug="")
    assert isinstance(result, ToolResult)
    assert "not found" in result.text.lower()


@pytest.mark.asyncio
async def test_phase_advance_unknown_target_errors(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="t1")
    result = await tool_phase_advance(ctx, target_phase_id="ghost",
                                       reason="")
    assert isinstance(result, ToolResult)
    assert "not a valid next phase" in result.text


@pytest.mark.asyncio
async def test_phase_advance_valid_target(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="t1")
    result = await tool_phase_advance(ctx, target_phase_id="b",
                                       reason="ready")
    assert isinstance(result, ToolResult)
    assert "Advanced" in result.text or "b" in result.text


@pytest.mark.asyncio
async def test_phase_advance_dynamic_enum_reflects_current_phase(
        tmp_path: Path):
    """The phase_advance schema enum lists only the current phase's targets."""
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="t1")
    definition = build_phase_advance_definition(ctx)
    assert definition is not None
    enum_vals = definition["function"]["parameters"]["properties"][
        "target_phase_id"]["enum"]
    assert set(enum_vals) == {"b", "c"}
    desc = definition["function"]["description"]
    assert "when ready" in desc
    assert "when stuck" in desc


@pytest.mark.asyncio
async def test_phase_advance_definition_none_when_no_run_active(
        tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    assert build_phase_advance_definition(ctx) is None


@pytest.mark.asyncio
async def test_workflow_artifact_write_and_read(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="t1")
    await tool_workflow_artifact_write(
        ctx, relative_path="notes.txt", content="hello")
    result = await tool_workflow_artifact_read(
        ctx, relative_path="notes.txt")
    text = result.text if isinstance(result, ToolResult) else result
    assert "hello" in text


@pytest.mark.asyncio
async def test_workflow_artifact_write_rejects_path_traversal(
        tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="t1")
    result = await tool_workflow_artifact_write(
        ctx, relative_path="../../escape.txt", content="hi")
    assert isinstance(result, ToolResult)
    assert "outside" in result.text.lower() or "invalid" in result.text.lower()


@pytest.mark.asyncio
async def test_workflow_status_shows_valid_targets(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="t1")
    result = await tool_workflow_status(ctx)
    text = result.text if isinstance(result, ToolResult) else result
    assert "when ready" in text
    assert "when stuck" in text


@pytest.mark.asyncio
async def test_workflow_list_and_switch(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="one")
    first = ctx.skills.data["current_workflow_run"]
    await tool_workflow_start(ctx, name="demo", slug="two")
    second = ctx.skills.data["current_workflow_run"]
    assert first != second

    listing = await tool_workflow_list(ctx, workflow="", status="")
    text = listing.text if isinstance(listing, ToolResult) else listing
    assert "one" in text
    assert "two" in text

    await tool_workflow_switch(ctx, run_id=first)
    assert ctx.skills.data["current_workflow_run"] == first


@pytest.mark.asyncio
async def test_refresh_workflow_tools_injects_phase_advance(tmp_path: Path):
    """After workflow_start, refresh_workflow_tools should add
    phase_advance to ctx.tools.extra_definitions with the right enum."""
    from decafclaw.tools.workflow_tools import refresh_workflow_tools

    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    ctx.tools = SimpleNamespace(extra={}, extra_definitions=[], allowed=None)

    refresh_workflow_tools(ctx)
    assert "phase_advance" not in ctx.tools.extra

    await tool_workflow_start(ctx, name="demo", slug="t1")
    refresh_workflow_tools(ctx)
    assert "phase_advance" in ctx.tools.extra
    defs = [d for d in ctx.tools.extra_definitions
            if d["function"]["name"] == "phase_advance"]
    assert len(defs) == 1
    enum_vals = defs[0]["function"]["parameters"]["properties"][
        "target_phase_id"]["enum"]
    assert set(enum_vals) == {"b", "c"}


# ---------------------------------------------------------------
# Bug-fix tests for production demo failure mode (see PR #557 review)
# ---------------------------------------------------------------


def _subagent_initial_wf() -> WorkflowDef:
    """Workflow whose initial phase is a subagent — exercises the
    'workflow_start lands on subagent' path."""
    return WorkflowDef(
        name="sub_init", description="", initial_phase="gather",
        phases={
            "gather": PhaseDef(
                id="gather", kind=PhaseKind.SUBAGENT,
                prompt="research", tools=["vault_read"],
                next_phases=[EdgeDef(id="draft", when="", gate=None)],
                gate=None, outputs=("sources.md",),
                subagent_skill=None, context_profile={},
            ),
            "draft": PhaseDef(
                id="draft", kind=PhaseKind.INLINE, prompt="draft body",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=False, argument_hint="",
    )


def _mid_subagent_wf() -> WorkflowDef:
    """Workflow with a subagent phase in the middle — exercises
    'phase_advance lands on subagent' path."""
    return WorkflowDef(
        name="mid_sub", description="", initial_phase="a",
        phases={
            "a": PhaseDef(
                id="a", kind=PhaseKind.INLINE, prompt="A",
                tools=[],
                next_phases=[EdgeDef(id="b", when="", gate=None)],
                gate=None, outputs=(), subagent_skill=None,
                context_profile={},
            ),
            "b": PhaseDef(
                id="b", kind=PhaseKind.SUBAGENT, prompt="B subagent",
                tools=["vault_read"],
                next_phases=[EdgeDef(id="c", when="", gate=None)],
                gate=None, outputs=("out.md",), subagent_skill=None,
                context_profile={},
            ),
            "c": PhaseDef(
                id="c", kind=PhaseKind.INLINE, prompt="C",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=False, argument_hint="",
    )


@pytest.mark.asyncio
async def test_workflow_start_dispatches_subagent_initial_phase(
        tmp_path: Path, monkeypatch):
    """Bug fix: if a workflow's initial phase is a subagent,
    workflow_start should synchronously dispatch the subagent and
    return with the run already advanced past it."""
    from decafclaw.workflow import subagent as wf_subagent
    from decafclaw.workflow.runs import load_run
    from decafclaw.workflow.types import RunStatus

    registry.register(_subagent_initial_wf())

    async def fake_run_child(*, ctx, workspace, state, phase):
        artifacts = (workspace / "workflows" / state.workflow
                     / "runs" / state.run_id / "artifacts" / phase.id)
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "sources.md").write_text("fetched")
        return "done"

    monkeypatch.setattr(wf_subagent, "_run_child", fake_run_child)

    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="sub_init", slug="t1")

    run_id = ctx.skills.data["current_workflow_run"]
    state = load_run(ctx.config.workspace_path, run_id)
    assert state is not None
    # The subagent should have run, and the state should now be on
    # the inline phase past it (draft is terminal → DONE).
    assert state.current_phase == "draft", (
        f"workflow_start did not dispatch subagent — still in "
        f"phase {state.current_phase!r}")
    assert state.status == RunStatus.DONE


@pytest.mark.asyncio
async def test_phase_advance_dispatches_subagent_target(
        tmp_path: Path, monkeypatch):
    """Bug fix: if phase_advance lands on a subagent phase, the
    subagent should be dispatched synchronously and the run should
    advance past it before the tool returns."""
    from decafclaw.workflow import subagent as wf_subagent
    from decafclaw.workflow.runs import load_run
    from decafclaw.workflow.types import RunStatus

    registry.register(_mid_subagent_wf())

    async def fake_run_child(*, ctx, workspace, state, phase):
        artifacts = (workspace / "workflows" / state.workflow
                     / "runs" / state.run_id / "artifacts" / phase.id)
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "out.md").write_text("done")
        return "ok"

    monkeypatch.setattr(wf_subagent, "_run_child", fake_run_child)

    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="mid_sub", slug="t1")
    await tool_phase_advance(ctx, target_phase_id="b", reason="go")

    state = load_run(ctx.config.workspace_path,
                     ctx.skills.data["current_workflow_run"])
    assert state is not None
    assert state.current_phase == "c", (
        f"phase_advance did not dispatch subagent — still in "
        f"phase {state.current_phase!r}")
    assert state.status == RunStatus.DONE


@pytest.mark.asyncio
async def test_refresh_workflow_tools_restricts_catalog_per_phase(
        tmp_path: Path):
    """Bug fix: when a workflow is active in an inline phase,
    refresh_workflow_tools should restrict ctx.tools.allowed to the
    phase's tool whitelist plus an always-on baseline (workflow
    admin + critical-priority tools)."""
    from decafclaw.tools.workflow_tools import refresh_workflow_tools

    # Build a workflow whose 'a' phase has a specific tool whitelist
    wf = WorkflowDef(
        name="restricted", description="", initial_phase="a",
        phases={
            "a": PhaseDef(
                id="a", kind=PhaseKind.INLINE, prompt="A",
                tools=["vault_read", "vault_write"],
                next_phases=[EdgeDef(id="b", when="", gate=None)],
                gate=None, outputs=(), subagent_skill=None,
                context_profile={},
            ),
            "b": PhaseDef(
                id="b", kind=PhaseKind.INLINE, prompt="B",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=False, argument_hint="",
    )
    registry.register(wf)

    ctx = _ctx_for(tmp_path)
    ctx.tools = SimpleNamespace(extra={}, extra_definitions=[], allowed=None)

    # Before workflow starts, allowed should remain None (unrestricted)
    refresh_workflow_tools(ctx)
    assert ctx.tools.allowed is None

    await tool_workflow_start(ctx, name="restricted", slug="t1")
    refresh_workflow_tools(ctx)

    # Now allowed should be a restricted set, including phase tools +
    # workflow admin + phase_advance + critical-priority infra (notes_*,
    # checklist_*)
    assert ctx.tools.allowed is not None, (
        "refresh_workflow_tools should restrict ctx.tools.allowed "
        "when a workflow is in an inline phase")
    allowed = ctx.tools.allowed
    # Phase whitelist must be honored
    assert "vault_read" in allowed
    assert "vault_write" in allowed
    # Workflow admin tools must always be available
    assert "workflow_status" in allowed
    assert "workflow_artifact_read" in allowed
    assert "workflow_artifact_write" in allowed
    # Dynamic phase_advance must be allowed
    assert "phase_advance" in allowed
    # Tools NOT in the phase whitelist and NOT in the always-on baseline
    # should be excluded — e.g. tabstack_research isn't in the whitelist
    assert "tabstack_research" not in allowed


@pytest.mark.asyncio
async def test_refresh_workflow_tools_clears_allowed_on_no_run(
        tmp_path: Path):
    """Bug fix complement: when no workflow is active and the
    restriction was set by a previous workflow run (workflow_restricted
    is True), refresh should clear it."""
    from decafclaw.tools.workflow_tools import refresh_workflow_tools

    ctx = _ctx_for(tmp_path)
    ctx.tools = SimpleNamespace(
        extra={}, extra_definitions=[],
        allowed={"vault_read"},          # stale from previous workflow
        workflow_restricted=True,
    )
    refresh_workflow_tools(ctx)
    assert ctx.tools.allowed is None
    assert ctx.tools.workflow_restricted is False


@pytest.mark.asyncio
async def test_refresh_workflow_tools_preserves_unrelated_restriction(
        tmp_path: Path):
    """If ctx.tools.allowed is set by code outside the workflow engine
    (workflow_restricted=False), refresh must not clear it."""
    from decafclaw.tools.workflow_tools import refresh_workflow_tools

    ctx = _ctx_for(tmp_path)
    ctx.tools = SimpleNamespace(
        extra={}, extra_definitions=[],
        allowed={"vault_read"},          # set by something else
        workflow_restricted=False,
    )
    refresh_workflow_tools(ctx)
    assert ctx.tools.allowed == {"vault_read"}
    assert ctx.tools.workflow_restricted is False
