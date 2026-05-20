"""Workflow run persistence and discovery.

State lives at:
    {workspace}/workflows/{workflow}/runs/{run_id}/state.json
    {workspace}/workflows/{workflow}/runs/{run_id}/artifacts/

run_id format: {YYYY-MM-DD-HHMM}-{workflow}-{slug}
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from .types import RunState, RunStatus

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")


def _workflows_root(workspace: Path) -> Path:
    return workspace / "workflows"


def _run_dir(workspace: Path, workflow: str, run_id: str) -> Path:
    return _workflows_root(workspace) / workflow / "runs" / run_id


def _state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def create_run(workspace: Path, workflow: str, slug: str,
               initial_phase: str) -> RunState:
    """Create a new workflow run on disk and return its RunState."""
    ts = _ts_prefix()
    run_id = f"{ts}-{workflow}-{slug}"
    run_dir = _run_dir(workspace, workflow, run_id)
    # On collision (rare — same-minute creation), disambiguate with
    # seconds, then microseconds if even that collides.
    if run_dir.exists():
        now = datetime.now(timezone.utc)
        secs = now.strftime("%S")
        run_id = f"{ts}{secs}-{workflow}-{slug}"
        run_dir = _run_dir(workspace, workflow, run_id)
        if run_dir.exists():
            usecs = f"{now.microsecond:06d}"
            run_id = f"{ts}{secs}{usecs}-{workflow}-{slug}"
            run_dir = _run_dir(workspace, workflow, run_id)
            if run_dir.exists():
                raise RuntimeError(
                    f"run id collision for {workflow}/{slug} "
                    "even after microsecond disambiguation")
    run_dir.mkdir(parents=True)
    (run_dir / "artifacts").mkdir()

    now = _now_iso()
    state = RunState(
        workflow=workflow,
        slug=slug,
        run_id=run_id,
        status=RunStatus.RUNNING,
        current_phase=initial_phase,
        created_at=now,
        updated_at=now,
        history=[{
            "from": None,
            "to": initial_phase,
            "edge_index": None,
            "gate_response": None,
            "reason": "initial",
            "timestamp": now,
        }],
    )
    _write_state(run_dir, state)
    log.info("[workflow] created run %s", run_id)
    return state


def save_run(workspace: Path, state: RunState) -> None:
    """Persist state to disk atomically."""
    state.updated_at = _now_iso()
    run_dir = _run_dir(workspace, state.workflow, state.run_id)
    _write_state(run_dir, state)


def _write_state(run_dir: Path, state: RunState) -> None:
    path = _state_path(run_dir)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(state.to_json())
    os.replace(tmp, path)


def load_run(workspace: Path, run_id: str) -> RunState | None:
    """Find and load a run by id. Returns None if not found or corrupt."""
    for state_path in _workflows_root(workspace).glob(
            f"*/runs/{run_id}/state.json"):
        try:
            return RunState.from_json(state_path.read_text())
        except (ValueError, OSError) as exc:
            log.warning("[workflow] failed to load %s: %s",
                        state_path, exc)
            return None
    return None


def list_runs(workspace: Path, workflow: str = "",
              status: str = "") -> list[RunState]:
    """List all runs, optionally filtered by workflow name or status.

    Most-recent first (sorted by run_id, which is timestamp-prefixed).
    """
    root = _workflows_root(workspace)
    if not root.is_dir():
        return []

    pattern = f"{workflow}/runs/*/state.json" if workflow \
        else "*/runs/*/state.json"

    results: list[RunState] = []
    for state_path in root.glob(pattern):
        try:
            state = RunState.from_json(state_path.read_text())
        except (ValueError, OSError) as exc:
            log.warning("[workflow] failed to load %s: %s",
                        state_path, exc)
            continue
        if status and state.status.value != status:
            continue
        results.append(state)

    results.sort(key=lambda s: s.run_id, reverse=True)
    return results


# Per-run lock registry. Locks are created lazily on first acquire and
# keyed by run_id. Entries accumulate for the process lifetime — there
# is no GC. Acceptable because concurrent workflow runs are few.
_run_locks: dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def run_lock(run_id: str) -> AsyncIterator[None]:
    """Async context manager that serializes operations on a single run."""
    lock = _run_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        yield
