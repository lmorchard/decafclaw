"""Integration tests for project skill tools."""

from types import SimpleNamespace

import pytest

from decafclaw.media import EndTurnConfirm, ToolResult
from decafclaw.skills.project.state import ProjectState, load_project
from decafclaw.skills.project.tools import (
    get_tools,
    tool_project_add_steps,
    tool_project_advance,
    tool_project_create,
    tool_project_list,
    tool_project_next_task,
    tool_project_note,
    tool_project_status,
    tool_project_switch,
    tool_project_task_done,
    tool_project_update_plan,
    tool_project_update_spec,
    tool_project_update_step,
)


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(workspace_path=workspace)
    tools = SimpleNamespace(
        preapproved={"project_next_task", "project_advance"},
        current_call_id=None,
    )
    skills = SimpleNamespace(data={})
    return SimpleNamespace(config=config, tools=tools, skills=skills)


def _approve(result):
    """Simulate EndTurnConfirm approval by calling on_approve callback."""
    if isinstance(result, ToolResult) and isinstance(result.end_turn, EndTurnConfirm):
        if result.end_turn.on_approve:
            result.end_turn.on_approve()
        return True
    return False


def _deny(result):
    """Simulate EndTurnConfirm denial by calling on_deny callback."""
    if isinstance(result, ToolResult) and isinstance(result.end_turn, EndTurnConfirm):
        if result.end_turn.on_deny:
            result.end_turn.on_deny()
        return True
    return False


def _text(result):
    """Extract text from str or ToolResult."""
    if isinstance(result, ToolResult):
        return result.text
    return result


SAMPLE_PLAN = """\
# Plan: Test

## Steps

- [ ] 1. First step
  - [ ] 1.1. Sub-step A
  - [ ] 1.2. Sub-step B
- [ ] 2. Second step
- [ ] 3. Third step
"""


async def _advance_to_planning(ctx, slug="test"):
    """Helper: create project, write spec, approve → planning."""
    await tool_project_create(ctx, "Test", slug=slug)
    result = await tool_project_update_spec(ctx, spec_text="# Spec\nBuild a thing.")
    _approve(result)  # spec_review → planning


async def _advance_to_executing(ctx, slug="test", plan=None):
    """Helper: advance to executing state."""
    await _advance_to_planning(ctx, slug=slug)
    result = await tool_project_update_plan(ctx, plan_text=plan or SAMPLE_PLAN)
    _approve(result)  # plan_review → executing


class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_normal_lifecycle(self, ctx):
        # Create
        result = await tool_project_create(ctx, "Build a widget", slug="widget")
        assert "widget" in result

        # Brainstorming
        result = await tool_project_next_task(ctx)
        assert "BRAINSTORMING" in result
        assert "question" in result.lower()

        # Write spec → SPEC_REVIEW + EndTurnConfirm
        result = await tool_project_update_spec(ctx, spec_text="# Spec\nBuild a widget.")
        assert "Spec updated" in _text(result)
        assert isinstance(result, ToolResult)
        assert isinstance(result.end_turn, EndTurnConfirm)
        info = load_project(ctx.config, "widget")
        assert info.status == ProjectState.SPEC_REVIEW

        # Approve → PLANNING
        _approve(result)
        info = load_project(ctx.config, "widget")
        assert info.status == ProjectState.PLANNING

        # Write plan → PLAN_REVIEW + EndTurnConfirm
        result = await tool_project_update_plan(ctx, plan_text=SAMPLE_PLAN)
        assert "4 steps" in _text(result)
        assert isinstance(result, ToolResult)
        assert isinstance(result.end_turn, EndTurnConfirm)

        # Approve → EXECUTING
        _approve(result)
        info = load_project(ctx.config, "widget")
        assert info.status == ProjectState.EXECUTING

        # Execute all steps
        await tool_project_update_step(ctx, step="1.1", status="done", note="Done A")
        await tool_project_update_step(ctx, step="1.2", status="done", note="Done B")
        await tool_project_update_step(ctx, step="1", status="done")
        await tool_project_update_step(ctx, step="2", status="done")
        await tool_project_update_step(ctx, step="3", status="done")

        # task_done → done
        result = await tool_project_task_done(ctx)
        assert isinstance(result, ToolResult)
        assert "complete" in result.text.lower()
        assert result.end_turn is True

        info = load_project(ctx.config, "widget")
        assert info.status == ProjectState.DONE


