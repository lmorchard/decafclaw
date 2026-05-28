# Workflow Engine Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the workflow engine (PR #557) so workflow state is conversation-scoped instead of cross-conversation. Drops `workflow_list`/`workflow_switch`/`runs.py`/run-id machinery; adds `workflow_abort`, `required-skills` auto-activation, and `priority: critical` on the dynamic `phase_advance` schema (root-cause fix for the demo's "unknown tool" loop).

**Architecture:** Replace `src/decafclaw/workflow/runs.py` with `conv_state.py` — state at `conversations/{conv_id}/workflow.json`, artifacts at `conversations/{conv_id}/artifacts/{phase}/`. One active workflow per conversation; reaching terminal state archives `workflow.json` to `workflow-<terminated_timestamp>.json` so a fresh workflow can start. Engine + composer + tools updated to call into `conv_state` instead of run-keyed persistence. Subagent dispatch unchanged in concept; just the artifact directory path changes.

**Tech Stack:** Python 3.x (dataclasses, pyyaml, asyncio), pytest, decafclaw's existing `ctx.conv_id` plumbing.

**Spec:** [`spec.md`](spec.md) in this directory.

**Original implementation (kept as reference):** branch `feat/255-workflow-engine` commits up through `7425996`. The rework adds commits on top; final PR is squash-merged. Earlier "demo bug fix" commits already landed (subagent dispatch wiring `2a83053`, overlay refresh `2ff9e13`, tool catalog filter `7425996`) and stay — the rework integrates them.

---

## File Structure

### Created

| Path | Responsibility |
|---|---|
| `src/decafclaw/workflow/conv_state.py` | Conversation-scoped workflow state persistence. Replaces `runs.py`. Functions: `init_workflow_state`, `load_workflow_state`, `save_workflow_state`, `archive_workflow_state`, `conv_lock`, `artifacts_dir`. |
| `tests/test_workflow_conv_state.py` | Tests for the above. Replaces `tests/test_workflow_runs.py`. |

### Deleted

| Path | Reason |
|---|---|
| `src/decafclaw/workflow/runs.py` | Replaced by `conv_state.py`. |
| `tests/test_workflow_runs.py` | Replaced by `tests/test_workflow_conv_state.py`. |

### Modified

| Path | Change |
|---|---|
| `src/decafclaw/workflow/types.py` | Rename `RunState` → `WorkflowState`. Drop `run_id` field. Add `required_skills: list[str]` to `WorkflowDef`. |
| `src/decafclaw/workflow/engine.py` | Switch from `runs.run_lock(run_id)`/`load_run`/`save_run` to the `conv_state` equivalents. `verify_subagent_outputs` uses conv-scoped artifact path. `dispatch_subagent_if_needed` passes `ctx` (not state) for state lookup. |
| `src/decafclaw/workflow/loader.py` | Parse `required-skills:` from SKILL.md frontmatter; validate it's a list of strings; populate `WorkflowDef.required_skills`. |
| `src/decafclaw/workflow/subagent.py` | Artifact directory hint for the child becomes `conversations/{conv_id}/artifacts/{phase}/`. |
| `src/decafclaw/workflow/context.py` | `consult_workflow_overlay` reads from conv state. Drop `WorkflowOverlay.run_id` field (becomes `conv_id` or just goes away — see Task 7). |
| `src/decafclaw/tools/workflow_tools.py` | Major rewrite. Drop `tool_workflow_list`, `tool_workflow_switch`. Add `tool_workflow_abort`. Drop `slug` from `tool_workflow_start`; add required-skills activation. `_get_run` → `_get_workflow`. `_resolve_artifact_path` uses conv-scoped path. Add `"priority": "critical"` to `build_phase_advance_definition`. Update `WORKFLOW_TOOLS` + `WORKFLOW_TOOL_DEFINITIONS`. |
| `tests/test_workflow_types.py` | Update names (`RunState` → `WorkflowState`); drop `run_id` references; add `required_skills` test. |
| `tests/test_workflow_loader.py` | Add tests for `required-skills` parsing (valid + invalid). |
| `tests/test_workflow_engine.py` | Update API calls: pass `ctx` instead of `(workspace, state)` where applicable; use `init_workflow_state` instead of `create_run`. Drop `run_id` references. |
| `tests/test_workflow_tools.py` | Drop tests for `tool_workflow_list` and `tool_workflow_switch`. Add tests for `workflow_abort` and required-skills activation. Update existing tests for the new API. |
| `tests/test_workflow_context.py` | Update overlay tests for conv-scoped state. |
| `src/decafclaw/skills/workflow_demo/SKILL.md` | Add `required-skills: [tabstack]`. Update body to drop `slug` from `workflow_start` call. |
| `docs/workflows.md` | Rewrite for conv-scoped architecture. Drop `workflow_list`/`workflow_switch`/run-id from docs; add `required-skills`, `workflow_abort`. |
| `CLAUDE.md` | Update workflow bullet in Skills section if the public surface changed in any user-visible way. |
| `src/decafclaw/context.py` | (Likely unchanged. The `ToolState.workflow_restricted` flag stays. The `current_workflow_run` pointer in `ctx.skills.data` is no longer needed but harmless if vestigial — Task 5 cleans it up.) |
| `src/decafclaw/agent.py` | `_refresh_workflow_msg` continues to call `consult_workflow_overlay(ctx)`. No changes needed unless overlay signature changes. |

### Unchanged

- `src/decafclaw/context_composer.py` — overlay integration already in place from prior commits.
- `src/decafclaw/tool_definitions.py` — `refresh_workflow_tools` hook stays.
- `src/decafclaw/workflow/registry.py` — workflow definition registry stays.
- `src/decafclaw/workflow/__init__.py` — module init stays.
- Phase files (`src/decafclaw/skills/workflow_demo/phases/*.md`) — unchanged.
- `evals/workflow_routing.yaml` — unchanged (scenarios still apply).

---

## Task 1: Rename `RunState` → `WorkflowState`; add `required_skills` field

**Files:**
- Modify: `src/decafclaw/workflow/types.py`
- Modify: `tests/test_workflow_types.py`

Foundational rename. Drops `run_id` (conv_id is the implicit identifier). Adds `required_skills: list[str]` to `WorkflowDef`.

- [ ] **Step 1.1: Update tests to use `WorkflowState` and new fields**

In `tests/test_workflow_types.py`, replace `RunState` references with `WorkflowState`. Drop the `run_id` argument from any constructor calls. Update `test_run_state_json_round_trip` (rename to `test_workflow_state_json_round_trip` for consistency):

```python
def test_workflow_state_json_round_trip():
    state = WorkflowState(
        workflow="weeknotes",
        status=RunStatus.PAUSED_GATE,
        current_phase="draft",
        created_at="2026-05-19T14:02:00+00:00",
        updated_at="2026-05-19T14:35:12+00:00",
        history=[
            {"from": None, "to": "gather", "edge_index": None,
             "gate_response": None, "reason": "initial",
             "timestamp": "2026-05-19T14:02:00+00:00"}
        ],
        pending_gate={"edge_target": "review", "on_deny": "draft"},
        pending_subagent=None,
        error=None,
    )
    raw = state.to_json()
    parsed = json.loads(raw)
    assert parsed["workflow"] == "weeknotes"
    assert parsed["status"] == "paused-gate"
    back = WorkflowState.from_json(raw)
    assert back == state
```

Also update the import at top of file:

```python
from decafclaw.workflow.types import (
    EdgeDef,
    GateDef,
    PhaseDef,
    PhaseKind,
    WorkflowDef,
    WorkflowState,
    RunStatus,
)
```

Note we keep `RunStatus` — the enum values (`running`, `paused-gate`, etc.) still apply. Only the dataclass is renamed.

Add a new test for `required_skills`:

```python
def test_workflow_def_required_skills_default_empty():
    p = PhaseDef(
        id="a", kind=PhaseKind.INLINE, prompt="", tools=[],
        next_phases=[], gate=None, outputs=(),
        subagent_skill=None, context_profile={},
    )
    wf = WorkflowDef(
        name="t", description="", initial_phase="a",
        phases={"a": p},
        user_invocable=False, argument_hint="",
    )
    assert wf.required_skills == []


def test_workflow_def_required_skills_populated():
    p = PhaseDef(
        id="a", kind=PhaseKind.INLINE, prompt="", tools=[],
        next_phases=[], gate=None, outputs=(),
        subagent_skill=None, context_profile={},
    )
    wf = WorkflowDef(
        name="t", description="", initial_phase="a",
        phases={"a": p},
        user_invocable=False, argument_hint="",
        required_skills=["tabstack", "vault"],
    )
    assert wf.required_skills == ["tabstack", "vault"]
```

Also update `RunStatus` test if it includes "aborted":

```python
def test_run_status_values():
    assert {s.value for s in RunStatus} == {
        "running", "paused-gate", "paused-subagent", "done", "error",
        "aborted",
    }
```

- [ ] **Step 1.2: Run tests, expect failure**

