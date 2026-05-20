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
    ctx.tools = SimpleNamespace(extra={}, extra_definitions=[])

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
