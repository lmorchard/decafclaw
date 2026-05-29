"""Conversation-scoped workflow state persistence.

State lives at:
    {workspace}/conversations/{conv_id}/workflow.json
    {workspace}/conversations/{conv_id}/artifacts/{phase}/...

The conv_id IS the implicit identifier for the workflow. One active
workflow per conversation; reaching a terminal state archives
workflow.json to workflow-<terminated_timestamp>.json in the same
directory so a fresh workflow can start.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from .types import RunStatus, WorkflowState

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conv_dir(ctx) -> Path:
    return (ctx.config.workspace_path / "conversations" / ctx.conv_id)


def _workflow_path(ctx) -> Path:
    return _conv_dir(ctx) / "workflow.json"


def artifacts_dir(ctx) -> Path:
    """Path to the artifacts root for the current conversation's
    workflow. Returned regardless of whether the directory exists —
    callers that write to it should mkdir(parents=True, exist_ok=True)
    on the specific subpath."""
    return _conv_dir(ctx) / "artifacts"


def init_workflow_state(ctx, workflow: str,
                        initial_phase: str) -> WorkflowState:
    """Initialize a fresh workflow for the current conversation.

    Raises ValueError if a workflow is already active in this conv
    (status is not done/error/aborted). Call archive_workflow_state
    first to start a successor.
    """
    existing = load_workflow_state(ctx)
    if existing is not None and existing.status not in (
            RunStatus.DONE, RunStatus.ERROR, RunStatus.ABORTED):
        raise ValueError(
            f"a workflow is already active in this conversation "
            f"(workflow='{existing.workflow}', "
            f"status='{existing.status.value}'); call workflow_abort "
            f"or wait for it to finish before starting another")

    conv_dir = _conv_dir(ctx)
    conv_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "artifacts").mkdir(exist_ok=True)

    now = _now_iso()
    state = WorkflowState(
        workflow=workflow,
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
    _write_state(ctx, state)
    log.info("[workflow] initialized %s for conv=%s",
             workflow, ctx.conv_id)
    return state


def save_workflow_state(ctx, state: WorkflowState) -> None:
    """Persist state to disk atomically. Updates state.updated_at."""
    state.updated_at = _now_iso()
    _write_state(ctx, state)


def _write_state(ctx, state: WorkflowState) -> None:
    path = _workflow_path(ctx)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(state.to_json())
    os.replace(tmp, path)


def load_workflow_state(ctx) -> WorkflowState | None:
    """Load the conversation's current workflow state, or None if
    no workflow is initialized (or the state file is corrupt)."""
    path = _workflow_path(ctx)
    if not path.is_file():
        return None
    try:
        return WorkflowState.from_json(path.read_text())
    except (ValueError, OSError) as exc:
        log.warning("[workflow] failed to load %s: %s", path, exc)
        return None


def archive_workflow_state(ctx) -> Path | None:
    """Rename the current workflow.json to workflow-<ts>.json so a
    successor workflow can start fresh. No-op if no workflow.json
    exists. Returns the archived path or None."""
    path = _workflow_path(ctx)
    if not path.is_file():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    archived = path.parent / f"workflow-{ts}.json"
    # On collision (rare), append microseconds
    if archived.exists():
        us = f"{datetime.now(timezone.utc).microsecond:06d}"
        archived = path.parent / f"workflow-{ts}{us}.json"
    os.replace(path, archived)
    log.info("[workflow] archived %s → %s for conv=%s",
             path.name, archived.name, ctx.conv_id)
    return archived


# Per-conversation lock registry. Locks are created lazily on first
# acquire and keyed by conv_id. Entries accumulate for the process
# lifetime — there is no GC. Acceptable because concurrent workflow
# operations on the same conv are rare.
_conv_locks: dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def conv_lock(ctx) -> AsyncIterator[None]:
    """Async context manager serializing workflow operations for one
    conversation."""
    lock = _conv_locks.setdefault(ctx.conv_id, asyncio.Lock())
    async with lock:
        yield