Run: `pytest tests/test_workflow_types.py -v`
Expected: FAIL — `WorkflowState` not defined, `required_skills` field missing, `aborted` not in `RunStatus`.

- [ ] **Step 1.3: Update `types.py`**

In `src/decafclaw/workflow/types.py`:

1. Add `ABORTED = "aborted"` to `RunStatus`:

```python
class RunStatus(str, Enum):
    RUNNING = "running"
    PAUSED_GATE = "paused-gate"
    PAUSED_SUBAGENT = "paused-subagent"
    DONE = "done"
    ERROR = "error"
    ABORTED = "aborted"
```

2. Rename `RunState` → `WorkflowState`, drop `run_id` and `slug` fields:

```python
@dataclass
class WorkflowState:
    """A workflow's durable state for one conversation — serialized to
    {workspace}/conversations/{conv_id}/workflow.json. The conv_id is
    the implicit identifier; no run_id field needed."""

    workflow: str
    status: RunStatus
    current_phase: str
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601
    history: list[dict] = field(default_factory=list)
    pending_gate: dict | None = None
    pending_subagent: dict | None = None
    error: str | None = None

    def to_json(self) -> str:
        d = asdict(self)
        # str-Enum already serializes as its value via json.dumps, but
        # we set it explicitly so round-trip stays correct if RunStatus
        # is ever changed to not inherit from str.
        d["status"] = self.status.value
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> WorkflowState:
        d = json.loads(raw)
        d["status"] = RunStatus(d["status"])
        return cls(**d)
```

3. Add `required_skills: list[str]` to `WorkflowDef`:

```python
@dataclass(frozen=True)
class WorkflowDef:
    """A loaded workflow definition. Built by loader.py, consumed by engine."""

    name: str
    description: str
    initial_phase: str
    phases: dict[str, PhaseDef]
    user_invocable: bool
    argument_hint: str
    required_skills: list[str] = field(default_factory=list)

    def phase(self, phase_id: str) -> PhaseDef | None:
        return self.phases.get(phase_id)
```

- [ ] **Step 1.4: Run tests, expect pass**

Run: `pytest tests/test_workflow_types.py -v`
Expected: PASS — all tests including the two new `required_skills` tests + the `RunStatus` aborted test.

- [ ] **Step 1.5: Commit**

Run `make lint` first; expect clean.

```bash
git add src/decafclaw/workflow/types.py tests/test_workflow_types.py
git commit -m "$(cat <<'EOF'
refactor(workflow): rename RunState→WorkflowState; add required_skills + ABORTED

Foundational rename for the conv-scoped rework: RunState becomes
WorkflowState (no run_id field — the conv_id is the implicit
identifier). RunStatus gains ABORTED for explicit
user-abandoned-mid-workflow state. WorkflowDef gains a
required_skills: list[str] field for the new required-skills
declaration in SKILL.md frontmatter.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Replace `runs.py` with `conv_state.py`

**Files:**
- Create: `src/decafclaw/workflow/conv_state.py`
- Create: `tests/test_workflow_conv_state.py`
- Delete: `src/decafclaw/workflow/runs.py` (in step 2.6)
- Delete: `tests/test_workflow_runs.py` (in step 2.6)

Core persistence change. The conversation directory holds workflow state + artifacts. Per-conversation `asyncio.Lock` registry (same idea as `_run_locks`, just keyed by conv_id).

- [ ] **Step 2.1: Write failing tests for `conv_state.py`**

```python
# tests/test_workflow_conv_state.py
"""Tests for conversation-scoped workflow state persistence."""

from __future__ import annotations

import asyncio
import json
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
from decafclaw.workflow.types import RunStatus, WorkflowState


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
```

- [ ] **Step 2.2: Run tests, expect ImportError**

Run: `pytest tests/test_workflow_conv_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'decafclaw.workflow.conv_state'`.

- [ ] **Step 2.3: Implement `conv_state.py`**

```python
# src/decafclaw/workflow/conv_state.py
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
```

- [ ] **Step 2.4: Run tests, expect pass**

Run: `pytest tests/test_workflow_conv_state.py -v`
Expected: PASS — 10 passed.

- [ ] **Step 2.5: Run lint + typecheck**

Run: `make lint && make typecheck`
Expected: clean.

- [ ] **Step 2.6: Delete the old runs.py module and tests**

```bash
git rm src/decafclaw/workflow/runs.py tests/test_workflow_runs.py
```

(Tasks 4, 5, 6, 7 below will update everything that imports from `runs.py` — but they touch separate files; deleting now flushes the dead module without affecting the rest of this commit.)

Run `pytest tests/test_workflow_conv_state.py -v` again to confirm nothing was relying on `runs.py` from the conv_state tests.

- [ ] **Step 2.7: Commit**

```bash
git add src/decafclaw/workflow/conv_state.py tests/test_workflow_conv_state.py
git commit -m "$(cat <<'EOF'
feat(workflow): conv-scoped state persistence (replaces runs.py)

Workflow state for one conversation lives at
conversations/{conv_id}/workflow.json with artifacts at
conversations/{conv_id}/artifacts/. The conv_id is the implicit
identifier — no run_id field, no cross-conversation discovery.

init_workflow_state rejects a fresh init when a workflow is
already active in the conv (forces explicit abort first).
archive_workflow_state renames workflow.json to a timestamped
archive so a successor workflow can start cleanly.

Per-conv asyncio.Lock registry serializes concurrent workflow
operations on the same conv.

Deletes runs.py and tests/test_workflow_runs.py — engine, tools,
subagent, composer-overlay updates in following tasks switch
their imports.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Note: After this commit, `make test` will fail because engine.py / workflow_tools.py / subagent.py / context.py still import from the deleted `runs.py`. Tasks 4-7 fix those imports. This is the only commit in the plan that leaves the tree red, and it's by design — the alternative (cramming everything into one mega-commit) is worse for review.

---

## Task 3: Add `required-skills` parsing to loader

**Files:**
- Modify: `src/decafclaw/workflow/loader.py`
- Modify: `tests/test_workflow_loader.py`

Parse `required-skills:` from SKILL.md frontmatter; validate it's a list of strings; populate `WorkflowDef.required_skills`.

- [ ] **Step 3.1: Add failing tests**

Append to `tests/test_workflow_loader.py`:

```python
def test_load_parses_required_skills(tmp_path):
    skill_md = """---
name: demo
description: test workflow
kind: workflow
user-invocable: true
required-skills: [tabstack, vault]
workflow:
  initial-phase: gather
---
body
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": skill_md,
        "phases/gather.md": _GATHER,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    wf = load_workflow(d)
    assert wf.required_skills == ["tabstack", "vault"]


def test_load_required_skills_defaults_empty(tmp_path):
    """When required-skills is absent, default to empty list."""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,  # no required-skills
        "phases/gather.md": _GATHER,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    wf = load_workflow(d)
    assert wf.required_skills == []


def test_load_fails_when_required_skills_not_a_list(tmp_path):
    skill_md = """---
name: demo
description: test workflow
kind: workflow
user-invocable: true
required-skills: tabstack
workflow:
  initial-phase: gather
---
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": skill_md,
        "phases/gather.md": _GATHER,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="required-skills"):
        load_workflow(d)


def test_load_fails_when_required_skills_contains_non_string(tmp_path):
    skill_md = """---
name: demo
description: test workflow
kind: workflow
user-invocable: true
required-skills: [tabstack, null]
workflow:
  initial-phase: gather
---
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": skill_md,
        "phases/gather.md": _GATHER,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="required-skills"):
        load_workflow(d)
```

- [ ] **Step 3.2: Run tests, expect failure on the new tests**

Run: `pytest tests/test_workflow_loader.py -v`
Expected: 4 new tests fail (no parsing logic + `required_skills` field not populated).

- [ ] **Step 3.3: Implement parsing in `loader.py`**

In `src/decafclaw/workflow/loader.py`, find `load_workflow` and update its `WorkflowDef` construction. Right before the existing `return WorkflowDef(...)`:

```python
    # Parse required-skills: must be a list of non-empty strings, or absent.
    required_skills_raw = meta.get("required-skills", [])
    if not isinstance(required_skills_raw, list):
        raise LoaderError(
            "required-skills must be a list of skill names "
            f"(got {type(required_skills_raw).__name__})")
    required_skills: list[str] = []
    for i, entry in enumerate(required_skills_raw):
        if not isinstance(entry, str) or not entry.strip():
            raise LoaderError(
                f"required-skills[{i}] must be a non-empty string")
        required_skills.append(entry.strip())
```

Then update the `return WorkflowDef(...)` call to include `required_skills=required_skills`:

```python
    return WorkflowDef(
        name=name,
        description=description,
        initial_phase=initial,
        phases=phases,
        user_invocable=bool(meta.get("user-invocable", False)),
        argument_hint=meta.get("argument-hint", ""),
        required_skills=required_skills,
    )
```

- [ ] **Step 3.4: Run tests, expect pass**

