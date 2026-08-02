"""Integration tests for project skill tools."""

import re
from types import SimpleNamespace

import pytest

from decafclaw.media import EndTurnConfirm, ToolResult
from decafclaw.skills.project.state import (
    TRANSITIONS,
    ProjectInfo,
    ProjectState,
    load_project,
    save_project,
)
from decafclaw.skills.project.tools import (
    _PHASE_TOOLS,
    TOOLS,
    _load_prompt,
    _next_execution_step,
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
    return SimpleNamespace(config=config, tools=tools, skills=skills,
                           conv_id="proj-conv", manager=None)


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


class TestProgressTrackerEmit:
    @pytest.mark.asyncio
    async def test_update_step_emits_during_executing(self, ctx, monkeypatch):
        from unittest.mock import AsyncMock
        set_mock = AsyncMock()
        monkeypatch.setattr("decafclaw.sticky.set_sticky", set_mock)
        monkeypatch.setattr("decafclaw.sticky.clear_sticky", AsyncMock())
        await _advance_to_executing(ctx, slug="pt-step")
        set_mock.reset_mock()
        await tool_project_update_step(ctx, step="1.1", status="done", note="ok")
        assert set_mock.await_count >= 1
        args, _ = set_mock.await_args
        assert args[2] == "progress_tracker"
        labels = [s["label"] for s in args[3]["steps"]]
        assert any(lbl.startswith("1.1.") for lbl in labels)

    @pytest.mark.asyncio
    async def test_done_clears_sticky(self, ctx, monkeypatch):
        from unittest.mock import AsyncMock
        clear_mock = AsyncMock()
        monkeypatch.setattr("decafclaw.sticky.set_sticky", AsyncMock())
        monkeypatch.setattr("decafclaw.sticky.clear_sticky", clear_mock)
        plan = "# Plan\n\n## Steps\n\n- [ ] 1. Only step\n"
        await _advance_to_executing(ctx, slug="pt-done", plan=plan)
        await tool_project_update_step(ctx, step="1", status="done")
        result = await tool_project_task_done(ctx)
        assert _text(result) == "Project complete!" or "complete" in _text(result).lower()
        assert clear_mock.await_count >= 1

    @pytest.mark.asyncio
    async def test_no_emit_outside_executing(self, ctx, monkeypatch):
        from unittest.mock import AsyncMock
        set_mock = AsyncMock()
        monkeypatch.setattr("decafclaw.sticky.set_sticky", set_mock)
        monkeypatch.setattr("decafclaw.sticky.clear_sticky", AsyncMock())
        await tool_project_create(ctx, description="planning phase", slug="pt-plan")
        # BRAINSTORMING phase: next_task must not pin a tracker.
        await tool_project_next_task(ctx)
        set_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_emit_failure_is_fail_open(self, ctx, monkeypatch):
        from unittest.mock import AsyncMock
        monkeypatch.setattr("decafclaw.sticky.set_sticky",
                            AsyncMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr("decafclaw.sticky.clear_sticky",
                            AsyncMock(side_effect=RuntimeError("boom")))
        await _advance_to_executing(ctx, slug="pt-failopen")
        # Must not raise.
        result = await tool_project_update_step(ctx, step="1.1", status="done")
        assert _text(result)

    @pytest.mark.asyncio
    async def test_add_steps_during_planning_does_not_emit(self, ctx, monkeypatch):
        from unittest.mock import AsyncMock
        set_mock = AsyncMock()
        monkeypatch.setattr("decafclaw.sticky.set_sticky", set_mock)
        monkeypatch.setattr("decafclaw.sticky.clear_sticky", AsyncMock())
        await _advance_to_planning(ctx, slug="pt-add-plan")
        await tool_project_update_plan(ctx, plan_text=SAMPLE_PLAN)
        # Now in PLAN_REVIEW, not EXECUTING.
        result = await tool_project_add_steps(ctx, after_step="1", steps=["Extra step"])
        assert "Added" in _text(result)
        set_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_advance_out_of_executing_clears(self, ctx, monkeypatch):
        from unittest.mock import AsyncMock
        clear_mock = AsyncMock()
        monkeypatch.setattr("decafclaw.sticky.set_sticky", AsyncMock())
        monkeypatch.setattr("decafclaw.sticky.clear_sticky", clear_mock)
        await _advance_to_executing(ctx, slug="pt-advance")
        clear_mock.reset_mock()
        result = await tool_project_advance(ctx, target_status="planning")
        assert "reverted" in _text(result).lower()
        assert clear_mock.await_count >= 1

    @pytest.mark.asyncio
    async def test_switch_away_from_executing_clears(self, ctx, monkeypatch):
        """C1: Switching to another project while current is EXECUTING clears its tracker."""
        from unittest.mock import AsyncMock
        clear_mock = AsyncMock()
        monkeypatch.setattr("decafclaw.sticky.set_sticky", AsyncMock())
        monkeypatch.setattr("decafclaw.sticky.clear_sticky", clear_mock)
        # Create and advance project A to EXECUTING
        await _advance_to_executing(ctx, slug="pt-switch-a")
        # Create project B (which will be in BRAINSTORMING)
        await tool_project_create(ctx, description="Project B", slug="pt-switch-b")
        clear_mock.reset_mock()
        # Switch back to project A (still EXECUTING) then switch to B
        await tool_project_switch(ctx, project="pt-switch-a")
        clear_mock.reset_mock()  # reset again after switch to A
        # Now switch away from the EXECUTING project A to project B
        result = await tool_project_switch(ctx, project="pt-switch-b")
        assert "pt-switch-b" in _text(result).lower()
        # The sticky slot should have been cleared when leaving the EXECUTING project
        assert clear_mock.await_count >= 1

    @pytest.mark.asyncio
    async def test_create_while_executing_clears(self, ctx, monkeypatch):
        """C2: Creating a new project while current is EXECUTING clears the tracker."""
        from unittest.mock import AsyncMock
        clear_mock = AsyncMock()
        monkeypatch.setattr("decafclaw.sticky.set_sticky", AsyncMock())
        monkeypatch.setattr("decafclaw.sticky.clear_sticky", clear_mock)
        # Create and advance project A to EXECUTING
        await _advance_to_executing(ctx, slug="pt-create-a")
        clear_mock.reset_mock()
        # Create a new project, which makes it active — should clear A's tracker
        result = await tool_project_create(ctx, description="Project B", slug="pt-create-b")
        assert "pt-create-b" in _text(result).lower()
        # The sticky slot should have been cleared when leaving the EXECUTING project
        assert clear_mock.await_count >= 1


# Any `project_*` token appearing in instruction text.
_PROJECT_TOOL_RE = re.compile(r"\bproject_[a-z_]+\b")


class TestPhaseInstructionConsistency:
    """#727 — instruction text must name tools the reading phase can dispatch.

    Each site's text is obtained by invoking the code that produces it, so the
    assertions track the real source instead of a copy of the strings.
    """

    @staticmethod
    async def _sites(ctx) -> list[tuple[str, str, ProjectState]]:
        """Build the (label, emitted text, the phase that reads it) table.

        One entry per (site, reading phase) pair. Where a site's text varies
        with the reading phase, the text is produced *for that phase* — grading
        the text emitted for one phase against a different phase's tool set
        describes a pairing that never occurs at runtime (see the amendment
        logged in the session's checks.md).

        A general control-flow analysis is out of scope for #727; new
        instruction sites get appended to this table by hand.
        """
        sites: list[tuple[str, str, ProjectState]] = []

        # 1. _next_execution_step, empty/missing-plan branch. Only EXECUTING
        #    reaches it, and the text does not vary.
        no_plan_info = ProjectInfo(
            slug="pic-no-plan",
            description="Project whose plan file is missing",
            status=ProjectState.EXECUTING,
            mode="normal",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            directory=ctx.config.workspace_path,
        )
        assert not no_plan_info.plan_path.exists(), (
            "fixture error: this ProjectInfo must hit the missing-plan branch"
        )
        sites.append(
            ("_next_execution_step", _next_execution_step(no_plan_info),
             ProjectState.EXECUTING)
        )

        # 2. tool_project_switch. The switched-to project's own status governs
        #    the next turn, so produce the text once per phase, with the target
        #    project actually in that phase.
        for i, phase in enumerate(ProjectState):
            slug = f"pic-switch-{i}"
            await tool_project_create(ctx, f"Switch target {i}", slug=slug)
            info = load_project(ctx.config, slug)
            assert info is not None, f"fixture error: {slug} did not persist"
            info.status = phase
            save_project(info)
            text = _text(await tool_project_switch(ctx, project=slug))
            assert phase.value in text, (
                f"fixture error: switch text for {slug} does not report "
                f"'{phase.value}' — the project was not in that phase"
            )
            sites.append(("tool_project_switch", text, phase))

        # 3. tool_project_advance success. The target phase reads it on the next
        #    turn, so produce the text once per reachable target — including the
        #    forward transition to DONE, which is the case #727 was filed for.
        for i, target in enumerate(
            sorted(TRANSITIONS[ProjectState.EXECUTING], key=lambda s: s.value)
        ):
            slug = f"pic-advance-{i}"
            await tool_project_create(ctx, f"Advance source {i}", slug=slug)
            info = load_project(ctx.config, slug)
            assert info is not None, f"fixture error: {slug} did not persist"
            info.status = ProjectState.EXECUTING
            save_project(info)
            await tool_project_switch(ctx, project=slug)
            text = _text(await tool_project_advance(ctx, target_status=target.value))
            assert target.value in text, (
                f"fixture error: advance to '{target.value}' did not succeed; "
                f"got {text!r}"
            )
            sites.append(("tool_project_advance", text, target))

        # 4. plan_no_steps.md. Returned from the PLANNING/PLAN_REVIEW branch of
        #    project_task_done before any status change, so the reading phase is
        #    whichever of the two it already was. Static file, same text for both.
        no_steps_text = _load_prompt("plan_no_steps")
        for phase in (ProjectState.PLANNING, ProjectState.PLAN_REVIEW):
            sites.append(("plan_no_steps", no_steps_text, phase))

        return sites

    @pytest.mark.asyncio
    async def test_no_instruction_names_undispatchable_tool(self, ctx):
        """C1: no instruction names a tool the reading phase cannot dispatch."""
        problems = []
        for label, text, phase in await self._sites(ctx):
            named = set(_PROJECT_TOOL_RE.findall(text))
            dispatchable = set(_PHASE_TOOLS[phase])
            undispatchable = sorted(named - dispatchable)
            if undispatchable:
                problems.append(
                    f"{label} → phase '{phase.value}' names "
                    f"{', '.join(undispatchable)}; dispatchable there: "
                    f"{', '.join(sorted(dispatchable))}"
                )
        assert not problems, (
            f"{len(problems)} instruction/phase mismatch(es):\n  "
            + "\n  ".join(problems)
        )

    @pytest.mark.asyncio
    async def test_every_instruction_names_a_dispatchable_tool(self, ctx):
        """C2: every instruction still points at a usable next action."""
        problems = []
        for label, text, phase in await self._sites(ctx):
            named = set(_PROJECT_TOOL_RE.findall(text))
            dispatchable = set(_PHASE_TOOLS[phase])
            if not named & dispatchable:
                problems.append(
                    f"{label} → phase '{phase.value}' names "
                    f"{', '.join(sorted(named)) or '(no project_* tool)'}, "
                    f"none of which is dispatchable there; dispatchable: "
                    f"{', '.join(sorted(dispatchable))}"
                )
        assert not problems, (
            f"{len(problems)} instruction(s) leave the agent with no next "
            f"action:\n  " + "\n  ".join(problems)
        )

    def test_guard_phase_tools_exclusions_preserved(self):
        """Widening the phase gates is not an acceptable fix for #727."""
        for phase in (
            ProjectState.SPEC_REVIEW,
            ProjectState.PLAN_REVIEW,
            ProjectState.DONE,
        ):
            assert "project_next_task" not in _PHASE_TOOLS[phase], (
                f"'{phase.value}' must not gain project_next_task"
            )
        assert "project_update_plan" not in _PHASE_TOOLS[ProjectState.EXECUTING], (
            "'executing' must not gain project_update_plan"
        )

    def test_guard_phase_gating_remains_real(self):
        """No phase may expose the whole registry — that would defeat gating."""
        for phase, names in _PHASE_TOOLS.items():
            if not isinstance(phase, ProjectState):
                continue
            withheld = set(TOOLS) - set(names)
            assert withheld, (
                f"'{phase.value}' exposes every tool in TOOLS; phase gating "
                f"must stay real"
            )

    def test_guard_transitions_unchanged(self):
        """The states reachable from EXECUTING define who reads advance's text."""
        assert TRANSITIONS[ProjectState.EXECUTING] == {
            ProjectState.DONE,
            ProjectState.PLANNING,
            ProjectState.BRAINSTORMING,
        }
