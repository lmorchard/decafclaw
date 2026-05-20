"""Tests for workflow run persistence and discovery."""

import asyncio
from pathlib import Path

import pytest

from decafclaw.workflow.runs import (
    create_run,
    list_runs,
    load_run,
    run_lock,
    save_run,
)
from decafclaw.workflow.types import RunStatus


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def test_create_run_makes_directory_and_state_json(tmp_path):
    ws = _workspace(tmp_path)
    state = create_run(ws, workflow="weeknotes", slug="w20",
                       initial_phase="gather")
    run_dir = ws / "workflows" / "weeknotes" / "runs" / state.run_id
    assert run_dir.is_dir()
    assert (run_dir / "state.json").is_file()
    assert (run_dir / "artifacts").is_dir()
    assert state.status == RunStatus.RUNNING
    assert state.current_phase == "gather"
    assert state.history and state.history[0]["to"] == "gather"


def test_create_run_id_timestamp_prefix(tmp_path):
    ws = _workspace(tmp_path)
    state = create_run(ws, workflow="weeknotes", slug="w20",
                       initial_phase="gather")
    # Format: YYYY-MM-DD-HHMM-{workflow}-{slug}
    parts = state.run_id.split("-")
    assert len(parts) >= 5
    assert parts[-2] == "weeknotes"
    assert parts[-1] == "w20"


def test_load_run_round_trip(tmp_path):
    ws = _workspace(tmp_path)
    state = create_run(ws, workflow="weeknotes", slug="w20",
                       initial_phase="gather")
    loaded = load_run(ws, state.run_id)
    assert loaded == state


def test_save_run_atomic_write(tmp_path):
    ws = _workspace(tmp_path)
    state = create_run(ws, workflow="t", slug="x", initial_phase="a")
    state.current_phase = "b"
    save_run(ws, state)
    # No leftover .tmp files
    run_dir = ws / "workflows" / "t" / "runs" / state.run_id
    leftovers = list(run_dir.glob("*.tmp"))
    assert not leftovers
    reloaded = load_run(ws, state.run_id)
    assert reloaded.current_phase == "b"


def test_list_runs_walks_all_workflows(tmp_path):
    ws = _workspace(tmp_path)
    r1 = create_run(ws, workflow="weeknotes", slug="w20",
                    initial_phase="gather")
    r2 = create_run(ws, workflow="story", slug="shadowport",
                    initial_phase="premise")
    ids = {r.run_id for r in list_runs(ws)}
    assert ids == {r1.run_id, r2.run_id}


def test_list_runs_filter_by_workflow(tmp_path):
    ws = _workspace(tmp_path)
    create_run(ws, workflow="weeknotes", slug="w20", initial_phase="g")
    s2 = create_run(ws, workflow="story", slug="shadowport",
                    initial_phase="p")
    runs = list_runs(ws, workflow="story")
    assert [r.run_id for r in runs] == [s2.run_id]


def test_list_runs_skips_corrupted_state(tmp_path, caplog):
    ws = _workspace(tmp_path)
    state = create_run(ws, workflow="t", slug="x", initial_phase="a")
    (ws / "workflows" / "t" / "runs" / state.run_id /
     "state.json").write_text("{not json")
    # Should not raise; should log a warning
    with caplog.at_level("WARNING"):
        runs = list_runs(ws)
    assert runs == []
    assert any("state.json" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_run_lock_serializes_concurrent_advances(tmp_path):
    ws = _workspace(tmp_path)
    state = create_run(ws, workflow="t", slug="x", initial_phase="a")

    sequence: list[str] = []

    async def advance(label: str):
        async with run_lock(state.run_id):
            sequence.append(f"{label}-enter")
            await asyncio.sleep(0)  # yield
            sequence.append(f"{label}-exit")

    await asyncio.gather(advance("A"), advance("B"))
    # A must fully complete before B starts (or vice versa) — interleaved
    # enter/exit would prove the lock failed
    assert sequence in (
        ["A-enter", "A-exit", "B-enter", "B-exit"],
        ["B-enter", "B-exit", "A-enter", "A-exit"],
    )