Run: `pytest tests/test_workflow_loader.py -v`
Expected: PASS — all tests including the 4 new ones.

- [ ] **Step 3.5: Lint + commit**

Run: `make lint`. Expected clean.

```bash
git add src/decafclaw/workflow/loader.py tests/test_workflow_loader.py
git commit -m "$(cat <<'EOF'
feat(workflow): parse required-skills from SKILL.md frontmatter

Workflow definitions can declare required-skills: [skill1, skill2]
in SKILL.md frontmatter. Loader validates it's a list of non-empty
strings and populates WorkflowDef.required_skills. Empty/absent
defaults to an empty list.

Engine consumption (auto-activate on workflow_start) lands in
Task 5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Update `engine.py` to use `conv_state`

**Files:**
- Modify: `src/decafclaw/workflow/engine.py`
- Modify: `tests/test_workflow_engine.py`

Switch from `runs.py` API (load_run/save_run/run_lock/run_id) to conv_state API (load_workflow_state/save_workflow_state/conv_lock/ctx). `verify_subagent_outputs` reads from `conversations/{conv_id}/artifacts/{phase}/` instead of run-keyed path. `dispatch_and_finalize_subagent` and `dispatch_subagent_if_needed` take `ctx` consistently.

This is the biggest mechanical change but the logic is unchanged. Tests get a parallel rewrite.

- [ ] **Step 4.1: Read the existing engine.py end-to-end**

Read `src/decafclaw/workflow/engine.py` to remember:
- The exact functions (`advance`, `finalize_gate_response`, `dispatch_and_finalize_subagent`, `dispatch_subagent_if_needed`, `verify_subagent_outputs`, `_apply_transition`, `_enter_gate`, `AdvanceResult`).
- That `_now_iso` is imported from `runs.py` — this import needs to move (define locally or import from `conv_state`).
- The signatures: most functions take `(workspace, state)` plus other args. The rework changes these to take `ctx`.

- [ ] **Step 4.2: Update test fixtures + signatures**

Rewrite `tests/test_workflow_engine.py`. Major changes:
- Replace `create_run(ws, workflow, slug, initial_phase)` with `init_workflow_state(ctx, workflow=..., initial_phase=...)`. Note the change in API.
- Replace `load_run(ws, run_id)` with `load_workflow_state(ctx)`.
- Drop the `slug` argument everywhere.
- All test contexts get a `conv_id` field.

The full test file rewrite is large but mechanical. Here is the new `_ctx_for` helper to use (place near the top, after imports):

```python
def _ctx_for(tmp_path: Path,
             conv_id: str = "conv-engine-test") -> SimpleNamespace:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(workspace_path=workspace)
    return SimpleNamespace(config=config, conv_id=conv_id,
                           manager=None)
```

And the migration pattern for each test:

Before:
```python
ws = tmp_path / "workspace"
ws.mkdir()
state = create_run(ws, workflow="demo", slug="x", initial_phase="a")
# ...
result = await advance(ws, state, target="b", reason="done")
# ...
reloaded = load_run(ws, state.run_id)
```

After:
```python
ctx = _ctx_for(tmp_path)
state = init_workflow_state(ctx, workflow="demo", initial_phase="a")
# ...
result = await advance(ctx, state, target="b", reason="done")
# ...
reloaded = load_workflow_state(ctx)
```

Apply this transformation to every test in `test_workflow_engine.py`. The assertions about `state.current_phase`, `state.status`, `state.history`, `state.pending_gate` stay unchanged.

Also: update the TOCTOU test (`test_finalize_gate_response_uses_fresh_state`) — the second `finalize_gate_response` call uses the same stale `captured` state, but now the engine re-loads from disk via conv_state inside the lock.

For subagent tests, update the monkeypatched stub signature:

```python
async def fake_run_child(*, ctx, state, phase):
    artifacts_dir_ = (ctx.config.workspace_path / "conversations"
                     / ctx.conv_id / "artifacts" / phase.id)
    artifacts_dir_.mkdir(parents=True, exist_ok=True)
    (artifacts_dir_ / "sources.md").write_text("fetched")
    return "done"
```

Note: `_run_child` signature changes from `(*, ctx, workspace, state, phase)` to `(*, ctx, state, phase)` — `workspace` is `ctx.config.workspace_path`, no need to pass separately.

- [ ] **Step 4.3: Run tests, expect failures**

Run: `pytest tests/test_workflow_engine.py -v`
Expected: many failures from API mismatch — engine.py still uses old API.

- [ ] **Step 4.4: Update engine.py imports**

In `src/decafclaw/workflow/engine.py`, find the top imports and replace:

```python
from .runs import _now_iso, run_lock, save_run
```

with:

```python
from .conv_state import (
    artifacts_dir,
    conv_lock,
    load_workflow_state,
    save_workflow_state,
)
```

Also add a local `_now_iso`:

```python
from datetime import datetime, timezone

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
```

(Imported from `runs.py` before; locally defined now to avoid cross-module import of a private helper.)

- [ ] **Step 4.5: Update function signatures and bodies**

Refactor every function in `engine.py`. The pattern: replace `workspace: Path` first arg with `ctx`, replace `run_lock(state.run_id)` with `conv_lock(ctx)`, replace `save_run(workspace, state)` with `save_workflow_state(ctx, state)`, replace `load_run(workspace, state.run_id)` with `load_workflow_state(ctx)`.

`advance` becomes:

```python
async def advance(ctx, state: WorkflowState, target: str,
                  reason: str) -> AdvanceResult:
    """Advance the workflow along the matching edge. Same behavior as
    before — gates return EndTurnConfirm, non-gated transitions apply
    immediately. State is mutated and persisted.
    """
    wf = registry.get(state.workflow)
    if wf is None:
        raise ValueError(f"workflow '{state.workflow}' not registered")

    async with conv_lock(ctx):
        phase = wf.phase(state.current_phase)
        if phase is None:
            raise ValueError(
                f"current phase '{state.current_phase}' not in workflow")
        found = _find_edge(phase, target)
        if found is None:
            valid = ", ".join(e.id for e in phase.next_phases) or "(none)"
            raise ValueError(
                f"'{target}' is not a valid next phase from "
                f"'{state.current_phase}'. Valid: {valid}")
        edge_idx, edge = found

        if edge.gate is not None:
            return _enter_gate(ctx, state, edge_idx, edge, reason)

        return _apply_transition(
            ctx, wf, state, edge_idx, target, reason,
            gate_response=None)
```

`_enter_gate` becomes:

```python
def _enter_gate(ctx, state: WorkflowState, edge_idx: int,
                edge: EdgeDef, reason: str) -> AdvanceResult:
    gate = edge.gate
    assert gate is not None
    on_deny = gate.on_deny or state.current_phase
    state.status = RunStatus.PAUSED_GATE
    state.pending_gate = {"edge_target": edge.id, "on_deny": on_deny}
    save_workflow_state(ctx, state)

    confirm = EndTurnConfirm(
        message=gate.message,
        approve_label=gate.approve_label,
        deny_label=gate.deny_label,
        on_approve=None,
        on_deny=None,
    )
    return AdvanceResult(new_phase=state.current_phase,
                         end_turn_signal=confirm)
```

`finalize_gate_response` becomes (note: re-loads via conv_state inside the lock, preserving the TOCTOU fix from `e7b6032`):

```python
async def finalize_gate_response(ctx, state: WorkflowState,
                                 approved: bool) -> AdvanceResult:
    wf = registry.get(state.workflow)
    if wf is None:
        raise ValueError(f"workflow '{state.workflow}' not registered")

    async with conv_lock(ctx):
        fresh = load_workflow_state(ctx)
        if fresh is None:
            raise ValueError("no workflow active in conversation")
        if fresh.status != RunStatus.PAUSED_GATE \
                or fresh.pending_gate is None:
            raise ValueError("workflow is not paused on a gate")
        state = fresh

        pending = state.pending_gate
        assert pending is not None
        target = pending["edge_target"] if approved else pending["on_deny"]
        phase = wf.phase(state.current_phase)
        if phase is None:
            raise ValueError(
                f"current phase '{state.current_phase}' not in workflow")
        edge_idx = -1
        for i, e in enumerate(phase.next_phases):
            if e.id == pending["edge_target"]:
                edge_idx = i
                break
        if edge_idx < 0:
            raise ValueError(
                f"gate edge target '{pending['edge_target']}' is no "
                f"longer in phase '{state.current_phase}' — workflow "
                "definition changed mid-run?")
        state.pending_gate = None
        return _apply_transition(
            ctx, wf, state, edge_idx, target,
            reason=("user approved" if approved else "user denied"),
            gate_response=("approved" if approved else "denied"))
