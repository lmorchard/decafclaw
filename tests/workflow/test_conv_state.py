"""Tests for conv_state — persistence, lock, archive, schema (step-keyed dict)."""

import asyncio
import json
from pathlib import Path

import pytest

from decafclaw.workflow.conv_state import (
    archive_workflow_state,
    conv_lock,
    init_workflow_state,
    load_workflow_state,
    save_workflow_state,
)
from decafclaw.workflow.types import RunStatus, WorkflowState


@pytest.fixture
def workspace_ctx(ctx, tmp_path):
    """Set up ctx with a tmp workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Override workspace_path on config
    ctx.config._workspace_path_override = workspace
    # Monkey-patch workspace_path to point at tmp
    ctx.config.__class__ = type(
        "PatchedConfig",
        (type(ctx.config),),
        {"workspace_path": property(lambda self: workspace)},
    )
    return ctx


def test_init_and_load(workspace_ctx):
    ctx = workspace_ctx
    state = init_workflow_state(ctx, workflow="test_wf", initial_step="greet")
    assert state.status == RunStatus.RUNNING
    assert state.current_step == "greet"
    assert state.initial_step == "greet"
    assert state.state == {}
    assert state.transitions == []

    loaded = load_workflow_state(ctx)
    assert loaded is not None
    assert loaded.workflow == "test_wf"
    assert loaded.current_step == "greet"


def test_init_raises_if_active_workflow_exists(workspace_ctx):
    ctx = workspace_ctx
    init_workflow_state(ctx, workflow="wf1", initial_step="step1")
    with pytest.raises(ValueError, match="already active"):
        init_workflow_state(ctx, workflow="wf2", initial_step="step1")


def test_init_allows_new_after_done(workspace_ctx):
    ctx = workspace_ctx
    state = init_workflow_state(ctx, workflow="wf1", initial_step="step1")
    state.status = RunStatus.DONE
    save_workflow_state(ctx, state)
    # Now a new one should succeed
    state2 = init_workflow_state(ctx, workflow="wf2", initial_step="step1")
    assert state2.workflow == "wf2"


def test_save_and_load_state(workspace_ctx):
    ctx = workspace_ctx
    state = init_workflow_state(ctx, workflow="hello", initial_step="greet")
    state.state["greet"] = {"greeting": "hi"}
    state.status = RunStatus.DONE
    save_workflow_state(ctx, state)

    loaded = load_workflow_state(ctx)
    assert loaded.status == RunStatus.DONE
    assert loaded.state["greet"]["greeting"] == "hi"


def test_load_returns_none_if_no_file(workspace_ctx):
    ctx = workspace_ctx
    assert load_workflow_state(ctx) is None


def test_archive_workflow_state(workspace_ctx):
    ctx = workspace_ctx
    state = init_workflow_state(ctx, workflow="hello", initial_step="greet")
    state.status = RunStatus.DONE
    save_workflow_state(ctx, state)

    archived = archive_workflow_state(ctx)
    assert archived is not None
    assert archived.is_file()
    assert "workflow-" in archived.name
    # Original is gone
    assert load_workflow_state(ctx) is None


def test_archive_returns_none_if_no_file(workspace_ctx):
    ctx = workspace_ctx
    assert archive_workflow_state(ctx) is None


def test_state_uses_step_keyed_dict(workspace_ctx):
    """Verify state schema is step_id -> output dict, not phase history."""
    ctx = workspace_ctx
    state = init_workflow_state(ctx, workflow="wf", initial_step="step1")
    # The state dict should be keyed by step_id
    state.state["step1"] = {"result": "value1"}
    state.state["step2"] = {"result": "value2"}
    save_workflow_state(ctx, state)

    loaded = load_workflow_state(ctx)
    assert loaded.state == {"step1": {"result": "value1"}, "step2": {"result": "value2"}}
    # No 'history' key (old schema)
    raw = json.loads((
        ctx.config.workspace_path
        / "conversations" / ctx.conv_id / "workflow.json"
    ).read_text())
    assert "history" not in raw  # old field gone
    assert "transitions" in raw   # new field


@pytest.mark.asyncio
async def test_conv_lock_serializes(workspace_ctx):
    """conv_lock serializes concurrent access for same conv_id."""
    ctx = workspace_ctx
    order = []

    async def task1():
        async with conv_lock(ctx):
            order.append("t1-in")
            await asyncio.sleep(0.01)
            order.append("t1-out")

    async def task2():
        await asyncio.sleep(0.001)
        async with conv_lock(ctx):
            order.append("t2-in")

    await asyncio.gather(task1(), task2())
    assert order == ["t1-in", "t1-out", "t2-in"]
