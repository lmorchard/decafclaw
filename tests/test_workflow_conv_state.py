"""Tests for conversation-scoped workflow state persistence."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from decafclaw.workflow.conv_state import (
    archive_workflow_state,
    artifacts_dir,
    conv_lock,
    init_workflow_state,
    load_workflow_state,
    save_workflow_state,
)
from decafclaw.workflow.types import RunStatus


def _ctx_for(tmp_path: Path, conv_id: str = "conv-abc"
             ) -> SimpleNamespace:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(workspace_path=workspace)
    return SimpleNamespace(config=config, conv_id=conv_id)


def test_init_creates_directory_and_workflow_json(tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="weeknotes",
                                initial_phase="gather")
    conv_dir = ctx.config.workspace_path / "conversations" / ctx.conv_id
    assert conv_dir.is_dir()
    assert (conv_dir / "workflow.json").is_file()
    assert state.status == RunStatus.RUNNING
    assert state.current_phase == "gather"
    assert state.workflow == "weeknotes"
    assert state.history and state.history[0]["to"] == "gather"


def test_load_after_init_returns_same_state(tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    init_workflow_state(ctx, workflow="weeknotes",
                        initial_phase="gather")
    loaded = load_workflow_state(ctx)
    assert loaded is not None
    assert loaded.workflow == "weeknotes"
    assert loaded.current_phase == "gather"


def test_load_returns_none_when_no_workflow(tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    assert load_workflow_state(ctx) is None


def test_save_atomic_write_no_leftover_tmp(tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="t",
                                initial_phase="a")
    state.current_phase = "b"
    save_workflow_state(ctx, state)
    conv_dir = ctx.config.workspace_path / "conversations" / ctx.conv_id
    leftovers = list(conv_dir.glob("*.tmp"))
    assert not leftovers
    reloaded = load_workflow_state(ctx)
    assert reloaded is not None
    assert reloaded.current_phase == "b"


def test_init_rejects_when_active_workflow_exists(tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    init_workflow_state(ctx, workflow="weeknotes",
                        initial_phase="gather")
    with pytest.raises(ValueError, match="already active"):
        init_workflow_state(ctx, workflow="other",
                            initial_phase="start")


def test_init_after_archive_allowed(tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="first",
                                initial_phase="a")
    state.status = RunStatus.DONE
    save_workflow_state(ctx, state)
    archive_workflow_state(ctx)
    # Starting a second workflow in the same conv should succeed
    second = init_workflow_state(ctx, workflow="second",
                                 initial_phase="x")
    assert second.workflow == "second"
    loaded = load_workflow_state(ctx)
    assert loaded is not None
    assert loaded.workflow == "second"


def test_archive_renames_workflow_json(tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="t",
                                initial_phase="a")
    state.status = RunStatus.DONE
    save_workflow_state(ctx, state)
    archived_path = archive_workflow_state(ctx)
    conv_dir = ctx.config.workspace_path / "conversations" / ctx.conv_id
    assert not (conv_dir / "workflow.json").exists()
    assert archived_path is not None
    assert archived_path.exists()
    assert archived_path.name.startswith("workflow-")
    assert archived_path.name.endswith(".json")


def test_archive_when_no_workflow_is_noop(tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    # Calling archive on a conv with no workflow should not raise
    result = archive_workflow_state(ctx)
    assert result is None


def test_artifacts_dir_returns_conv_scoped_path(tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    init_workflow_state(ctx, workflow="t", initial_phase="a")
    art = artifacts_dir(ctx)
    expected = (ctx.config.workspace_path / "conversations"
                / ctx.conv_id / "artifacts")
    assert art == expected


@pytest.mark.asyncio
async def test_conv_lock_serializes_concurrent_ops(tmp_path: Path):
    ctx = _ctx_for(tmp_path, conv_id="conv-lock-test")
    init_workflow_state(ctx, workflow="t", initial_phase="a")

    sequence: list[str] = []

    async def op(label: str):
        async with conv_lock(ctx):
            sequence.append(f"{label}-enter")
            await asyncio.sleep(0)  # yield
            sequence.append(f"{label}-exit")

    await asyncio.gather(op("A"), op("B"))
    assert sequence in (
        ["A-enter", "A-exit", "B-enter", "B-exit"],
        ["B-enter", "B-exit", "A-enter", "A-exit"],
    )


def test_load_skips_corrupted_state(tmp_path: Path, caplog):
    ctx = _ctx_for(tmp_path)
    init_workflow_state(ctx, workflow="t", initial_phase="a")
    conv_dir = ctx.config.workspace_path / "conversations" / ctx.conv_id
    (conv_dir / "workflow.json").write_text("{not json")
    with caplog.at_level("WARNING"):
        result = load_workflow_state(ctx)
    assert result is None
    assert any("workflow.json" in rec.message
               for rec in caplog.records)