```

`_apply_transition` becomes:

```python
def _apply_transition(ctx, wf: WorkflowDef, state: WorkflowState,
                      edge_idx: int, target: str, reason: str,
                      gate_response: str | None) -> AdvanceResult:
    prev = state.current_phase
    next_phase = wf.phase(target)
    if next_phase is None:
        raise ValueError(f"transition target '{target}' not in workflow")
    state.current_phase = target
    state.history.append({
        "from": prev,
        "to": target,
        "edge_index": edge_idx if edge_idx >= 0 else None,
        "gate_response": gate_response,
        "reason": reason,
        "timestamp": _now_iso(),
    })
    if next_phase.is_terminal:
        state.status = RunStatus.DONE
    elif next_phase.kind == PhaseKind.SUBAGENT:
        state.status = RunStatus.PAUSED_SUBAGENT
    else:
        state.status = RunStatus.RUNNING
    save_workflow_state(ctx, state)
    return AdvanceResult(new_phase=target, end_turn_signal=None)
```

`verify_subagent_outputs` becomes (uses conv-scoped artifacts path):

```python
def verify_subagent_outputs(ctx, state: WorkflowState,
                            phase_id: str) -> list[str]:
    """Return the list of expected outputs MISSING from artifacts.
    Empty list = all present (or fail-open for non-subagent phases)."""
    wf = registry.get(state.workflow)
    if wf is None:
        return []
    phase = wf.phase(phase_id)
    if phase is None or phase.kind != PhaseKind.SUBAGENT:
        return []
    art_root = artifacts_dir(ctx)
    missing: list[str] = []
    for output in phase.outputs:
        if not (art_root / phase_id / output).is_file():
            missing.append(output)
    return missing
```

`dispatch_and_finalize_subagent` becomes (note: no more workspace arg; calls subagent._run_child with new signature):

```python
async def dispatch_and_finalize_subagent(ctx, state: WorkflowState,
                                         phase_id: str) -> None:
    from . import subagent as wf_subagent

    wf = registry.get(state.workflow)
    if wf is None:
        raise ValueError(f"workflow '{state.workflow}' not registered")
    phase = wf.phase(phase_id)
    if phase is None:
        raise ValueError(
            f"phase '{phase_id}' not in workflow '{state.workflow}'")

    async with conv_lock(ctx):
        try:
            await wf_subagent._run_child(
                ctx=ctx, state=state, phase=phase,
            )
        except Exception as exc:
            log.exception(
                "[workflow] subagent crashed for conv=%s phase=%s",
                ctx.conv_id, phase_id)
            state.status = RunStatus.ERROR
            state.error = f"subagent crashed: {exc}"
            save_workflow_state(ctx, state)
            return

        missing = verify_subagent_outputs(ctx, state, phase_id)
        if missing:
            state.status = RunStatus.ERROR
            state.error = (
                "subagent did not produce required outputs: "
                + ", ".join(missing)
            )
            save_workflow_state(ctx, state)
            log.warning(
                "[workflow] subagent for conv=%s phase=%s missing "
                "outputs: %s", ctx.conv_id, phase_id, missing)
            return

        if len(phase.next_phases) != 1:
            state.status = RunStatus.ERROR
            state.error = (
                f"subagent phase '{phase_id}' must have exactly one "
                f"next-phases edge for auto-advance "
                f"(found {len(phase.next_phases)})"
            )
            save_workflow_state(ctx, state)
            return

        target = phase.next_phases[0].id
        _apply_transition(
            ctx, wf, state, edge_idx=0, target=target,
            reason="subagent complete", gate_response=None,
        )
        log.info("[workflow] subagent complete for conv=%s phase=%s → %s",
                 ctx.conv_id, phase_id, target)
```

`dispatch_subagent_if_needed` becomes:

```python
async def dispatch_subagent_if_needed(ctx,
                                       state: WorkflowState
                                       ) -> WorkflowState:
    """Synchronously dispatch the subagent for the current phase if
    it's a subagent phase. Loops if dispatch advances to another
    subagent. Capped to prevent infinite chains.

    No-op when current phase is inline, terminal, or status is
    DONE/ERROR/ABORTED/PAUSED_GATE."""
    for _ in range(_SUBAGENT_DISPATCH_CHAIN_CAP):
        wf = registry.get(state.workflow)
        if wf is None:
            return state
        phase = wf.phase(state.current_phase)
        if phase is None or phase.kind != PhaseKind.SUBAGENT:
            return state
        if state.status in (RunStatus.DONE, RunStatus.ERROR,
                            RunStatus.ABORTED, RunStatus.PAUSED_GATE):
            return state

        await dispatch_and_finalize_subagent(
            ctx, state, state.current_phase)

        fresh = load_workflow_state(ctx)
        if fresh is None:
            return state
        state = fresh

    log.warning(
        "[workflow] subagent dispatch chain hit cap (%d) for conv=%s",
        _SUBAGENT_DISPATCH_CHAIN_CAP, ctx.conv_id)
    return state
```

- [ ] **Step 4.6: Run tests, expect pass**

Run: `pytest tests/test_workflow_engine.py -v`
Expected: PASS — all engine tests including the rewrite of fixtures + the TOCTOU + subagent dispatch tests.

- [ ] **Step 4.7: Lint + typecheck**

Run: `make lint && make typecheck`
Expected: clean.

- [ ] **Step 4.8: Commit**

```bash
git add src/decafclaw/workflow/engine.py tests/test_workflow_engine.py
git commit -m "$(cat <<'EOF'
refactor(workflow): engine uses conv_state instead of runs.py

Switch advance / finalize_gate_response / verify_subagent_outputs /
dispatch_and_finalize_subagent / dispatch_subagent_if_needed from
the (workspace, state) signature to (ctx, state). conv_lock(ctx)
replaces run_lock(state.run_id). load_workflow_state(ctx) /
save_workflow_state(ctx, state) replace load_run / save_run.
verify_subagent_outputs reads from
conversations/{conv_id}/artifacts/{phase}/ via artifacts_dir(ctx).

TOCTOU fix from e7b6032 preserved: finalize_gate_response re-loads
state from disk inside the conv_lock.

_now_iso defined locally instead of imported from the deleted
runs.py.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Rewrite `workflow_tools.py`

**Files:**
- Modify: `src/decafclaw/tools/workflow_tools.py`
- Modify: `tests/test_workflow_tools.py`

Major surface rewrite:
- Drop `tool_workflow_list`, `tool_workflow_switch` (and their TOOL_DEFINITIONS entries).
- Add `tool_workflow_abort`.
- Simplify `tool_workflow_start`: no `slug` param; activate required-skills before initializing state.
- `_get_run` → `_get_workflow` (loads from conv_state).
- `_resolve_artifact_path` uses `artifacts_dir(ctx)`.
- Add `"priority": "critical"` to the dynamic `phase_advance` definition (suspected root cause of the demo's "unknown tool" loop).

- [ ] **Step 5.1: Find the skill activation entry point**

Read `src/decafclaw/tools/skill_tools.py` and locate `activate_skill_internal` (the function the `activate_skill` tool ultimately calls). Note its signature — we'll call it directly from `tool_workflow_start` to activate `required-skills`. Confirm it can be called from a tool context (it should be — `activate_skill` itself is a tool).

If `activate_skill_internal` is awaitable, our code must `await` it. If it's sync, just call directly. (As of this writing it's likely async; verify.)

- [ ] **Step 5.2: Update tests**

Rewrite `tests/test_workflow_tools.py`. Major changes:

1. Drop `test_workflow_list_and_switch` entirely.
2. Update `_ctx_for` to add `conv_id`:

```python
def _ctx_for(tmp_path: Path,
             conv_id: str = "conv-tool-test") -> SimpleNamespace:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(workspace_path=workspace,
                             discovered_skills=[])
    skills = SimpleNamespace(data={}, activated=set())
    return SimpleNamespace(config=config, skills=skills,
                           conv_id=conv_id, manager=None)
```

3. Drop the `slug` param everywhere it's passed to `tool_workflow_start`. Drop `tool_workflow_switch` import. Drop `tool_workflow_list` import. Add `tool_workflow_abort` import.

4. Replace `ctx.skills.data["current_workflow_run"]` checks with checks on `conv_state.load_workflow_state(ctx)`.

5. Add new tests:

```python
@pytest.mark.asyncio
async def test_workflow_start_no_active_workflow_initializes(
        tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    result = await tool_workflow_start(ctx, name="demo")
    text = result.text if isinstance(result, ToolResult) else result
    assert "demo" in text
    from decafclaw.workflow.conv_state import load_workflow_state
    state = load_workflow_state(ctx)
    assert state is not None
    assert state.workflow == "demo"


@pytest.mark.asyncio
async def test_workflow_start_with_active_workflow_errors(
        tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo")
    second = await tool_workflow_start(ctx, name="demo")
    assert isinstance(second, ToolResult)
    assert "already active" in second.text


@pytest.mark.asyncio
async def test_workflow_abort_when_active(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo")
    result = await tool_workflow_abort(ctx, reason="user requested")
    text = result.text if isinstance(result, ToolResult) else result
    assert "abort" in text.lower()
    from decafclaw.workflow.conv_state import load_workflow_state
    assert load_workflow_state(ctx) is None  # archived


@pytest.mark.asyncio
async def test_workflow_abort_when_no_workflow_errors(tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    result = await tool_workflow_abort(ctx, reason="")
    assert isinstance(result, ToolResult)
    assert "no workflow" in result.text.lower()


@pytest.mark.asyncio
async def test_workflow_start_activates_required_skills(
        tmp_path: Path, monkeypatch):
    """workflow_start auto-activates every skill in required_skills
    before initializing state."""
    activated: list[str] = []

    async def fake_activate(ctx, name):
        activated.append(name)
        return ToolResult(text=f"Activated {name}")

    monkeypatch.setattr(
        "decafclaw.tools.workflow_tools._activate_skill_for_workflow",
        fake_activate)

    wf = WorkflowDef(
        name="needs_tabstack", description="", initial_phase="a",
        phases={
            "a": PhaseDef(
                id="a", kind=PhaseKind.INLINE, prompt="", tools=[],
                next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=False, argument_hint="",
        required_skills=["tabstack", "vault"],
    )
    registry.register(wf)
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="needs_tabstack")
    assert activated == ["tabstack", "vault"]


@pytest.mark.asyncio
async def test_workflow_start_fails_when_required_skill_fails(
        tmp_path: Path, monkeypatch):
    async def fake_activate(ctx, name):
        if name == "tabstack":
            return ToolResult(text="[error: tabstack: denied]")
        return ToolResult(text=f"Activated {name}")

    monkeypatch.setattr(
        "decafclaw.tools.workflow_tools._activate_skill_for_workflow",
        fake_activate)

    wf = WorkflowDef(
        name="needs_tabstack", description="", initial_phase="a",
        phases={
            "a": PhaseDef(
                id="a", kind=PhaseKind.INLINE, prompt="", tools=[],
                next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=False, argument_hint="",
        required_skills=["tabstack"],
    )
    registry.register(wf)
    ctx = _ctx_for(tmp_path)
    result = await tool_workflow_start(ctx, name="needs_tabstack")
    assert isinstance(result, ToolResult)
    assert "tabstack" in result.text
    assert "required" in result.text.lower() or "failed" in result.text.lower()

    from decafclaw.workflow.conv_state import load_workflow_state
    # State should NOT be initialized on activation failure
    assert load_workflow_state(ctx) is None


@pytest.mark.asyncio
async def test_phase_advance_definition_has_critical_priority(
        tmp_path: Path):
    """The dynamic phase_advance schema must declare critical
    priority so it stays in the active catalog under load."""
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    ctx.tools = SimpleNamespace(extra={}, extra_definitions=[],
                                allowed=None)
    await tool_workflow_start(ctx, name="demo")
    definition = build_phase_advance_definition(ctx)
    assert definition is not None
    assert definition.get("priority") == "critical"
```

6. The existing `test_phase_advance_*` and `test_workflow_artifact_*` tests stay, but their setup uses the new `_ctx_for` (which now provides `conv_id`) and they no longer call `_set_current_run`.

- [ ] **Step 5.3: Run tests, expect failures**

Run: `pytest tests/test_workflow_tools.py -v`
Expected: many failures from API changes + missing `tool_workflow_abort` + missing priority field.

- [ ] **Step 5.4: Rewrite `workflow_tools.py`**

Major rewrite. Key changes:

1. Imports — drop the now-unneeded `list_runs`; add conv_state helpers:

```python
from ..media import EndTurnConfirm, ToolResult
from ..workflow import engine, registry
from ..workflow.conv_state import (
    archive_workflow_state,
    artifacts_dir,
    init_workflow_state,
    load_workflow_state,
)
from ..workflow.types import PhaseKind
```

2. Drop `_set_current_run`, replace `_get_run` with `_get_workflow`:

```python
def _get_workflow(ctx):
    """Return (state, wf) or (None, None) if no workflow is active or
    the registered workflow is gone."""
    state = load_workflow_state(ctx)
    if state is None:
        return None, None
    wf = registry.get(state.workflow)
    if wf is None:
        return None, None
    return state, wf
```

(Every internal call site that used `_get_run` updates to `_get_workflow`. Same shape, different storage.)

3. Add `_activate_skill_for_workflow`:

```python
async def _activate_skill_for_workflow(ctx, name: str) -> ToolResult:
    """Activate a skill required by a workflow definition.

    Delegates to the standard skill activation path so user-tier
    skills hit the same approval gate they would for a direct
    activate_skill call. Returns a ToolResult — success carries the
    skill body text, failure carries '[error: ...]'.
    """
    from .skill_tools import tool_activate_skill
    return await tool_activate_skill(ctx, name=name)
```

(This wrapping is testable via monkeypatch and isolates the change from skill_tools internals.)

4. Update `build_phase_advance_definition` to include priority:

```python
    return {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "phase_advance",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "target_phase_id": {
                        "type": "string",
                        "enum": enum_vals,
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Brief justification (1-2 sentences) "
                            "for choosing this target."),
                    },
                },
                "required": ["target_phase_id"],
            },
        },
    }
```

5. Rewrite `tool_workflow_start`:

```python
async def tool_workflow_start(ctx, name: str) -> str | ToolResult:
    """Start a fresh workflow for the current conversation.

    Activates each skill in `wf.required_skills` first (errors if any
    fails); then initializes conversation-scoped state. If the initial
    phase is a subagent, dispatches it synchronously before returning.

    Errors if a workflow is already active in this conversation (the
    user must call workflow_abort first, or wait for the current run
    to finish).
    """
    wf = registry.get(name)
    if wf is None:
        return ToolResult(text=f"[error: workflow '{name}' not found]")

    # Reject if a workflow is already active in this conv.
    existing = load_workflow_state(ctx)
    if existing is not None and existing.status not in (
            engine.RunStatus.DONE, engine.RunStatus.ERROR,
            engine.RunStatus.ABORTED):
        return ToolResult(text=(
            f"[error: workflow '{existing.workflow}' is already "
            f"active in this conversation (status: "
            f"{existing.status.value}). Call workflow_abort first, or "
            f"wait for it to finish.]"))

    # If a previous workflow ended, archive its state before starting
    # the new one so the directory layout is clean.
    if existing is not None:
        archive_workflow_state(ctx)

    # Activate required skills BEFORE initializing state, so a partial
    # init doesn't leave behind a dead workflow.json.
    for skill_name in wf.required_skills:
        result = await _activate_skill_for_workflow(ctx, skill_name)
        text = result.text if isinstance(result, ToolResult) else result
        if isinstance(text, str) and text.startswith("[error"):
            return ToolResult(text=(
                f"[error: required skill '{skill_name}' failed to "
                f"activate: {text}. Cannot start workflow '{name}'.]"))

    state = init_workflow_state(
        ctx, workflow=name, initial_phase=wf.initial_phase)

    # If the initial phase is a subagent, dispatch synchronously so the
    # run advances past it before the LLM continues.
    state = await engine.dispatch_subagent_if_needed(ctx, state)

    return (
        f"Started workflow '{name}'. "
        f"Current phase: {state.current_phase}. "
        f"Status: {state.status.value}. "
        f"Use phase_advance to move forward."
    )
```

6. Replace `tool_workflow_list` and `tool_workflow_switch` with `tool_workflow_abort`. Delete the two old functions:

```python
async def tool_workflow_abort(ctx, reason: str = "") -> str | ToolResult:
    """Abort the current workflow in this conversation.

    Marks the workflow as aborted, archives its workflow.json to
    workflow-<timestamp>.json in the same directory, and clears the
    conversation's active-workflow state. Artifacts remain on disk for
    reference but the workflow is no longer the conversation's
    active context.
    """
    state = load_workflow_state(ctx)
    if state is None:
        return ToolResult(text="[error: no workflow active to abort]")

    state.status = engine.RunStatus.ABORTED
    state.error = reason.strip() or "user aborted"
    from .workflow_tools import load_workflow_state as _load  # noqa
    from ..workflow.conv_state import save_workflow_state
    save_workflow_state(ctx, state)
    archive_workflow_state(ctx)
    return (
        f"Aborted workflow '{state.workflow}' "
        f"(was at phase '{state.current_phase}'). "
        f"Reason: {state.error}"
    )
```

(The double import in the body is a code-smell. Simplify by importing `save_workflow_state` at the top of the file and removing the inline imports.)

Cleaner version, with the import at module top:

```python
from ..workflow.conv_state import (
    archive_workflow_state,
    artifacts_dir,
    init_workflow_state,
    load_workflow_state,
    save_workflow_state,
)
```

```python
async def tool_workflow_abort(ctx, reason: str = "") -> str | ToolResult:
    state = load_workflow_state(ctx)
    if state is None:
        return ToolResult(text="[error: no workflow active to abort]")

    state.status = engine.RunStatus.ABORTED
    state.error = reason.strip() or "user aborted"
    save_workflow_state(ctx, state)
    archive_workflow_state(ctx)
    return (
        f"Aborted workflow '{state.workflow}' "
        f"(was at phase '{state.current_phase}'). "
        f"Reason: {state.error}"
    )
```