class TestEndTurnSignals:
    @pytest.mark.asyncio
    async def test_update_spec_returns_end_turn_confirm(self, ctx):
        """Spec update triggers review via EndTurnConfirm."""
        await tool_project_create(ctx, "Test", slug="et-spec")
        result = await tool_project_update_spec(ctx, spec_text="# Spec")
        assert isinstance(result, ToolResult)
        assert isinstance(result.end_turn, EndTurnConfirm)
        assert "Approve" in result.end_turn.approve_label

    @pytest.mark.asyncio
    async def test_update_plan_returns_end_turn_confirm(self, ctx):
        """Plan update triggers review via EndTurnConfirm."""
        await _advance_to_planning(ctx, slug="et-plan")
        result = await tool_project_update_plan(ctx, plan_text=SAMPLE_PLAN)
        assert isinstance(result, ToolResult)
        assert isinstance(result.end_turn, EndTurnConfirm)
        assert "Approve" in result.end_turn.approve_label

    @pytest.mark.asyncio
    async def test_task_done_returns_end_turn_confirm_for_spec(self, ctx):
        """task_done from brainstorming returns EndTurnConfirm for spec review."""
        await tool_project_create(ctx, "Test", slug="et-confirm")
        await tool_project_update_spec(ctx, spec_text="# Spec")
        result = await tool_project_task_done(ctx)
        assert isinstance(result, ToolResult)
        assert isinstance(result.end_turn, EndTurnConfirm)
        assert "Approve" in result.end_turn.approve_label

    @pytest.mark.asyncio
    async def test_task_done_denial_reverts_state(self, ctx):
        """Denying spec review reverts to brainstorming."""
        await tool_project_create(ctx, "Test", slug="et-deny")
        await tool_project_update_spec(ctx, spec_text="# Spec")
        result = await tool_project_task_done(ctx)
        _deny(result)
        info = load_project(ctx.config, "et-deny")
        assert info.status == ProjectState.BRAINSTORMING

    @pytest.mark.asyncio
    async def test_task_done_approval_advances_state(self, ctx):
        """Approving spec review advances to planning."""
        await tool_project_create(ctx, "Test", slug="et-approve")
        await tool_project_update_spec(ctx, spec_text="# Spec")
        result = await tool_project_task_done(ctx)
        _approve(result)
        info = load_project(ctx.config, "et-approve")
        assert info.status == ProjectState.PLANNING

    @pytest.mark.asyncio
    async def test_task_done_ends_turn_on_completion(self, ctx):
        """task_done from executing → done should end the turn."""
        plan = "- [ ] 1. Only step\n"
        await _advance_to_executing(ctx, slug="et-done", plan=plan)
        await tool_project_update_step(ctx, step="1", status="done")
        result = await tool_project_task_done(ctx)
        assert isinstance(result, ToolResult)
        assert result.end_turn is True
        assert "complete" in result.text.lower()

    @pytest.mark.asyncio
    async def test_update_step_does_not_end_turn(self, ctx):
        """Execution steps should NOT end the turn — the model chains freely."""
        await _advance_to_executing(ctx, slug="et-step")
        result = await tool_project_update_step(ctx, step="1.1", status="done", note="Done")
        # Returns a bare string, not a ToolResult with end_turn
        assert not isinstance(result, ToolResult) or not result.end_turn


class TestNextTask:
    @pytest.mark.asyncio
    async def test_brainstorming_returns_interview_instruction(self, ctx):
        await tool_project_create(ctx, "Test", slug="test-next")
        result = await tool_project_next_task(ctx)
        assert "BRAINSTORMING" in result
        assert "question" in result.lower()

    @pytest.mark.asyncio
    async def test_executing_returns_next_step(self, ctx):
        await _advance_to_executing(ctx, slug="test-exec")
        result = await tool_project_next_task(ctx)
        assert "step" in result.lower()
        assert "1" in result

    @pytest.mark.asyncio
    async def test_done_returns_complete(self, ctx):
        plan = "- [ ] 1. Only step\n"
        await _advance_to_executing(ctx, slug="test-done", plan=plan)
        await tool_project_update_step(ctx, step="1", status="done")
        result = await tool_project_task_done(ctx)
        assert "complete" in _text(result).lower()


class TestBackwardTransitions:
    @pytest.mark.asyncio
    async def test_executing_to_planning(self, ctx):
        await _advance_to_executing(ctx, slug="back-test")
        result = await tool_project_advance(ctx, target_status="planning")
        assert "planning" in result

    @pytest.mark.asyncio
    async def test_executing_to_brainstorming(self, ctx):
        await _advance_to_executing(ctx, slug="rethink")
        result = await tool_project_advance(ctx, target_status="brainstorming")
        assert "brainstorming" in result


class TestStateValidation:
    @pytest.mark.asyncio
    async def test_cannot_update_spec_during_execution(self, ctx):
        await _advance_to_executing(ctx, slug="state-test")
        result = await tool_project_update_spec(ctx, spec_text="# Nope")
        assert "error" in result.text

    @pytest.mark.asyncio
    async def test_cannot_update_plan_during_brainstorming(self, ctx):
        await tool_project_create(ctx, "Test", slug="plan-early")
        result = await tool_project_update_plan(ctx, plan_text=SAMPLE_PLAN)
        assert "error" in result.text


