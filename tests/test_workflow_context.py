"""Tests for WorkflowOverlay and composer integration."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from decafclaw.workflow import registry
from decafclaw.workflow.context import (
    WorkflowOverlay,
    consult_workflow_overlay,
)
from decafclaw.workflow.runs import create_run
from decafclaw.workflow.types import (
    EdgeDef,
    PhaseDef,
    PhaseKind,
    WorkflowDef,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


def _wf_with_profile() -> WorkflowDef:
    return WorkflowDef(
        name="demo", description="", initial_phase="a",
        phases={
            "a": PhaseDef(
                id="a", kind=PhaseKind.INLINE,
                prompt="You are in phase A.",
                tools=[],
                next_phases=[EdgeDef(id="b", when="ready", gate=None)],
                gate=None, outputs=(), subagent_skill=None,
                context_profile={
                    "memory-retrieval": "off",
                    "notes-injection": "off",
                    "clear-prior-phase-tools": True,
                },
            ),
            "b": PhaseDef(
                id="b", kind=PhaseKind.INLINE, prompt="",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=False, argument_hint="",
    )


def test_consult_returns_none_when_no_run(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = SimpleNamespace(
        config=SimpleNamespace(workspace_path=ws),
        skills=SimpleNamespace(data={}),
    )
    assert consult_workflow_overlay(ctx) is None


def test_consult_returns_overlay_with_phase_prompt(tmp_path: Path):
    registry.register(_wf_with_profile())
    ws = tmp_path / "ws"
    ws.mkdir()
    state = create_run(ws, workflow="demo", slug="x", initial_phase="a")
    ctx = SimpleNamespace(
        config=SimpleNamespace(workspace_path=ws),
        skills=SimpleNamespace(
            data={"current_workflow_run": state.run_id}),
    )
    overlay = consult_workflow_overlay(ctx)
    assert isinstance(overlay, WorkflowOverlay)
    assert "You are in phase A" in overlay.phase_prompt_section
    assert "phase_advance" in overlay.phase_prompt_section.lower()
    assert overlay.context_profile_overrides.get("memory-retrieval") == "off"
    assert overlay.context_profile_overrides.get("notes-injection") == "off"


def test_overlay_includes_when_clauses(tmp_path: Path):
    registry.register(_wf_with_profile())
    ws = tmp_path / "ws"
    ws.mkdir()
    state = create_run(ws, workflow="demo", slug="x", initial_phase="a")
    ctx = SimpleNamespace(
        config=SimpleNamespace(workspace_path=ws),
        skills=SimpleNamespace(
            data={"current_workflow_run": state.run_id}),
    )
    overlay = consult_workflow_overlay(ctx)
    assert "ready" in overlay.phase_prompt_section


def test_phase_boundary_marker_clears_once(tmp_path: Path):
    """Phase-boundary marker should clear tool results on the first call,
    then be a no-op on subsequent calls at the same marker index."""
    from types import SimpleNamespace

    from decafclaw.context_composer import ComposerState, ContextComposer

    composer = ContextComposer(state=ComposerState())
    config = SimpleNamespace(cleanup=SimpleNamespace(preserve_tools=[]))

    big_content = "huge tool output" * 200
    history = [
        {"role": "tool", "name": "search", "content": big_content,
         "tool_call_id": "t1"},
        {"role": "workflow_phase_boundary"},
        {"role": "tool", "name": "notes_append", "content": "kept",
         "tool_call_id": "t2"},
    ]

    # First call — should clear t1 (before the marker)
    composer._apply_phase_boundary_clear(config, history)
    assert "tool output cleared" in history[0]["content"]
    assert history[2]["content"] == "kept"  # post-marker untouched
    assert composer.state.last_cleared_workflow_boundary_idx == 1
    cleared_count_after_first = composer.state.cleanup_cleared_count

    # Restore the cleared entry to a large value to verify it is NOT re-cleared
    history[0]["content"] = big_content

    # Second call at the same marker — should be a no-op
    composer._apply_phase_boundary_clear(config, history)
    assert history[0]["content"] == big_content  # not re-cleared
    assert composer.state.cleanup_cleared_count == cleared_count_after_first
    assert composer.state.last_cleared_workflow_boundary_idx == 1  # unchanged

    # Add a new (later) marker — should clear up to the new marker
    history.append({"role": "tool", "name": "x", "content": big_content,
                    "tool_call_id": "t3"})
    history.append({"role": "workflow_phase_boundary"})
    new_marker_idx = len(history) - 1

    composer._apply_phase_boundary_clear(config, history)
    assert composer.state.last_cleared_workflow_boundary_idx == new_marker_idx


def test_clear_tool_results_in_range_stubs_targeted_messages(tmp_path: Path):
    from decafclaw.context_cleanup import clear_tool_results_in_range

    history = [
        {"role": "user", "content": "start"},
        {"role": "assistant", "content": "thinking"},
        {"role": "tool", "name": "x", "content": "huge tool output" * 200,
         "tool_call_id": "1"},
        {"role": "tool", "name": "y", "content": "also huge" * 200,
         "tool_call_id": "2"},
        {"role": "assistant", "content": "done with phase A"},
        {"role": "user", "content": "phase A -> B"},
        {"role": "tool", "name": "z", "content": "current phase output",
         "tool_call_id": "3"},
    ]
    stats = clear_tool_results_in_range(
        history, start_idx=2, end_idx=5,
        preserve_tools={"notes_append", "checklist_create"},
    )
    assert stats.cleared_count >= 2
    # Pre-range and post-range messages untouched
    assert history[6]["content"] == "current phase output"
    assert "tool output cleared" in history[2]["content"]