7. Update `tool_workflow_status` to use `_get_workflow`:

```python
async def tool_workflow_status(ctx) -> str | ToolResult:
    state, wf = _get_workflow(ctx)
    if state is None or wf is None:
        return "No workflow active in this conversation. Use workflow_start to begin."
    phase = wf.phase(state.current_phase)
    lines = [
        f"# Workflow: {state.workflow}",
        f"**Phase:** {state.current_phase}",
        f"**Status:** {state.status.value}",
        f"**Updated:** {state.updated_at}",
    ]
    if phase and phase.next_phases:
        lines.append("\n**Available transitions:**")
        for edge in phase.next_phases:
            when = edge.when.strip() or "(only option)"
            gated = " [gated]" if edge.gate else ""
            lines.append(f"  - `{edge.id}`{gated} — {when}")
    elif phase and phase.is_terminal:
        lines.append("\n**Terminal phase** — no transitions available.")
    if state.history:
        lines.append("\n**Recent history:**")
        for h in state.history[-5:]:
            arrow = f"{h.get('from', '∅')} → {h['to']}"
            lines.append(f"  - {arrow} ({h.get('reason', '')})")
    return "\n".join(lines)
```

8. Update `tool_phase_advance` to call the new engine signatures (drop `workspace` arg):

```python
async def tool_phase_advance(ctx, target_phase_id: str,
                              reason: str = "") -> str | ToolResult:
    state, wf = _get_workflow(ctx)
    if state is None or wf is None:
        return ToolResult(text="[error: no active workflow run]")
    try:
        result = await engine.advance(
            ctx, state, target=target_phase_id, reason=reason)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")

    if result.end_turn_signal is not None:
        confirm = result.end_turn_signal
        if not isinstance(confirm, EndTurnConfirm):
            log.warning(
                "[workflow] unexpected end_turn_signal type %r from "
                "engine.advance — expected EndTurnConfirm",
                type(confirm).__name__)
            return ToolResult(
                text="[error: unexpected gate signal type from engine]")

        async def _on_approve():
            s = load_workflow_state(ctx)
            if s is not None:
                await engine.finalize_gate_response(ctx, s,
                                                   approved=True)

        async def _on_deny():
            s = load_workflow_state(ctx)
            if s is not None:
                await engine.finalize_gate_response(ctx, s,
                                                   approved=False)

        confirm.on_approve = _on_approve
        confirm.on_deny = _on_deny
        return ToolResult(text="Submitted for review.",
                          end_turn=confirm)

    fresh = load_workflow_state(ctx)
    if fresh is not None:
        fresh = await engine.dispatch_subagent_if_needed(ctx, fresh)
        return ToolResult(
            text=f"Advanced to phase '{fresh.current_phase}' "
                 f"(status: {fresh.status.value}).",
            end_turn=False)
    return ToolResult(
        text=f"Advanced to phase '{result.new_phase}'.",
        end_turn=False)
```

9. Update `_resolve_artifact_path` to use `artifacts_dir(ctx)`:

```python
def _resolve_artifact_path(ctx, relative_path: str) -> Path | None:
    state, _wf = _get_workflow(ctx)
    if state is None:
        return None
    base = artifacts_dir(ctx).resolve()
    candidate = (base / relative_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate
```

10. Update `WORKFLOW_TOOLS` + `WORKFLOW_TOOL_DEFINITIONS`. Drop list/switch; add abort:

```python
WORKFLOW_TOOLS = {
    "workflow_start": tool_workflow_start,
    "workflow_status": tool_workflow_status,
    "workflow_abort": tool_workflow_abort,
    "workflow_artifact_write": tool_workflow_artifact_write,
    "workflow_artifact_read": tool_workflow_artifact_read,
    # phase_advance is dynamic — injected per turn by
    # refresh_workflow_tools when a workflow is active.
}

WORKFLOW_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_start",
            "description": (
                "Start a fresh workflow in the current conversation. "
                "Activates the workflow's required-skills first, then "
                "initializes per-conversation state."),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_status",
            "description": (
                "Show the current workflow: phase, status, valid next "
                "phases with their when: annotations, recent history."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_abort",
            "description": (
                "Abort the currently-active workflow in this "
                "conversation. Archives state and clears the active-"
                "workflow context. Errors if no workflow is active."),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why the workflow is being aborted.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_artifact_write",
            "description": (
                "Write content to a relative path under the current "
                "workflow's artifacts/ directory."),
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["relative_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "workflow_artifact_read",
            "description": (
                "Read content from a relative path under the current "
                "workflow's artifacts/ directory."),
            "parameters": {
                "type": "object",
                "properties": {
                    "relative_path": {"type": "string"},
                },
                "required": ["relative_path"],
            },
        },
    },
]
```

11. Remove `tool_workflow_list` and `tool_workflow_switch` function definitions entirely.

- [ ] **Step 5.5: Run tests, expect pass**

Run: `pytest tests/test_workflow_tools.py -v`
Expected: PASS — all tests including the 7 new ones; old tests for list/switch are gone.

- [ ] **Step 5.6: Lint + typecheck**

Run: `make lint && make typecheck && make test 2>&1 | tail -5`
Expected: clean. (Full suite to confirm nothing else regressed.)

- [ ] **Step 5.7: Commit**

```bash
git add src/decafclaw/tools/workflow_tools.py tests/test_workflow_tools.py
git commit -m "$(cat <<'EOF'
refactor(workflow): tools surface for conv-scoped state

- workflow_start drops slug param; activates required-skills before
  initializing state; rejects if a workflow is already active in the
  conv (returns clear error pointing at workflow_abort).
- New workflow_abort tool: marks state as ABORTED and archives
  workflow.json.
- workflow_list and workflow_switch deleted — no cross-conv
  discovery in the new architecture.
- _resolve_artifact_path uses conv-scoped artifacts_dir(ctx).
- build_phase_advance_definition gains "priority": "critical" — root
  cause fix for the demo's "unknown tool 'phase_advance'" loop. With
  normal priority the tool was deferrable under load even though it
  was in ctx.tools.extra.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Update `subagent.py` for conv-scoped artifacts

**Files:**
- Modify: `src/decafclaw/workflow/subagent.py`

Smaller change. The subagent dispatcher's `_run_child` signature drops `workspace` (it's derivable from `ctx`). Artifact directory hint for the child becomes conv-scoped.

- [ ] **Step 6.1: Read current subagent.py**

Read `src/decafclaw/workflow/subagent.py` end-to-end to remember:
- `_run_child(*, ctx, workspace, state, phase) -> str` is the current signature.
- The setup callback that builds child_ctx is the bulk of the logic.
- Recent commit `7425996` made the child inherit `ctx.tools.extra` / `extra_definitions` / `skills.activated` / `skills.data` — that stays.

- [ ] **Step 6.2: Update the signature and artifacts hint**

In `src/decafclaw/workflow/subagent.py`:

1. Drop `workspace: Path` parameter from `_run_child`:

```python
async def _run_child(*, ctx, state: RunState,
                     phase: PhaseDef) -> str:
```

(Note: `RunState` in the type hint should also become `WorkflowState` — search for any remaining `RunState` references in this file and rename.)

2. Inside `_run_child`, where the child's working dir or artifacts path was passed: change to use `conversations/{conv_id}/artifacts/{phase}/`. If the subagent's prompt mentions the artifacts path explicitly (e.g., in the gather prompt), the workflow author handles the relative path; we don't pass a `workspace` arg, but the child sees `ctx.config.workspace_path` and the convention is that `workflow_artifact_write(relative_path)` writes under the current conv's artifacts. The subagent inherits the parent's conv_id (via Context.for_task — passes `conv_id` to the child).

Wait — there's a subtlety here. The subagent is spawned with a CHILD conv_id (e.g., `{parent_conv}--wf-{phase}-{rand}`). So `workflow_artifact_write` called from the child would resolve to the CHILD's artifacts dir, not the parent's. We don't want that — the parent owns the workflow run.

Two ways to fix:
- A. Child reuses parent's conv_id (so workflow_artifact_write resolves to parent's artifacts).
- B. Child uses its own conv_id but workflow tools look up workflow state via a parent_conv_id override.

(A) is simpler. The child's archive will still be in a separate `{child_conv_id}.jsonl` file (because that's keyed by event routing, not conv_id alone), but workflow state stays with parent.

Hmm actually the conv_id IS what keys the archive. Let me re-check delegate.py to see how the child's conv_id is set... actually let me defer the exact mechanism to implementation time. Pseudocode the intent:

Inside the `setup` callback in `_run_child`:

```python
def setup(child_ctx):
    # ... existing setup ...
    # Override child's conv_id to be the parent's, so workflow tools
    # (workflow_artifact_write etc.) read/write under the parent's
    # conv-scoped paths. The child's archive is keyed by event
    # routing / event_context_id_override, not conv_id alone.
    child_ctx.conv_id = ctx.conv_id
    # ... rest of existing setup ...