class TestProjectManagement:
    @pytest.mark.asyncio
    async def test_list_and_status(self, ctx):
        await tool_project_create(ctx, "First project", slug="first")
        await tool_project_create(ctx, "Second project", slug="second")

        result = await tool_project_list(ctx)
        assert "first" in result
        assert "second" in result

        result = await tool_project_status(ctx)
        assert "Second project" in result

    @pytest.mark.asyncio
    async def test_switch(self, ctx):
        await tool_project_create(ctx, "Project A", slug="proj-a")
        await tool_project_create(ctx, "Project B", slug="proj-b")
        result = await tool_project_switch(ctx, project="proj-a")
        assert "proj-a" in result

    @pytest.mark.asyncio
    async def test_add_steps(self, ctx):
        await _advance_to_executing(ctx, slug="add-steps")
        result = await tool_project_add_steps(
            ctx, after_step="2", steps=["New step A", "New step B"]
        )
        assert "Added 2 step(s)" in result

    @pytest.mark.asyncio
    async def test_note(self, ctx):
        await tool_project_create(ctx, "Test", slug="note-test")
        result = await tool_project_note(ctx, note_text="Found something.")
        assert "Note added" in result


class TestZeroStepWarning:
    @pytest.mark.asyncio
    async def test_plan_with_no_steps_returns_error(self, ctx):
        await _advance_to_planning(ctx, slug="no-steps")
        result = await tool_project_update_plan(
            ctx, plan_text="# Plan\n\nJust text, no steps."
        )
        assert "error" in result.text

    @pytest.mark.asyncio
    async def test_plan_with_unnumbered_steps_works(self, ctx):
        await _advance_to_planning(ctx, slug="unnumbered")
        result = await tool_project_update_plan(
            ctx, plan_text="# Plan\n\n## Steps\n\n- [ ] Do thing one\n- [ ] Do thing two\n",
        )
        assert "2 steps" in _text(result)


class TestSlugTruncation:
    @pytest.mark.asyncio
    async def test_long_description_gets_truncated(self, ctx):
        await tool_project_create(
            ctx,
            "Write a blog post about what we've gotten done on Decafclaw in the last week",
        )
        info = load_project(ctx.config, "write-a-blog-post-about-what")
        assert info is not None
        assert len(info.slug) <= 30


class TestGetTools:
    """Test dynamic tool loading via get_tools(ctx)."""

    @pytest.mark.asyncio
    async def test_no_project_returns_create_list_switch(self, ctx):
        tools, defs = get_tools(ctx)
        names = set(tools.keys())
        assert "project_create" in names
        assert "project_list" in names
        assert "project_switch" in names
        assert "project_update_spec" not in names
        assert "project_update_step" not in names

    @pytest.mark.asyncio
    async def test_brainstorming_includes_spec_tools(self, ctx):
        await tool_project_create(ctx, "Test", slug="gt-brainstorm")
        tools, defs = get_tools(ctx)
        names = set(tools.keys())
        assert "project_update_spec" in names
        assert "project_next_task" in names
        assert "project_task_done" in names
        # Should NOT include execution tools
        assert "project_update_step" not in names
        assert "project_update_plan" not in names
        assert "project_add_steps" not in names

    @pytest.mark.asyncio
    async def test_planning_includes_plan_tools(self, ctx):
        await _advance_to_planning(ctx, slug="gt-plan")
        tools, defs = get_tools(ctx)
        names = set(tools.keys())
        assert "project_update_plan" in names
        assert "project_next_task" in names
        # Should NOT include spec or execution tools
        assert "project_update_spec" not in names
        assert "project_update_step" not in names

    @pytest.mark.asyncio
    async def test_executing_includes_step_tools(self, ctx):
        await _advance_to_executing(ctx, slug="gt-exec")
        tools, defs = get_tools(ctx)
        names = set(tools.keys())
        assert "project_update_step" in names
        assert "project_add_steps" in names
        assert "project_advance" in names
        # Should NOT include spec/plan writing tools
        assert "project_update_spec" not in names
        assert "project_update_plan" not in names

    @pytest.mark.asyncio
    async def test_done_includes_status_tools(self, ctx):
        plan = "- [ ] 1. Only step\n"
        await _advance_to_executing(ctx, slug="gt-done", plan=plan)
        await tool_project_update_step(ctx, step="1", status="done")
        await tool_project_task_done(ctx)

        tools, defs = get_tools(ctx)
        names = set(tools.keys())
        assert "project_status" in names
        assert "project_list" in names
        assert "project_update_step" not in names
        assert "project_task_done" not in names

    @pytest.mark.asyncio
    async def test_defs_match_tools(self, ctx):
        """Tool definitions should correspond to tool functions."""
        await tool_project_create(ctx, "Test", slug="gt-match")
        tools, defs = get_tools(ctx)
        def_names = {d["function"]["name"] for d in defs}
        assert def_names == set(tools.keys())
