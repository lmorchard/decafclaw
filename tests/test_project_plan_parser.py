"""Tests for project plan parser."""

import pytest

from decafclaw.skills.project.plan_parser import (
    Step,
    find_step,
    insert_steps,
    next_actionable,
    parse_plan,
    plan_progress,
    render_plan,
    update_step_status,
)

SAMPLE_PLAN = """\
# Plan: Test Project

## Overview
A test plan for unit testing.

## Steps

- [ ] 1. First step
  - [ ] 1.1. Sub-step A
  - [ ] 1.2. Sub-step B
- [ ] 2. Second step
- [x] 3. Third step (done)
  > Completed: All good.
- [-] 4. Fourth step (skipped)
  > Skipped: Not needed.
- [>] 5. Fifth step (in progress)
"""


class TestParsePlan:
    def test_parse_basic(self):
        overview, steps, tail = parse_plan(SAMPLE_PLAN)
        assert "Test Project" in overview
        assert len(steps) == 5

    def test_parse_statuses(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        assert steps[0].status == "pending"
        assert steps[2].status == "done"
        assert steps[3].status == "skipped"
        assert steps[4].status == "in_progress"

    def test_parse_notes(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        assert steps[2].note == "Completed: All good."
        assert steps[3].note == "Skipped: Not needed."

    def test_parse_children(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        assert len(steps[0].children) == 2
        assert steps[0].children[0].number == "1.1"
        assert steps[0].children[1].number == "1.2"

    def test_parse_empty(self):
        overview, steps, tail = parse_plan("")
        assert overview == ""
        assert steps == []


class TestRenderPlan:
    def test_round_trip(self):
        """Parse then render should produce parseable output."""
        overview, steps, tail = parse_plan(SAMPLE_PLAN)
        rendered = render_plan(overview, steps)
        overview2, steps2, _ = parse_plan(rendered)
        assert len(steps) == len(steps2)
        for s1, s2 in zip(steps, steps2):
            assert s1.number == s2.number
            assert s1.description == s2.description
            assert s1.status == s2.status
            assert s1.note == s2.note
            assert len(s1.children) == len(s2.children)

    def test_render_empty(self):
        assert render_plan("", []) == "\n"


class TestFindStep:
    def test_find_top_level(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        step = find_step(steps, "2")
        assert step is not None
        assert step.description == "Second step"

    def test_find_sub_step(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        step = find_step(steps, "1.2")
        assert step is not None
        assert step.description == "Sub-step B"

    def test_find_nonexistent(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        assert find_step(steps, "99") is None


class TestNextActionable:
    def test_first_pending(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        step = next_actionable(steps)
        # Step 1 has children, so should return first pending child
        assert step is not None
        assert step.number == "1.1"

    def test_in_progress_step(self):
        steps = [
            Step("1", "Done step", status="done"),
            Step("2", "In progress", status="in_progress"),
            Step("3", "Pending", status="pending"),
        ]
        step = next_actionable(steps)
        assert step is not None
        assert step.number == "2"

    def test_all_done(self):
        steps = [
            Step("1", "Done", status="done"),
            Step("2", "Skipped", status="skipped"),
        ]
        assert next_actionable(steps) is None

    def test_in_progress_parent_pending_child(self):
        parent = Step(
            "1", "Parent", status="in_progress",
            children=[
                Step("1.1", "Done child", status="done"),
                Step("1.2", "Pending child", status="pending"),
            ],
        )
        step = next_actionable([parent])
        assert step is not None
        assert step.number == "1.2"

    def test_in_progress_parent_all_children_done(self):
        """Parent should be returned when all children are done."""
        parent = Step(
            "1", "Parent", status="in_progress",
            children=[
                Step("1.1", "Done", status="done"),
                Step("1.2", "Done", status="done"),
            ],
        )
        step = next_actionable([parent])
        assert step is not None
        assert step.number == "1"


class TestUpdateStepStatus:
    def test_update_existing(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        assert update_step_status(steps, "2", "done", "Finished!")
        step = find_step(steps, "2")
        assert step.status == "done"
        assert step.note == "Finished!"

    def test_update_sub_step(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        assert update_step_status(steps, "1.1", "in_progress")
        step = find_step(steps, "1.1")
        assert step.status == "in_progress"

    def test_update_nonexistent(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        assert not update_step_status(steps, "99", "done")


class TestInsertSteps:
    def test_insert_top_level(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        assert insert_steps(steps, "2", ["New step A", "New step B"])
        # Should be renumbered
        assert steps[2].description == "New step A"
        assert steps[3].description == "New step B"
        assert steps[2].number == "3"
        assert steps[3].number == "4"
        # Original step 3 is now 5
        assert steps[4].description == "Third step (done)"
        assert steps[4].number == "5"

    def test_insert_sub_steps(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        assert insert_steps(steps, "1.1", ["New sub-step"])
        parent = steps[0]
        assert len(parent.children) == 3
        assert parent.children[1].description == "New sub-step"
        assert parent.children[1].number == "1.2"
        # Original 1.2 is now 1.3
        assert parent.children[2].description == "Sub-step B"
        assert parent.children[2].number == "1.3"

    def test_insert_nonexistent(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        assert not insert_steps(steps, "99", ["Nope"])


UNNUMBERED_PLAN = """\
# Plan: Test

## Overview
A plan with unnumbered steps.

## Steps

- [ ] First step
- [ ] Second step
- [x] Third step
  > Done already.
- [ ] Fourth step
"""


class TestUnnumberedParsing:
    def test_unnumbered_steps_get_auto_numbered(self):
        _, steps, _ = parse_plan(UNNUMBERED_PLAN)
        assert len(steps) == 4
        assert steps[0].number == "1"
        assert steps[1].number == "2"
        assert steps[2].number == "3"
        assert steps[3].number == "4"

    def test_unnumbered_preserves_status(self):
        _, steps, _ = parse_plan(UNNUMBERED_PLAN)
        assert steps[0].status == "pending"
        assert steps[2].status == "done"
        assert steps[2].note == "Done already."

    def test_unnumbered_round_trip(self):
        overview, steps, tail = parse_plan(UNNUMBERED_PLAN)
        rendered = render_plan(overview, steps)
        # After round-trip, steps should be numbered
        assert "- [ ] 1." in rendered
        assert "- [ ] 2." in rendered
        overview2, steps2, _ = parse_plan(rendered)
        assert len(steps) == len(steps2)


class TestTrailingContent:
    def test_preserves_trailing_content(self):
        plan_with_tail = """\
# Plan

## Steps

- [ ] 1. First step
- [ ] 2. Second step

## Notes

Some trailing content here.
"""
        overview, steps, tail = parse_plan(plan_with_tail)
        assert len(steps) == 2
        assert "trailing content" in tail
        # Round-trip preserves it after steps
        rendered = render_plan(overview, steps, tail)
        assert "trailing content" in rendered
        # Verify tail comes after steps, not before
        step_pos = rendered.index("- [ ] 1.")
        tail_pos = rendered.index("trailing content")
        assert tail_pos > step_pos


class TestPlanProgress:
    def test_mixed_statuses(self):
        _, steps, _ = parse_plan(SAMPLE_PLAN)
        completed, total = plan_progress(steps)
        # Leaves: 1.1 (pending), 1.2 (pending), 2 (pending),
        # 3 (done), 4 (skipped), 5 (in_progress) = 2/6
        assert total == 6
        assert completed == 2

    def test_all_done(self):
        steps = [
            Step("1", "Done", status="done"),
            Step("2", "Skipped", status="skipped"),
        ]
        assert plan_progress(steps) == (2, 2)

    def test_empty(self):
        assert plan_progress([]) == (0, 0)