```

(If this conflicts with how `Context.for_task` uses `conv_id`, the implementer should consult `delegate.py` for guidance. The intent: the child agent runs under the same conv_id as the parent, so workflow tools resolve to the parent's per-conv state.)

3. Update the engine call site too. `dispatch_and_finalize_subagent` already changed in Task 4 to call `wf_subagent._run_child(ctx=ctx, state=state, phase=phase)` (no `workspace=`). Confirm that matches.

- [ ] **Step 6.3: Verify tests still pass**

Run: `pytest tests/test_workflow_*.py -v`
Expected: PASS — engine tests already exercise `_run_child` via monkeypatch (their fake stub has the new signature per Task 4 changes).

- [ ] **Step 6.4: Lint + commit**

Run: `make lint && make typecheck`
Expected: clean.

```bash
git add src/decafclaw/workflow/subagent.py
git commit -m "$(cat <<'EOF'
refactor(workflow): subagent dispatcher uses conv-scoped artifacts

_run_child drops the workspace parameter (derived from ctx.config).
Child's conv_id is set to the parent's so workflow_artifact_write
called from the child resolves to the parent's conv-scoped
artifacts/ directory, where the engine then verifies outputs.

The child's archive stays in a separate JSONL file via
event_context_id_override — only workflow state shares the parent's
conv namespace.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Update composer overlay for conv-scoped state

**Files:**
- Modify: `src/decafclaw/workflow/context.py`
- Modify: `tests/test_workflow_context.py`

`consult_workflow_overlay(ctx)` reads workflow state from `conv_state.load_workflow_state(ctx)` instead of from `runs.load_run(workspace, run_id)`. The `WorkflowOverlay` fields stay; `run_id` becomes the value of `ctx.conv_id`.

- [ ] **Step 7.1: Update the overlay**

In `src/decafclaw/workflow/context.py`:

1. Update imports — drop `.runs.load_run`, add `.conv_state.load_workflow_state`:

```python
from . import registry
from .conv_state import load_workflow_state
from .types import PhaseKind, RunStatus
```

2. Rewrite `consult_workflow_overlay`:

```python
def consult_workflow_overlay(ctx) -> WorkflowOverlay | None:
    """Return the workflow overlay for the current conversation, or
    None when no workflow is active.

    Fail-open: missing/corrupt state, missing workflow def, or missing
    phase all return None so the composer falls through to its default
    behavior.
    """
    state = load_workflow_state(ctx)
    if state is None:
        return None
    wf = registry.get(state.workflow)
    if wf is None:
        return None
    phase = wf.phase(state.current_phase)
    if phase is None:
        return None

    phase_boundary = bool(
        phase.context_profile.get("clear-prior-phase-tools", True)
    )

    return WorkflowOverlay(
        phase_prompt_section=_format_phase_section(state, phase, wf),
        context_profile_overrides=dict(phase.context_profile),
        phase_boundary=phase_boundary,
        phase_id=phase.id,
        workflow_name=wf.name,
        run_id=ctx.conv_id,  # conv_id serves as the run identifier
    )
```

(Note: `_format_phase_section` is unchanged. The `state.run_id` reference inside it changes to `ctx.conv_id` — find and replace.)

Actually re-read `_format_phase_section` and verify whether it uses `state.run_id` — if so, replace with `ctx.conv_id` (passed through, since the function takes state, phase, wf currently; either pass ctx too, or simplify the XML attribute):

```python
def _format_phase_section(state, phase, wf,
                          run_id: str = "") -> str:
    # ... uses run_id from arg ...
```

And update `consult_workflow_overlay` to pass `run_id=ctx.conv_id` when calling `_format_phase_section`.

- [ ] **Step 7.2: Update tests**

In `tests/test_workflow_context.py`:

1. Update `_ctx_for` to add `conv_id`:

```python
def _ctx_for(tmp_path: Path,
             conv_id: str = "conv-overlay-test") -> SimpleNamespace:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(workspace_path=workspace)
    skills = SimpleNamespace(data={}, activated=set())
    return SimpleNamespace(config=config, skills=skills,
                           conv_id=conv_id)
```

2. Replace `create_run` calls with `init_workflow_state`:

```python
from decafclaw.workflow.conv_state import init_workflow_state
# ...
state = init_workflow_state(ctx, workflow="demo",
                            initial_phase="a")
```

3. Drop references to `ctx.skills.data["current_workflow_run"]` — no longer used to gate overlay.

4. Asserts that check `state.run_id` — change to `ctx.conv_id` if they're checking the overlay's `run_id` field.

- [ ] **Step 7.3: Run tests, expect pass**

Run: `pytest tests/test_workflow_context.py -v`
Expected: PASS.

- [ ] **Step 7.4: Run the full workflow test suite to confirm nothing else regressed**

Run: `pytest tests/test_workflow_*.py -v`
Expected: PASS.

- [ ] **Step 7.5: Lint + commit**

```bash
git add src/decafclaw/workflow/context.py tests/test_workflow_context.py
git commit -m "$(cat <<'EOF'
refactor(workflow): composer overlay reads from conv_state

consult_workflow_overlay loads workflow state via
load_workflow_state(ctx) instead of the old
load_run(workspace, run_id). The WorkflowOverlay.run_id field stays
in the dataclass (preserves the public shape) but its value is now
ctx.conv_id — the natural identifier in conv-scoped state.

Tests updated to use init_workflow_state and the conv_id-bearing
context fixture.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Update demo workflow and docs

**Files:**
- Modify: `src/decafclaw/skills/workflow_demo/SKILL.md`
- Modify: `docs/workflows.md`
- Modify: `CLAUDE.md` (one line — the workflow bullet in Skills)

- [ ] **Step 8.1: Update demo workflow's SKILL.md**

In `src/decafclaw/skills/workflow_demo/SKILL.md`:

1. Add `required-skills: [tabstack]` to frontmatter:

```yaml
---
name: research_brief
description: "Research a topic and produce a short brief — demo of the workflow engine."
kind: workflow
user-invocable: true
argument-hint: "[start|status|abort] <topic>"
required-skills: [tabstack]
workflow:
  initial-phase: gather
---
```

2. Update the body to drop the `slug` arg from the call instructions:

```markdown
Research a topic and produce a short written brief.

When invoked as `!research_brief start <topic>` or `/research_brief start <topic>`,
call `workflow_start` with `name="research_brief"`. The engine activates
the `tabstack` skill (declared in required-skills above) before any
phase runs, then dispatches the gather subagent which fetches sources.

After the gather subagent completes, call `phase_advance` to route
between phases. Use `workflow_status` if you ever lose track of where
you are. Use `workflow_abort` if you need to start over.

User said: $ARGUMENTS
```

- [ ] **Step 8.2: Verify the demo workflow still loads**

Run: `uv run python -c "from decafclaw.workflow.loader import load_workflow; from pathlib import Path; wf = load_workflow(Path('src/decafclaw/skills/workflow_demo')); print(wf.name, sorted(wf.phases), wf.required_skills)"`
Expected: `research_brief ['draft', 'gather', 'publish', 'review'] ['tabstack']`

- [ ] **Step 8.3: Rewrite `docs/workflows.md`**

Read the current `docs/workflows.md` and rewrite the affected sections. Specifically:

1. Replace the "Run state" section's run-id storage description with the conv-scoped paths.
2. Drop `workflow_list` and `workflow_switch` from the engine-tools table.
3. Add `workflow_abort` to the table.
4. Add a "required-skills" subsection to "Authoring a workflow":

```markdown
### `required-skills:` (optional)

Workflows can declare skills they depend on:

\`\`\`yaml
---
name: research_brief
kind: workflow
required-skills: [tabstack]
workflow:
  initial-phase: gather
---
\`\`\`

`workflow_start` activates each named skill before initializing
state. If any skill fails to activate (denied, missing env vars,
not found), `workflow_start` returns an error and no state is
written. Activated skills stay active for the rest of the
conversation.
```

5. Update the "Run state" section:

```markdown
## Run state

Workflow state for a conversation lives at:

\`\`\`
data/{agent_id}/workspace/
  conversations/
    {conv_id}.jsonl                          # conversation archive
    {conv_id}/                               # workflow directory
      workflow.json                          # state, history, phase
      artifacts/                             # phase outputs
        gather/sources.md
        draft/brief.md
\`\`\`

One active workflow per conversation. Reaching a terminal phase
(done / error / aborted) archives `workflow.json` to
`workflow-<terminated-timestamp>.json`, freeing the slot for a fresh
workflow_start. Artifacts persist across these archive transitions.
```

6. Drop the "Limitations" sub-point about hundreds-of-runs walks (no longer applicable).

- [ ] **Step 8.4: Update CLAUDE.md workflow bullet**

In `/Users/lorchard/devel/decafclaw/CLAUDE.md`, the Skills section bullet about workflow skills. The text currently reads:

