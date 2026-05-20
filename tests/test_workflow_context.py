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