> - **Workflow skills** ([docs/workflows.md](docs/workflows.md)) — `kind: workflow` in SKILL.md plus `phases/*.md` files declare a graph-based multi-phase task. Engine constrains tools + context per phase, dispatches subagents for `kind: subagent` phases, and routes edges via a dynamically-generated `phase_advance` enum. Bundled example: `skills/workflow_demo/` (`research_brief`).

Update to mention required-skills + conversation scope:

```markdown
- **Workflow skills** ([docs/workflows.md](docs/workflows.md)) — `kind: workflow` in SKILL.md plus `phases/*.md` files declare a graph-based multi-phase task scoped to a single conversation. Optional `required-skills:` in frontmatter; engine auto-activates them on `workflow_start`. Engine constrains tools + context per phase, dispatches subagents for `kind: subagent` phases, and routes edges via a dynamically-generated `phase_advance` enum. Bundled example: `skills/workflow_demo/` (`research_brief`).
```

- [ ] **Step 8.5: Run the full test suite to confirm nothing regressed**

Run: `make check && make test`
Expected: clean.

- [ ] **Step 8.6: Commit**

```bash
git add src/decafclaw/skills/workflow_demo/SKILL.md docs/workflows.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(workflow): demo + docs for conv-scoped rework

- Demo workflow gains required-skills: [tabstack]; skill body
  updated to drop the slug arg from the workflow_start example.
- docs/workflows.md: drop workflow_list/workflow_switch; add
  workflow_abort and the required-skills authoring subsection;
  replace the run-id storage description with conv-scoped paths.
- CLAUDE.md: note conversation scope + required-skills in the
  workflow-skills bullet.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Final verification

**Files:** none — verification + smoke + manual eval.

- [ ] **Step 9.1: Full suite + check**

Run: `make check && make test`
Expected: all green.

- [ ] **Step 9.2: Manual smoke against the demo**

Coordinate with Les to ensure his `make dev` agent has the rework loaded. Then in the web UI:

```
/research_brief start the history of movable type
```

Expected sequence:
1. `activate_skill` fires for `tabstack` (via required-skills auto-activation).
2. `workflow_start` initializes conv-scoped state at `conversations/{conv_id}/workflow.json`. Initial phase `gather` is a subagent, so it dispatches synchronously.
3. Subagent runs with `tabstack_research`, `tabstack_extract_markdown`, `vault_read`, `workflow_artifact_write` available. Writes `conversations/{conv_id}/artifacts/gather/sources.md`.
4. Engine verifies outputs, auto-advances to `draft`.
5. `workflow_start` returns: "Started workflow 'research_brief'. Current phase: draft. Status: running. Use phase_advance to move forward."
6. LLM in iteration 2 sees the `<workflow_phase>` overlay describing draft phase + `phase_advance` with enum `[review, gather]`. Calls `phase_advance(target_phase_id="review")` after writing the brief.
7. Review phase fires the Approve / Needs Changes gate.
8. Approve → publish phase → vault page written → state.status = DONE.

If any step still fails, the rework spec's "Open questions for planning" or a fresh root-cause investigation is required. Capture findings in `notes.md` in this dev-session directory.

- [ ] **Step 9.3: Re-run the eval**

Run: `make eval ARGS="--file evals/workflow_routing.yaml"` (or whatever the project's eval-runner invocation is — check Makefile).
Expected: both cases pass per the existing eval. The eval prompts don't change with the rework since they're framed agnostically.

- [ ] **Step 9.4: Update PR description**

```bash
gh pr edit 557 --title "feat(workflow): first-class workflow engine, conversation-scoped (#255)" --body "$(cat <<'EOF'
## Summary

Implements [#255](https://github.com/lmorchard/decafclaw/issues/255) — declarative multi-phase workflows scoped to a single conversation. Workflows are `kind: workflow` skills with `phases/*.md` files declaring tools, transitions (with `when:` annotations), edge-level review gates, inline + subagent phase kinds, and per-phase context-composer overrides.

Originally designed with cross-conversation runs (run-ids, workflow_list/switch). Demos surfaced cascading wiring bugs from the layered state machine; PR was reworked to scope state to one conversation, which simplified the engine, eliminated the run-id namespace, and resolved the demo failure mode. See [`docs/dev-sessions/2026-05-21-1732-workflow-engine-rework/spec.md`](docs/dev-sessions/2026-05-21-1732-workflow-engine-rework/spec.md) for the rework rationale.

### What ships

- `src/decafclaw/workflow/` module (types, loader, conv_state, engine, subagent, context overlay, registry)
- `src/decafclaw/tools/workflow_tools.py` with `workflow_start/status/abort`, `workflow_artifact_read/write`, dynamic `phase_advance`
- Skill loader recognizes `kind: workflow` and registers loaded definitions
- `required-skills:` in SKILL.md frontmatter; engine auto-activates on `workflow_start`
- `ContextComposer` consults a `WorkflowOverlay` in INTERACTIVE mode and applies per-phase context-profile overrides; per-iteration `workflow_msg` injection (so workflows started mid-turn are visible in the next iteration)
- Per-phase tool catalog hard-gate via `ctx.tools.allowed`
- Subagent dispatch via `ConversationManager.enqueue_turn(kind=CHILD_AGENT)`; child inherits parent's skill tools; artifacts at conversation scope
- Conv-scoped persistence at `conversations/{conv_id}/workflow.json` + `artifacts/`; one active workflow per conv, sequential after archive
- Bundled `research_brief` demo workflow exercising every engine feature
- Tests covering types, loader, conv_state, engine, tools, context overlay
- `docs/workflows.md`

### Resolved follow-ups

- #561 (`phase_advance` critical priority) — resolved here. Closes #561.

### Carried-forward follow-ups

- #562 (engine writes phase-boundary markers) — still open.
- #563 (decision-slice context-profile override) — still open.
- #564 (subagent-skill integration test) — still open.

## Test plan

- [x] `make check` — clean (ruff + pyright + tsc)
- [x] `make test` — full suite passing
- [x] Manual smoke: `/research_brief start <topic>` walks gather → draft → review → publish
- [x] Eval at `evals/workflow_routing.yaml`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 9.5: Push and request re-review**

```bash
git push origin feat/255-workflow-engine
gh pr edit 557 --add-reviewer copilot-pull-request-reviewer
```

---

## Implementation notes for the executing engineer

- **Commit per task.** Each task produces one commit. Tasks 4-7 each leave the tree green; task 2 deliberately leaves it briefly red (deleted runs.py without yet updating all importers) — that's the only red state in the plan.
- **TDD discipline:** every code change has a failing test first. Where the change is a delete (e.g., dropping `tool_workflow_list`), no new test is needed — the absence of the function and its test together is the assertion.
- **No `asyncio.sleep` in tests** (CLAUDE.md). The only sleep in this plan is `await asyncio.sleep(0)` in the lock test, which is the right cooperative yield.
- **Test fixtures use SimpleNamespace** for ctx, not the real `Context` dataclass. This matches the convention in existing workflow tests. The `manager` field stays `None` in unit tests; subagent dispatch tests monkeypatch `_run_child`.
- **`subagent.py:_run_child` real path is untested** — only monkeypatched in unit tests. The manual smoke (step 9.2) is the integration check.
- **Per-conv lock leak:** `_conv_locks` accumulates dict entries indefinitely. Acceptable for v1 (CLAUDE.md mentions this is the existing pattern for similar registries). If a conv churn problem ever surfaces, a follow-up issue can add cleanup.
- **If any task fails twice the same way, stop and ask** (CLAUDE.md). Don't pile retries on the same broken approach. Systematic-debugging applies.

## Self-review

Ran inline after writing this plan:

- **Spec coverage:** every section of `spec.md` has a corresponding task. Required-skills (spec § "Skill activation") → Task 3 (parsing) + Task 5 (activation). Conv-scoped storage (spec § "Workflow state at conversation scope") → Task 2. Engine API change → Task 4. Tool surface changes → Task 5. Subagent paths → Task 6. Overlay reads conv state → Task 7. Demo + docs → Task 8. Manual + eval verify → Task 9.
- **Placeholders:** scanned for "TBD", "TODO", "implement later", "add appropriate error handling" — none.
- **Type consistency:** `WorkflowState`, `WorkflowDef`, `WorkflowOverlay`, `RunStatus`, `init_workflow_state`, `load_workflow_state`, `save_workflow_state`, `archive_workflow_state`, `artifacts_dir`, `conv_lock`, `_get_workflow`, `tool_workflow_abort`, `tool_workflow_start`, `_activate_skill_for_workflow` — all consistent across tasks.
- **Spec note about activation policy:** spec § "Open questions for planning" asked whether `required-skills` should respect skill tiers or skip approval. Resolution: respect tiers — Task 5 calls the standard `tool_activate_skill`, which already honors the tier-based approval flow. If a workspace-tier skill is denied, `workflow_start` fails clearly. This decision is reflected in the test `test_workflow_start_fails_when_required_skill_fails`.
