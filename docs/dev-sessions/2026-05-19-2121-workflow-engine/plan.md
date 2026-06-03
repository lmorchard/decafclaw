# Workflow Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-class workflow engine (issue #255) — declarative multi-phase workflows authored as `kind: workflow` skills with `phases/*.md` files, graph-based routing via dynamic `phase_advance` enum, edge-level review gates, inline + subagent phase kinds, and per-phase ContextComposer overrides.

**Architecture:** New `src/decafclaw/workflow/` module with types + loader + runs + engine + composer overlay. Always-loaded engine tools in `src/decafclaw/tools/workflow_tools.py`. Skill loader gains a `kind: workflow` branch. ContextComposer gains a workflow-overlay consult. Subagent dispatch builds on `delegate.py`'s primitives. A small demo workflow ships alongside as the v1 dogfood.

**Tech Stack:** Python 3.x, dataclasses, pyyaml, asyncio, pytest (parallel via pytest-xdist).

**Spec:** [`spec.md`](spec.md) in this directory.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `src/decafclaw/workflow/__init__.py` | Package init, re-exports |
| `src/decafclaw/workflow/types.py` | `WorkflowDef`, `PhaseDef`, `EdgeDef`, `GateDef`, `RunState` dataclasses |
| `src/decafclaw/workflow/loader.py` | Parse SKILL.md + `phases/*.md` → `WorkflowDef`; load-time validation |
| `src/decafclaw/workflow/runs.py` | `RunState` persistence: create / load / save / list / switch / atomic write |
| `src/decafclaw/workflow/engine.py` | Transition logic, gate dispatch, subagent dispatch, state-machine ops |
| `src/decafclaw/workflow/subagent.py` | Workflow-aware subagent dispatcher built on `delegate.py` primitives |
| `src/decafclaw/workflow/context.py` | `WorkflowOverlay` for `ContextComposer.compose()` integration |
| `src/decafclaw/workflow/registry.py` | In-memory registry of loaded `WorkflowDef`s + lookup; called from skill loader branch |
| `src/decafclaw/tools/workflow_tools.py` | Always-loaded engine tools: start, list, switch, status, advance, artifact_*; also exports the per-turn dynamic-refresh entrypoint |
| `src/decafclaw/skills/workflow_demo/SKILL.md` | Demo workflow shell (exact name TBD during Task 8) |
| `src/decafclaw/skills/workflow_demo/phases/*.md` | Demo workflow phase files |
| `docs/workflows.md` | Author-facing documentation: schema, file layout, authoring conventions, examples |
| `tests/test_workflow_types.py` | Dataclass round-trip + equality tests |
| `tests/test_workflow_loader.py` | Loader + validation tests (valid loads + every failure mode) |
| `tests/test_workflow_runs.py` | Run state persistence + listing + atomic write tests |
| `tests/test_workflow_engine.py` | Transition flow tests (no-gate, gate approve/deny, subagent advance, missing outputs, terminal) |
| `tests/test_workflow_tools.py` | Tool surface tests including dynamic `phase_advance` enum regeneration |
| `tests/test_workflow_context.py` | `WorkflowOverlay` + composer override flow tests |
| `tests/test_workflow_skill_loader.py` | Skill-loader `kind: workflow` branch tests |
| `evals/workflow_routing.yaml` | One eval case: 2-edge phase with distinct `when:` clauses, agent picks correct target |

### Modified files

| Path | Change |
|---|---|
| `src/decafclaw/skills/__init__.py` | Add `kind: workflow` branch in `parse_skill_md()`; new `WorkflowSkillInfo` companion or flag on `SkillInfo` |
| `src/decafclaw/tool_definitions.py` | Call workflow's per-turn refresh after the skill dynamic-providers loop in `refresh_dynamic_tools()` |
| `src/decafclaw/context_composer.py` | Add `_consult_workflow_overlay()` call in `compose()`; apply overlay overrides in the existing branches (memory, vault, notes, decision slice) |
| `src/decafclaw/context_cleanup.py` | Add `clear_tool_results_in_range(history, start_idx, end_idx, preserve_tools)` for phase-boundary clearing |
| `src/decafclaw/tools/__init__.py` | Merge `WORKFLOW_TOOLS` + `WORKFLOW_TOOL_DEFINITIONS` into the global registry dicts |
| `docs/index.md` | Link the new `docs/workflows.md` page |
| `CLAUDE.md` | One-paragraph workflow-engine convention block in the "Skills" section with a pointer to `docs/workflows.md` |

---

## Task 1: Workflow data types

**Files:**
- Create: `src/decafclaw/workflow/__init__.py`
- Create: `src/decafclaw/workflow/types.py`
- Create: `tests/test_workflow_types.py`

The data types are pure dataclasses — no I/O, no engine logic. Foundational.

- [ ] **Step 1.1: Create the package init**

```python
# src/decafclaw/workflow/__init__.py
"""Workflow engine — declarative multi-phase agent workflows.

See docs/workflows.md for the author-facing schema and conventions.
"""
```

- [ ] **Step 1.2: Write failing tests for the dataclasses**

```python
# tests/test_workflow_types.py
"""Tests for workflow dataclass shapes and round-trips."""

import json

from decafclaw.workflow.types import (
    EdgeDef,
    GateDef,
    PhaseDef,
    PhaseKind,
    RunState,
    RunStatus,
    WorkflowDef,
)


def test_phase_kind_values():
    assert PhaseKind.INLINE.value == "inline"
    assert PhaseKind.SUBAGENT.value == "subagent"


def test_run_status_values():
    assert {s.value for s in RunStatus} == {
        "running", "paused-gate", "paused-subagent", "done", "error"
    }


def test_phase_def_minimal():
    phase = PhaseDef(
        id="draft",
        kind=PhaseKind.INLINE,
        prompt="Write the draft.",
        tools=["vault_write"],
        next_phases=[EdgeDef(id="review", when="ready", gate=None)],
        gate=None,  # legacy hook; phase-level gates not supported
        outputs=(),
        subagent_skill=None,
        context_profile={},
    )
    assert phase.id == "draft"
    assert phase.is_terminal is False


def test_phase_def_terminal():
    phase = PhaseDef(
        id="publish",
        kind=PhaseKind.INLINE,
        prompt="Publish.",
        tools=[],
        next_phases=[],
        gate=None,
        outputs=(),
        subagent_skill=None,
        context_profile={},
    )
    assert phase.is_terminal is True


def test_edge_def_with_gate():
    gate = GateDef(
        type="review",
        message="Approve?",
        approve_label="Yes",
        deny_label="No",
        on_deny="draft",
    )
    edge = EdgeDef(id="publish", when="approved", gate=gate)
    assert edge.gate is gate


def test_run_state_json_round_trip():
    state = RunState(
        workflow="weeknotes",
        slug="w20",
        run_id="2026-05-19-1402-weeknotes-w20",
        status=RunStatus.RUNNING,
        current_phase="draft",
        created_at="2026-05-19T14:02:00+00:00",
        updated_at="2026-05-19T14:35:12+00:00",
        history=[
            {"from": None, "to": "gather", "edge_index": None,
             "gate_response": None, "reason": "initial",
             "timestamp": "2026-05-19T14:02:00+00:00"}
        ],
        pending_gate=None,
        pending_subagent=None,
        error=None,
    )
    raw = state.to_json()
    parsed = json.loads(raw)
    assert parsed["workflow"] == "weeknotes"
    assert parsed["status"] == "running"
    back = RunState.from_json(raw)
    assert back == state


def test_workflow_def_lookup_phase():
    p1 = PhaseDef(
        id="a", kind=PhaseKind.INLINE, prompt="", tools=[],
        next_phases=[EdgeDef(id="b", when="", gate=None)],
        gate=None, outputs=(), subagent_skill=None, context_profile={},
    )
    p2 = PhaseDef(
        id="b", kind=PhaseKind.INLINE, prompt="", tools=[],
        next_phases=[], gate=None, outputs=(),
        subagent_skill=None, context_profile={},
    )
    wf = WorkflowDef(
        name="t", description="d", initial_phase="a",
        phases={"a": p1, "b": p2},
        user_invocable=True, argument_hint="",
    )
    assert wf.phase("a") is p1
    assert wf.phase("missing") is None
```

- [ ] **Step 1.3: Run tests, expect ImportError**

Run: `pytest tests/test_workflow_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'decafclaw.workflow.types'`

- [ ] **Step 1.4: Implement the types**

```python
# src/decafclaw/workflow/types.py
"""Workflow engine dataclasses.

Pure data — no I/O, no engine logic. Loader builds these from SKILL.md
+ phases/*.md; engine consumes them. Round-trip-safe via to_json /
from_json on RunState.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum


class PhaseKind(str, Enum):
    INLINE = "inline"
    SUBAGENT = "subagent"


class RunStatus(str, Enum):
    RUNNING = "running"
    PAUSED_GATE = "paused-gate"
    PAUSED_SUBAGENT = "paused-subagent"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True)
class GateDef:
    """A user-facing confirmation that mediates an edge transition."""

    type: str = "review"   # only "review" in v1
    message: str = ""
    approve_label: str = "Approve"
    deny_label: str = "Deny"
    on_deny: str = ""      # phase id; empty = stay in current phase


@dataclass(frozen=True)
class EdgeDef:
    """A directed edge out of a phase.

    The agent picks an edge via phase_advance(target_phase_id, ...).
    If gate is set, the engine fires the confirmation and routes:
    on approve → edge.id; on deny → gate.on_deny (or current phase if
    empty).
    """

    id: str          # target phase id
    when: str = ""   # LLM-facing routing annotation
    gate: GateDef | None = None


@dataclass(frozen=True)
class PhaseDef:
    """A single phase in a workflow definition."""

    id: str
    kind: PhaseKind
    prompt: str            # body of phases/{id}.md (or unused if subagent_skill set)
    tools: list[str]       # glob patterns
    next_phases: list[EdgeDef]
    gate: None             # placeholder — phase-level gates not supported in v1
    outputs: tuple[str, ...]  # required artifact filenames for subagent phases
    subagent_skill: str | None  # if set, child boots this skill instead of inline prompt
    context_profile: dict  # raw dict of context-profile keys (e.g. memory-retrieval: off)

    @property
    def is_terminal(self) -> bool:
        return not self.next_phases


@dataclass(frozen=True)
class WorkflowDef:
    """A loaded workflow definition. Built by loader.py, consumed by engine."""

    name: str
    description: str
    initial_phase: str
    phases: dict[str, PhaseDef]
    user_invocable: bool
    argument_hint: str

    def phase(self, phase_id: str) -> PhaseDef | None:
        return self.phases.get(phase_id)


@dataclass
class RunState:
    """A workflow run's durable state — serialized to state.json."""

    workflow: str
    slug: str
    run_id: str
    status: RunStatus
    current_phase: str
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601
    history: list[dict] = field(default_factory=list)
    pending_gate: dict | None = None       # {edge_target, on_deny} during paused-gate
    pending_subagent: dict | None = None   # {phase, dispatched_at} during paused-subagent
    error: str | None = None

    def to_json(self) -> str:
        d = asdict(self)
        d["status"] = self.status.value
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> RunState:
        d = json.loads(raw)
        d["status"] = RunStatus(d["status"])
        return cls(**d)
```

- [ ] **Step 1.5: Run tests, expect pass**

Run: `pytest tests/test_workflow_types.py -v`
Expected: PASS — 7 passed

- [ ] **Step 1.6: Lint + commit**

Run: `make lint`
Expected: clean

```bash
git add src/decafclaw/workflow/__init__.py src/decafclaw/workflow/types.py tests/test_workflow_types.py
git commit -m "$(cat <<'EOF'
feat(workflow): workflow engine dataclasses

Foundational types for the workflow engine: PhaseKind / RunStatus
enums; GateDef / EdgeDef / PhaseDef / WorkflowDef as frozen
dataclasses; RunState with JSON round-trip for state.json persistence.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Run state persistence

**Files:**
- Create: `src/decafclaw/workflow/runs.py`
- Create: `tests/test_workflow_runs.py`

Persistence layer for runs — atomic writes, listing across `workspace/workflows/*/runs/`, per-run lock registry.

- [ ] **Step 2.1: Write failing tests**

```python
# tests/test_workflow_runs.py
"""Tests for workflow run persistence and discovery."""

import asyncio
import json
from pathlib import Path

import pytest

from decafclaw.workflow.runs import (
    create_run,
    list_runs,
    load_run,
    save_run,
    run_lock,
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
```

- [ ] **Step 2.2: Run tests, expect ImportError**

Run: `pytest tests/test_workflow_runs.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 2.3: Implement runs.py**

```python
# src/decafclaw/workflow/runs.py
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
    if run_dir.exists():
        # Add seconds to avoid collisions when starting multiple runs
        # within the same minute
        secs = datetime.now(timezone.utc).strftime("%S")
        run_id = f"{ts}{secs}-{workflow}-{slug}"
        run_dir = _run_dir(workspace, workflow, run_id)
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
    tmp = path.with_suffix(".json.tmp")
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
# held by run_id. Cleanup is bounded by the run's lifetime — runs are
# few enough that we don't need to GC.
_run_locks: dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def run_lock(run_id: str):
    """Async context manager that serializes operations on a single run."""
    lock = _run_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        yield
```

- [ ] **Step 2.4: Run tests, expect pass**

Run: `pytest tests/test_workflow_runs.py -v`
Expected: PASS — 8 passed

- [ ] **Step 2.5: Lint + commit**

Run: `make lint`
Expected: clean

```bash
git add src/decafclaw/workflow/runs.py tests/test_workflow_runs.py
git commit -m "$(cat <<'EOF'
feat(workflow): run state persistence and discovery

Per-workflow sub-workspace at workspace/workflows/{name}/runs/{run-id}/
with atomic state.json writes, transition history, and per-run
asyncio.Lock for serializing concurrent advances. list_runs walks
all workflows; corrupt state.json files are dropped with a warning.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Loader + validation

**Files:**
- Create: `src/decafclaw/workflow/loader.py`
- Create: `src/decafclaw/workflow/registry.py`
- Create: `tests/test_workflow_loader.py`

Parser that turns `SKILL.md` + `phases/*.md` into a `WorkflowDef`, with strict load-time validation. Registry is a small global dict keyed by workflow name.

- [ ] **Step 3.1: Write failing tests covering happy path + every validation gate**

```python
# tests/test_workflow_loader.py
"""Tests for the workflow loader and validation gates."""

from pathlib import Path

import pytest

from decafclaw.workflow.loader import (
    LoaderError,
    load_workflow,
)
from decafclaw.workflow.types import PhaseKind


def _write_workflow(root: Path, files: dict[str, str]) -> Path:
    skill_dir = root / "skill"
    skill_dir.mkdir()
    phases_dir = skill_dir / "phases"
    phases_dir.mkdir()
    for relpath, content in files.items():
        target = skill_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return skill_dir


_SKILL_MD = """---
name: demo
description: test workflow
kind: workflow
user-invocable: true
workflow:
  initial-phase: gather
---
body
"""

_GATHER = """---
kind: subagent
tools: [vault_read]
outputs: [sources.md]
next-phases:
  - id: draft
---
gather prompt
"""

_DRAFT = """---
kind: inline
tools: [vault_write]
next-phases:
  - id: review
    when: ready
---
draft prompt
"""

_REVIEW = """---
kind: inline
tools: [vault_read]
next-phases:
  - id: publish
    when: approved
    gate:
      type: review
      message: "Approve?"
      approve-label: "Yes"
      deny-label: "No"
      on-deny: draft
---
review prompt
"""

_PUBLISH = """---
kind: inline
tools: [vault_write]
---
publish prompt
"""


def test_load_happy_path(tmp_path):
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": _GATHER,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    wf = load_workflow(d)
    assert wf.name == "demo"
    assert wf.initial_phase == "gather"
    assert set(wf.phases) == {"gather", "draft", "review", "publish"}
    assert wf.phases["gather"].kind == PhaseKind.SUBAGENT
    assert wf.phases["gather"].outputs == ("sources.md",)
    assert wf.phases["draft"].next_phases[0].id == "review"
    assert wf.phases["draft"].next_phases[0].when == "ready"
    review_edge = wf.phases["review"].next_phases[0]
    assert review_edge.gate is not None
    assert review_edge.gate.on_deny == "draft"
    assert wf.phases["publish"].is_terminal


def test_load_fails_when_initial_phase_missing(tmp_path):
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD.replace("initial-phase: gather",
                                       "initial-phase: nope"),
        "phases/gather.md": _GATHER,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="initial-phase"):
        load_workflow(d)


def test_load_fails_when_edge_target_undefined(tmp_path):
    bad_draft = _DRAFT.replace("- id: review", "- id: ghost")
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": _GATHER,
        "phases/draft.md": bad_draft,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="ghost"):
        load_workflow(d)


def test_load_fails_when_multi_edge_missing_when(tmp_path):
    bad_draft = """---
kind: inline
tools: [vault_write]
next-phases:
  - id: review
  - id: publish
---
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": _GATHER,
        "phases/draft.md": bad_draft,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="when"):
        load_workflow(d)


def test_load_fails_when_subagent_has_multiple_edges(tmp_path):
    bad_gather = """---
kind: subagent
tools: [vault_read]
outputs: [sources.md]
next-phases:
  - id: draft
    when: usually
  - id: review
    when: short-circuit
---
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": bad_gather,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="subagent"):
        load_workflow(d)


def test_load_fails_when_subagent_has_gated_edge(tmp_path):
    bad_gather = """---
kind: subagent
tools: [vault_read]
outputs: [sources.md]
next-phases:
  - id: draft
    gate:
      type: review
      message: "Approve?"
---
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": bad_gather,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="gate"):
        load_workflow(d)


def test_load_fails_when_subagent_missing_outputs(tmp_path):
    bad_gather = """---
kind: subagent
tools: [vault_read]
next-phases:
  - id: draft
---
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": bad_gather,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="outputs"):
        load_workflow(d)


def test_load_fails_when_gate_on_deny_undefined(tmp_path):
    bad_review = _REVIEW.replace("on-deny: draft", "on-deny: ghost")
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": _GATHER,
        "phases/draft.md": _DRAFT,
        "phases/review.md": bad_review,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="on-deny.*ghost"):
        load_workflow(d)


def test_load_fails_when_no_phases_directory(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SKILL_MD)
    with pytest.raises(LoaderError, match="phases"):
        load_workflow(skill_dir)


def test_load_subagent_skill_escape_hatch(tmp_path):
    gather_with_skill = """---
kind: subagent
subagent-skill: my-worker
outputs: [report.md]
next-phases:
  - id: draft
---
unused body
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": gather_with_skill,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    wf = load_workflow(d)
    assert wf.phases["gather"].subagent_skill == "my-worker"
```

- [ ] **Step 3.2: Run tests, expect ImportError**

Run: `pytest tests/test_workflow_loader.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3.3: Implement loader.py**

```python
# src/decafclaw/workflow/loader.py
"""Parse a workflow skill directory (SKILL.md + phases/*.md) into a
WorkflowDef. Strict validation at load time — invalid workflows raise
LoaderError. The skill loader catches LoaderError, logs a warning, and
skips the workflow.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .types import (
    EdgeDef,
    GateDef,
    PhaseDef,
    PhaseKind,
    WorkflowDef,
)

log = logging.getLogger(__name__)


class LoaderError(ValueError):
    """A workflow definition failed validation at load time."""


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body. Raises if missing."""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        raise LoaderError("missing YAML frontmatter")
    body_start = stripped.find("\n", 3)
    end = stripped.find("\n---", body_start)
    if end == -1:
        raise LoaderError("unterminated YAML frontmatter")
    fm_str = stripped[3:end].strip()
    body = stripped[end + 4:].lstrip("\n")
    try:
        meta = yaml.safe_load(fm_str) or {}
    except yaml.YAMLError as exc:
        raise LoaderError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise LoaderError("frontmatter must be a mapping")
    return meta, body


def _parse_gate(raw: dict) -> GateDef:
    return GateDef(
        type=raw.get("type", "review"),
        message=raw.get("message", ""),
        approve_label=raw.get("approve-label", "Approve"),
        deny_label=raw.get("deny-label", "Deny"),
        on_deny=raw.get("on-deny", ""),
    )


def _parse_edges(raw: list, phase_id: str) -> list[EdgeDef]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise LoaderError(
            f"phase '{phase_id}': next-phases must be a list")
    edges: list[EdgeDef] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise LoaderError(
                f"phase '{phase_id}': next-phases[{i}] must be a mapping")
        target = entry.get("id")
        if not target:
            raise LoaderError(
                f"phase '{phase_id}': next-phases[{i}] missing 'id'")
        gate_raw = entry.get("gate")
        gate = _parse_gate(gate_raw) if gate_raw else None
        edges.append(EdgeDef(
            id=target,
            when=entry.get("when", "") or "",
            gate=gate,
        ))
    return edges


def _parse_phase(path: Path) -> PhaseDef:
    phase_id = path.stem
    if not phase_id.replace("_", "").replace("-", "").isalnum() \
            or not phase_id[0].isalpha():
        raise LoaderError(
            f"phase '{phase_id}': id must be [a-z][a-z0-9_-]*")
    text = path.read_text()
    meta, body = _split_frontmatter(text)

    kind_raw = meta.get("kind", "inline")
    try:
        kind = PhaseKind(kind_raw)
    except ValueError:
        raise LoaderError(
            f"phase '{phase_id}': unknown kind '{kind_raw}'") from None

    tools_raw = meta.get("tools") or []
    if not isinstance(tools_raw, list):
        raise LoaderError(
            f"phase '{phase_id}': tools must be a list")
    tools = [str(t) for t in tools_raw]

    outputs_raw = meta.get("outputs") or []
    if not isinstance(outputs_raw, list):
        raise LoaderError(
            f"phase '{phase_id}': outputs must be a list")
    outputs = tuple(str(o) for o in outputs_raw)

    edges = _parse_edges(meta.get("next-phases") or [], phase_id)
    context_profile = meta.get("context-profile") or {}
    if not isinstance(context_profile, dict):
        raise LoaderError(
            f"phase '{phase_id}': context-profile must be a mapping")
    subagent_skill = meta.get("subagent-skill")

    return PhaseDef(
        id=phase_id,
        kind=kind,
        prompt=body.strip(),
        tools=tools,
        next_phases=edges,
        gate=None,
        outputs=outputs,
        subagent_skill=subagent_skill,
        context_profile=context_profile,
    )


def load_workflow(skill_dir: Path) -> WorkflowDef:
    """Load a workflow from a skill directory.

    Raises LoaderError if anything is invalid. The skill loader calls
    this and catches LoaderError to log + skip bad workflows.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise LoaderError(f"missing SKILL.md in {skill_dir}")
    meta, _body = _split_frontmatter(skill_md.read_text())

    name = meta.get("name")
    description = meta.get("description", "")
    if not name:
        raise LoaderError("SKILL.md missing 'name'")
    if meta.get("kind") != "workflow":
        raise LoaderError("SKILL.md kind must be 'workflow'")

    wf_block = meta.get("workflow") or {}
    if not isinstance(wf_block, dict):
        raise LoaderError("workflow: block must be a mapping")
    initial = wf_block.get("initial-phase")
    if not initial:
        raise LoaderError("workflow.initial-phase is required")

    phases_dir = skill_dir / "phases"
    if not phases_dir.is_dir():
        raise LoaderError(
            f"missing phases/ directory in {skill_dir}")

    phases: dict[str, PhaseDef] = {}
    for phase_file in sorted(phases_dir.glob("*.md")):
        phase = _parse_phase(phase_file)
        if phase.id in phases:
            raise LoaderError(
                f"duplicate phase id '{phase.id}'")
        phases[phase.id] = phase

    if not phases:
        raise LoaderError("no phase files found in phases/")
    if initial not in phases:
        raise LoaderError(
            f"workflow.initial-phase '{initial}' is not defined")

    _validate_phases(phases)

    return WorkflowDef(
        name=name,
        description=description,
        initial_phase=initial,
        phases=phases,
        user_invocable=bool(meta.get("user-invocable", False)),
        argument_hint=meta.get("argument-hint", ""),
    )


def _validate_phases(phases: dict[str, PhaseDef]) -> None:
    for phase in phases.values():
        # Edge targets resolve
        for edge in phase.next_phases:
            if edge.id not in phases:
                raise LoaderError(
                    f"phase '{phase.id}': edge target '{edge.id}' is "
                    "not defined")
            if edge.gate and edge.gate.on_deny \
                    and edge.gate.on_deny not in phases:
                raise LoaderError(
                    f"phase '{phase.id}': gate on-deny '{edge.gate.on_deny}'"
                    f" is not defined")
        # Multi-edge must have when: on every edge
        if len(phase.next_phases) > 1:
            for edge in phase.next_phases:
                if not edge.when.strip():
                    raise LoaderError(
                        f"phase '{phase.id}': multi-edge phases require "
                        f"'when:' on every edge (missing on '{edge.id}')")
        # Subagent constraints
        if phase.kind == PhaseKind.SUBAGENT:
            if phase.subagent_skill is None and not phase.outputs:
                raise LoaderError(
                    f"phase '{phase.id}': subagent phases require "
                    "'outputs:' (or a subagent-skill: that owns its own "
                    "output contract)")
            if len(phase.next_phases) > 1:
                raise LoaderError(
                    f"phase '{phase.id}': subagent phases must have "
                    "exactly one next-phases edge (no agent choice)")
            for edge in phase.next_phases:
                if edge.gate is not None:
                    raise LoaderError(
                        f"phase '{phase.id}': subagent phases cannot "
                        "have gated edges (gates are user-facing)")
```

- [ ] **Step 3.4: Implement registry.py**

```python
# src/decafclaw/workflow/registry.py
"""In-memory registry of loaded workflow definitions.

Populated by the skill loader branch when it sees `kind: workflow`.
Consumed by the engine and the workflow tools to find a WorkflowDef
by name.
"""

from __future__ import annotations

from .types import WorkflowDef

_REGISTRY: dict[str, WorkflowDef] = {}


def register(wf: WorkflowDef) -> None:
    _REGISTRY[wf.name] = wf


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def get(name: str) -> WorkflowDef | None:
    return _REGISTRY.get(name)


def all_workflows() -> list[WorkflowDef]:
    return list(_REGISTRY.values())


def clear() -> None:
    """Test-only: reset the registry. Production code should not call."""
    _REGISTRY.clear()
```

- [ ] **Step 3.5: Run tests, expect pass**

Run: `pytest tests/test_workflow_loader.py -v`
Expected: PASS — 10 passed

- [ ] **Step 3.6: Lint + commit**

Run: `make lint`
Expected: clean

```bash
git add src/decafclaw/workflow/loader.py src/decafclaw/workflow/registry.py tests/test_workflow_loader.py
git commit -m "$(cat <<'EOF'
feat(workflow): SKILL.md + phases/ loader with strict validation

Loader parses kind:workflow skills into WorkflowDef. Validation rejects
missing initial-phase, undefined edge targets, multi-edge phases
without when:, subagent phases with multiple edges, subagent phases
with gated edges, subagent phases without outputs:, and undefined
gate on-deny targets. In-memory registry by workflow name.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Engine — transitions, gates, subagent dispatch

**Files:**
- Create: `src/decafclaw/workflow/engine.py`
- Create: `src/decafclaw/workflow/subagent.py`
- Create: `tests/test_workflow_engine.py`

The transition core. `engine.py` owns the state-machine ops; `subagent.py` adapts `delegate.py` primitives for workflow-aware child dispatch.

- [ ] **Step 4.1: Write failing tests for transitions, gates, and subagent dispatch**

```python
# tests/test_workflow_engine.py
"""Tests for workflow engine transitions and dispatch."""

import asyncio
from pathlib import Path

import pytest

from decafclaw.media import EndTurnConfirm, ToolResult
from decafclaw.workflow import registry
from decafclaw.workflow.engine import (
    AdvanceResult,
    advance,
    finalize_gate_response,
    verify_subagent_outputs,
)
from decafclaw.workflow.runs import create_run, load_run
from decafclaw.workflow.types import (
    EdgeDef,
    GateDef,
    PhaseDef,
    PhaseKind,
    RunStatus,
    WorkflowDef,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


def _simple_workflow(name: str = "demo") -> WorkflowDef:
    return WorkflowDef(
        name=name,
        description="",
        initial_phase="a",
        phases={
            "a": PhaseDef(
                id="a", kind=PhaseKind.INLINE, prompt="A",
                tools=[],
                next_phases=[EdgeDef(id="b", when="", gate=None)],
                gate=None, outputs=(), subagent_skill=None,
                context_profile={},
            ),
            "b": PhaseDef(
                id="b", kind=PhaseKind.INLINE, prompt="B",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=False, argument_hint="",
    )


def _gated_workflow() -> WorkflowDef:
    gate = GateDef(type="review", message="?", on_deny="a")
    return WorkflowDef(
        name="gated", description="", initial_phase="a",
        phases={
            "a": PhaseDef(
                id="a", kind=PhaseKind.INLINE, prompt="A",
                tools=[],
                next_phases=[EdgeDef(id="b", when="ok", gate=gate)],
                gate=None, outputs=(), subagent_skill=None,
                context_profile={},
            ),
            "b": PhaseDef(
                id="b", kind=PhaseKind.INLINE, prompt="B",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=False, argument_hint="",
    )


def _subagent_workflow() -> WorkflowDef:
    return WorkflowDef(
        name="sub", description="", initial_phase="g",
        phases={
            "g": PhaseDef(
                id="g", kind=PhaseKind.SUBAGENT,
                prompt="gather",
                tools=["vault_read"],
                next_phases=[EdgeDef(id="d", when="", gate=None)],
                gate=None, outputs=("sources.md",),
                subagent_skill=None, context_profile={},
            ),
            "d": PhaseDef(
                id="d", kind=PhaseKind.INLINE, prompt="draft",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=False, argument_hint="",
    )


@pytest.mark.asyncio
async def test_advance_simple_no_gate(tmp_path: Path):
    wf = _simple_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="demo", slug="x",
                       initial_phase="a")

    result = await advance(ws, state, target="b", reason="done")
    assert isinstance(result, AdvanceResult)
    assert result.new_phase == "b"
    assert result.end_turn_signal is None
    reloaded = load_run(ws, state.run_id)
    assert reloaded.current_phase == "b"
    assert reloaded.status == RunStatus.DONE  # b is terminal
    assert reloaded.history[-1]["from"] == "a"
    assert reloaded.history[-1]["to"] == "b"
    assert reloaded.history[-1]["reason"] == "done"


@pytest.mark.asyncio
async def test_advance_rejects_invalid_target(tmp_path: Path):
    wf = _simple_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="demo", slug="x",
                       initial_phase="a")

    with pytest.raises(ValueError, match="not a valid next phase"):
        await advance(ws, state, target="ghost", reason="")
    # State unchanged
    reloaded = load_run(ws, state.run_id)
    assert reloaded.current_phase == "a"


@pytest.mark.asyncio
async def test_advance_with_gate_returns_end_turn_confirm(tmp_path: Path):
    wf = _gated_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="gated", slug="x",
                       initial_phase="a")

    result = await advance(ws, state, target="b", reason="ok")
    assert isinstance(result.end_turn_signal, EndTurnConfirm)
    # State should be paused-gate, current phase still 'a'
    reloaded = load_run(ws, state.run_id)
    assert reloaded.status == RunStatus.PAUSED_GATE
    assert reloaded.current_phase == "a"
    assert reloaded.pending_gate == {"edge_target": "b", "on_deny": "a"}


@pytest.mark.asyncio
async def test_finalize_gate_approve(tmp_path: Path):
    wf = _gated_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="gated", slug="x",
                       initial_phase="a")
    await advance(ws, state, target="b", reason="ok")
    state = load_run(ws, state.run_id)

    await finalize_gate_response(ws, state, approved=True)
    reloaded = load_run(ws, state.run_id)
    assert reloaded.current_phase == "b"
    assert reloaded.status == RunStatus.DONE
    assert reloaded.pending_gate is None
    assert reloaded.history[-1]["gate_response"] == "approved"


@pytest.mark.asyncio
async def test_finalize_gate_deny(tmp_path: Path):
    wf = _gated_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="gated", slug="x",
                       initial_phase="a")
    await advance(ws, state, target="b", reason="ok")
    state = load_run(ws, state.run_id)

    await finalize_gate_response(ws, state, approved=False)
    reloaded = load_run(ws, state.run_id)
    # on_deny was "a" — stayed in phase a (but transitioned through gate)
    assert reloaded.current_phase == "a"
    assert reloaded.status == RunStatus.RUNNING
    assert reloaded.history[-1]["gate_response"] == "denied"


@pytest.mark.asyncio
async def test_verify_subagent_outputs_present(tmp_path: Path):
    wf = _subagent_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="sub", slug="x",
                       initial_phase="g")
    artifacts = ws / "workflows" / "sub" / "runs" / state.run_id \
        / "artifacts" / "g"
    artifacts.mkdir(parents=True)
    (artifacts / "sources.md").write_text("data")

    missing = verify_subagent_outputs(ws, state, phase_id="g")
    assert missing == []


@pytest.mark.asyncio
async def test_verify_subagent_outputs_missing_returns_list(tmp_path: Path):
    wf = _subagent_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="sub", slug="x",
                       initial_phase="g")

    missing = verify_subagent_outputs(ws, state, phase_id="g")
    assert missing == ["sources.md"]
```

- [ ] **Step 4.2: Run tests, expect ImportError**

Run: `pytest tests/test_workflow_engine.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4.3: Implement engine.py (transitions + gates + output verification)**

```python
# src/decafclaw/workflow/engine.py
"""Workflow state-machine operations.

advance() is the canonical transition entrypoint. Gate dispatch returns
an EndTurnConfirm; finalize_gate_response completes the gated edge once
the user has answered. verify_subagent_outputs is called by the
subagent dispatcher after a child completes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..media import EndTurnConfirm
from . import registry
from .runs import run_lock, save_run
from .types import (
    EdgeDef,
    GateDef,
    PhaseDef,
    PhaseKind,
    RunState,
    RunStatus,
    WorkflowDef,
)

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class AdvanceResult:
    """Returned by advance(): new phase, optional end_turn signal."""

    new_phase: str
    end_turn_signal: EndTurnConfirm | bool | None = None


def _find_edge(phase: PhaseDef, target: str) -> tuple[int, EdgeDef] | None:
    for i, edge in enumerate(phase.next_phases):
        if edge.id == target:
            return i, edge
    return None


async def advance(workspace: Path, state: RunState, target: str,
                  reason: str) -> AdvanceResult:
    """Advance the run along the matching edge.

    If the edge has a gate, returns an AdvanceResult with an
    EndTurnConfirm in end_turn_signal — the caller surfaces the
    buttons. Otherwise applies the transition and persists.
    """
    wf = registry.get(state.workflow)
    if wf is None:
        raise ValueError(f"workflow '{state.workflow}' not registered")

    async with run_lock(state.run_id):
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
            return _enter_gate(workspace, state, edge_idx, edge, reason)

        return _apply_transition(
            workspace, wf, state, edge_idx, target, reason,
            gate_response=None)


def _enter_gate(workspace: Path, state: RunState, edge_idx: int,
                edge: EdgeDef, reason: str) -> AdvanceResult:
    gate = edge.gate
    assert gate is not None
    on_deny = gate.on_deny or state.current_phase
    state.status = RunStatus.PAUSED_GATE
    state.pending_gate = {"edge_target": edge.id, "on_deny": on_deny}
    save_run(workspace, state)

    confirm = EndTurnConfirm(
        message=gate.message,
        approve_label=gate.approve_label,
        deny_label=gate.deny_label,
        on_approve=None,  # filled in by tool layer with finalize_gate_response
        on_deny=None,
    )
    return AdvanceResult(new_phase=state.current_phase,
                         end_turn_signal=confirm)


async def finalize_gate_response(workspace: Path, state: RunState,
                                 approved: bool) -> AdvanceResult:
    """Apply a gate's approve/deny response and resume."""
    wf = registry.get(state.workflow)
    if wf is None:
        raise ValueError(f"workflow '{state.workflow}' not registered")
    if state.status != RunStatus.PAUSED_GATE or state.pending_gate is None:
        raise ValueError("run is not paused on a gate")

    async with run_lock(state.run_id):
        pending = state.pending_gate
        target = pending["edge_target"] if approved else pending["on_deny"]
        # Find edge index for history (approve path uses original edge)
        phase = wf.phase(state.current_phase)
        edge_idx = -1
        if phase is not None:
            for i, e in enumerate(phase.next_phases):
                if e.id == pending["edge_target"]:
                    edge_idx = i
                    break
        state.pending_gate = None
        return _apply_transition(
            workspace, wf, state, edge_idx, target,
            reason=("user approved" if approved else "user denied"),
            gate_response=("approved" if approved else "denied"))


def _apply_transition(workspace: Path, wf: WorkflowDef,
                      state: RunState, edge_idx: int, target: str,
                      reason: str, gate_response: str | None
                      ) -> AdvanceResult:
    prev = state.current_phase
    next_phase = wf.phase(target)
    if next_phase is None:
        raise ValueError(
            f"transition target '{target}' not in workflow")
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
    save_run(workspace, state)
    return AdvanceResult(new_phase=target, end_turn_signal=None)


def verify_subagent_outputs(workspace: Path, state: RunState,
                            phase_id: str) -> list[str]:
    """Return the list of expected outputs that are MISSING from artifacts.

    Empty list means all outputs are present.
    """
    wf = registry.get(state.workflow)
    if wf is None:
        return []
    phase = wf.phase(phase_id)
    if phase is None or phase.kind != PhaseKind.SUBAGENT:
        return []
    artifacts = (workspace / "workflows" / state.workflow / "runs"
                 / state.run_id / "artifacts" / phase_id)
    missing: list[str] = []
    for output in phase.outputs:
        if not (artifacts / output).is_file():
            missing.append(output)
    return missing
```

- [ ] **Step 4.4: Implement subagent.py (deferred until Task 7 — see note)**

The subagent dispatcher uses `delegate.py` primitives but with workflow-aware setup (prompt = phase body, tools = phase whitelist, working dir scoped to artifacts/{phase}/). It calls `verify_subagent_outputs` after completion and transitions on success. Implementing it requires the engine tools to wire into the agent loop, so it's split out into Task 7. For now, leave a stub so imports work:

```python
# src/decafclaw/workflow/subagent.py
"""Workflow-aware subagent dispatcher.

Implementation lives here; the tools/workflow_tools.py layer is what
calls dispatch_subagent_phase() during a transition into a subagent
phase. See Task 7 for the wiring.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .types import RunState

log = logging.getLogger(__name__)


async def dispatch_subagent_phase(ctx, workspace: Path,
                                   state: RunState, phase_id: str
                                   ) -> None:
    """Spawn a child agent to execute a subagent phase.

    Wires into delegate.py's _run_child_turn primitives with:
    - prompt = phase body (or activate `subagent-skill:` if set)
    - tool whitelist = phase.tools resolved against the registry
    - working dir hint = artifacts/{phase_id}/

    On completion, calls verify_subagent_outputs and, if outputs are
    present, applies the auto-advance transition (single edge).
    On missing outputs, sets state.status = ERROR.

    See Task 7 for the full implementation that ties this into the
    workflow_tools layer and the agent loop.
    """
    raise NotImplementedError("subagent dispatch — see Task 7")
```

- [ ] **Step 4.5: Run engine tests, expect pass (subagent dispatch tests cover verification only at this point)**

Run: `pytest tests/test_workflow_engine.py -v`
Expected: PASS — 7 passed

- [ ] **Step 4.6: Lint + commit**

Run: `make lint`
Expected: clean

```bash
git add src/decafclaw/workflow/engine.py src/decafclaw/workflow/subagent.py tests/test_workflow_engine.py
git commit -m "$(cat <<'EOF'
feat(workflow): engine transitions, gates, output verification

advance() applies edge transitions, gating via EndTurnConfirm when
the edge declares one. finalize_gate_response routes the
approve/deny response to the right target. verify_subagent_outputs
checks that subagent phases produced the files they promised.
subagent.py is stubbed; dispatch wiring lands in Task 7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Engine management tools (workflow_*)

**Files:**
- Create: `src/decafclaw/tools/workflow_tools.py`
- Modify: `src/decafclaw/tools/__init__.py`
- Create: `tests/test_workflow_tools.py`

The agent-facing tools. `phase_advance` is special — it gets dynamically regenerated per turn with a current-phase enum.

- [ ] **Step 5.1: Write failing tests for tool surface**

```python
# tests/test_workflow_tools.py
"""Tests for workflow engine tools."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from decafclaw.media import EndTurnConfirm, ToolResult
from decafclaw.workflow import registry
from decafclaw.workflow.runs import create_run, load_run
from decafclaw.workflow.types import (
    EdgeDef, GateDef, PhaseDef, PhaseKind, WorkflowDef,
)
from decafclaw.tools.workflow_tools import (
    build_phase_advance_definition,
    tool_phase_advance,
    tool_workflow_artifact_read,
    tool_workflow_artifact_write,
    tool_workflow_list,
    tool_workflow_start,
    tool_workflow_status,
    tool_workflow_switch,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


def _ctx_for(tmp_path: Path) -> SimpleNamespace:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(workspace_path=workspace)
    skills = SimpleNamespace(data={})
    return SimpleNamespace(config=config, skills=skills)


def _two_phase_wf() -> WorkflowDef:
    return WorkflowDef(
        name="demo", description="d", initial_phase="a",
        phases={
            "a": PhaseDef(
                id="a", kind=PhaseKind.INLINE, prompt="A",
                tools=[],
                next_phases=[
                    EdgeDef(id="b", when="when ready", gate=None),
                    EdgeDef(id="c", when="when stuck", gate=None),
                ],
                gate=None, outputs=(), subagent_skill=None,
                context_profile={},
            ),
            "b": PhaseDef(
                id="b", kind=PhaseKind.INLINE, prompt="",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
            "c": PhaseDef(
                id="c", kind=PhaseKind.INLINE, prompt="",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=True, argument_hint="",
    )


@pytest.mark.asyncio
async def test_workflow_start_creates_run(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    result = await tool_workflow_start(ctx, name="demo", slug="t1")
    assert isinstance(result, (str, ToolResult))
    text = result.text if isinstance(result, ToolResult) else result
    assert "demo" in text
    assert ctx.skills.data["current_workflow_run"]


@pytest.mark.asyncio
async def test_workflow_start_unknown_workflow(tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    result = await tool_workflow_start(ctx, name="ghost", slug="")
    assert isinstance(result, ToolResult)
    assert "not found" in result.text.lower()


@pytest.mark.asyncio
async def test_phase_advance_unknown_target_errors(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="t1")
    result = await tool_phase_advance(ctx, target_phase_id="ghost",
                                       reason="")
    assert isinstance(result, ToolResult)
    assert "not a valid next phase" in result.text


@pytest.mark.asyncio
async def test_phase_advance_valid_target(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="t1")
    result = await tool_phase_advance(ctx, target_phase_id="b",
                                       reason="ready")
    assert isinstance(result, ToolResult)
    assert "Advanced" in result.text or "b" in result.text


@pytest.mark.asyncio
async def test_phase_advance_dynamic_enum_reflects_current_phase(
        tmp_path: Path):
    """The phase_advance schema enum lists only the current phase's targets."""
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="t1")
    definition = build_phase_advance_definition(ctx)
    assert definition is not None
    enum_vals = definition["function"]["parameters"]["properties"][
        "target_phase_id"]["enum"]
    assert set(enum_vals) == {"b", "c"}
    # when: clauses surface in the description
    desc = definition["function"]["description"]
    assert "when ready" in desc
    assert "when stuck" in desc


@pytest.mark.asyncio
async def test_phase_advance_definition_none_when_no_run_active(
        tmp_path: Path):
    ctx = _ctx_for(tmp_path)
    assert build_phase_advance_definition(ctx) is None


@pytest.mark.asyncio
async def test_workflow_artifact_write_and_read(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="t1")
    await tool_workflow_artifact_write(
        ctx, relative_path="notes.txt", content="hello")
    result = await tool_workflow_artifact_read(
        ctx, relative_path="notes.txt")
    text = result.text if isinstance(result, ToolResult) else result
    assert "hello" in text


@pytest.mark.asyncio
async def test_workflow_artifact_write_rejects_path_traversal(
        tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="t1")
    result = await tool_workflow_artifact_write(
        ctx, relative_path="../../escape.txt", content="hi")
    assert isinstance(result, ToolResult)
    assert "outside" in result.text.lower() or "invalid" in result.text.lower()


@pytest.mark.asyncio
async def test_workflow_status_shows_valid_targets(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="t1")
    result = await tool_workflow_status(ctx)
    text = result.text if isinstance(result, ToolResult) else result
    assert "when ready" in text
    assert "when stuck" in text


@pytest.mark.asyncio
async def test_workflow_list_and_switch(tmp_path: Path):
    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    await tool_workflow_start(ctx, name="demo", slug="one")
    first = ctx.skills.data["current_workflow_run"]
    await tool_workflow_start(ctx, name="demo", slug="two")
    second = ctx.skills.data["current_workflow_run"]
    assert first != second

    listing = await tool_workflow_list(ctx, workflow="", status="")
    text = listing.text if isinstance(listing, ToolResult) else listing
    assert "one" in text
    assert "two" in text

    await tool_workflow_switch(ctx, run_id=first)
    assert ctx.skills.data["current_workflow_run"] == first
```

- [ ] **Step 5.2: Run tests, expect ImportError**

Run: `pytest tests/test_workflow_tools.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 5.3: Implement workflow_tools.py**

```python
# src/decafclaw/tools/workflow_tools.py
"""Always-loaded workflow engine tools.

- workflow_start / list / switch / status — run lifecycle
- phase_advance — canonical transition (dynamically regenerated per turn
  with a current-phase enum + when: clause descriptions)
- workflow_artifact_read / write — scoped artifact I/O

The dynamic provider `refresh_workflow_tools(ctx)` is called from
tool_definitions.refresh_dynamic_tools() to inject the per-turn
phase_advance schema reflecting the current run's current phase.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..media import ToolResult
from ..workflow import engine, registry
from ..workflow.runs import create_run, list_runs, load_run, save_run
from ..workflow.types import RunStatus

log = logging.getLogger(__name__)

_BASE_ADVANCE_DESC = (
    "Advance the current workflow run to its next phase. You MUST "
    "pick a target_phase_id from the enum — other values will be "
    "rejected by the engine. The 'reason' parameter is a 1-2 "
    "sentence justification for the routing choice."
)


def _get_run(ctx):
    run_id = (ctx.skills.data or {}).get("current_workflow_run")
    if not run_id:
        return None, None
    state = load_run(ctx.config.workspace_path, run_id)
    if state is None:
        return None, None
    wf = registry.get(state.workflow)
    return state, wf


def _set_current_run(ctx, run_id: str) -> None:
    if ctx.skills.data is None:
        ctx.skills.data = {}
    ctx.skills.data["current_workflow_run"] = run_id


def build_phase_advance_definition(ctx) -> dict | None:
    """Return the per-turn JSON-Schema function definition for
    phase_advance, with the enum + descriptions populated from the
    current run's current phase. Returns None when no run is active
    (the tool is hidden until a workflow starts).
    """
    state, wf = _get_run(ctx)
    if state is None or wf is None:
        return None
    phase = wf.phase(state.current_phase)
    if phase is None or not phase.next_phases:
        return None

    enum_vals = [e.id for e in phase.next_phases]
    parts = [
        f"You are currently in phase '{phase.id}' of workflow "
        f"'{wf.name}'. Pick the target that matches your situation:"
    ]
    for edge in phase.next_phases:
        when = edge.when.strip() or "(no annotation — only option)"
        parts.append(
            f"\n  - target_phase_id=\"{edge.id}\"\n"
            f"    Pick this when: {when}")
    parts.append(
        "\n\nIf you're not sure which applies, call workflow_status "
        "for a recap.")
    description = _BASE_ADVANCE_DESC + "\n\n" + "\n".join(parts)

    return {
        "type": "function",
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


def refresh_workflow_tools(ctx) -> None:
    """Per-turn dynamic refresh — called from refresh_dynamic_tools().

    Injects the dynamic phase_advance into ctx.tools.extra_definitions
    (and ctx.tools.extra) when a workflow run is active.
    """
    definition = build_phase_advance_definition(ctx)
    if definition is None:
        # Remove any stale phase_advance from a previous turn
        if hasattr(ctx, "tools"):
            ctx.tools.extra.pop("phase_advance", None)
            ctx.tools.extra_definitions = [
                d for d in ctx.tools.extra_definitions
                if d["function"]["name"] != "phase_advance"
            ]
        return
    ctx.tools.extra["phase_advance"] = tool_phase_advance
    ctx.tools.extra_definitions = [
        d for d in ctx.tools.extra_definitions
        if d["function"]["name"] != "phase_advance"
    ] + [definition]


# --------------------------------------------------------------- tools

async def tool_workflow_start(ctx, name: str, slug: str = ""
                              ) -> str | ToolResult:
    """Create a new run of a workflow."""
    wf = registry.get(name)
    if wf is None:
        return ToolResult(
            text=f"[error: workflow '{name}' not found]")
    slug = slug or "run"
    state = create_run(
        ctx.config.workspace_path,
        workflow=name,
        slug=slug,
        initial_phase=wf.initial_phase,
    )
    _set_current_run(ctx, state.run_id)
    return (
        f"Started workflow '{name}' (run {state.run_id}). "
        f"Current phase: {state.current_phase}. "
        f"Use phase_advance to move forward."
    )


async def tool_workflow_list(ctx, workflow: str = "",
                             status: str = "") -> str | ToolResult:
    """List workflow runs across all conversations."""
    runs = list_runs(ctx.config.workspace_path,
                     workflow=workflow, status=status)
    if not runs:
        return "No workflow runs."
    lines = ["| Run ID | Workflow | Phase | Status | Updated |",
             "| --- | --- | --- | --- | --- |"]
    for r in runs:
        lines.append(
            f"| {r.run_id} | {r.workflow} | {r.current_phase} "
            f"| {r.status.value} | {r.updated_at} |")
    return "\n".join(lines)


async def tool_workflow_switch(ctx, run_id: str) -> str | ToolResult:
    """Set the current workflow run for this conversation."""
    state = load_run(ctx.config.workspace_path, run_id)
    if state is None:
        return ToolResult(text=f"[error: run '{run_id}' not found]")
    _set_current_run(ctx, run_id)
    return f"Switched to run {run_id} (phase: {state.current_phase})."


async def tool_workflow_status(ctx) -> str | ToolResult:
    """Show the current run's state, valid next phases with when:
    annotations, and recent transition history."""
    state, wf = _get_run(ctx)
    if state is None or wf is None:
        return "No workflow run active. Use workflow_start to begin."
    phase = wf.phase(state.current_phase)
    lines = [
        f"# Workflow: {state.workflow}",
        f"**Run:** {state.run_id}",
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


async def tool_phase_advance(ctx, target_phase_id: str,
                              reason: str = "") -> str | ToolResult:
    """Canonical workflow transition. Dynamically gated per turn — the
    schema only allows current-phase target ids."""
    state, wf = _get_run(ctx)
    if state is None or wf is None:
        return ToolResult(text="[error: no active workflow run]")
    try:
        result = await engine.advance(
            ctx.config.workspace_path, state, target=target_phase_id,
            reason=reason)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")

    if result.end_turn_signal is not None:
        # Wire up the gate callbacks to call finalize_gate_response.
        # agent.py:686-689 awaits async callbacks via
        # asyncio.iscoroutinefunction, so pass async functions directly.
        from ..media import EndTurnConfirm
        confirm = result.end_turn_signal
        assert isinstance(confirm, EndTurnConfirm)
        run_id = state.run_id
        workspace = ctx.config.workspace_path

        async def _on_approve():
            s = load_run(workspace, run_id)
            if s is not None:
                await engine.finalize_gate_response(workspace, s,
                                                   approved=True)

        async def _on_deny():
            s = load_run(workspace, run_id)
            if s is not None:
                await engine.finalize_gate_response(workspace, s,
                                                   approved=False)

        confirm.on_approve = _on_approve
        confirm.on_deny = _on_deny
        return ToolResult(text="Submitted for review.",
                          end_turn=confirm)

    return ToolResult(
        text=f"Advanced to phase '{result.new_phase}'.",
        end_turn=False)


def _resolve_artifact_path(ctx, relative_path: str) -> Path | None:
    state, _wf = _get_run(ctx)
    if state is None:
        return None
    base = (ctx.config.workspace_path / "workflows" / state.workflow
            / "runs" / state.run_id / "artifacts").resolve()
    candidate = (base / relative_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


async def tool_workflow_artifact_write(ctx, relative_path: str,
                                        content: str) -> str | ToolResult:
    """Write content to a path under the current run's artifacts/."""
    path = _resolve_artifact_path(ctx, relative_path)
    if path is None:
        state, _ = _get_run(ctx)
        if state is None:
            return ToolResult(text="[error: no active workflow run]")
        return ToolResult(
            text=f"[error: '{relative_path}' is outside the run's "
            "artifacts directory]")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return f"Wrote {len(content)} chars to {relative_path}."


async def tool_workflow_artifact_read(ctx, relative_path: str
                                       ) -> str | ToolResult:
    """Read content from a path under the current run's artifacts/."""
    path = _resolve_artifact_path(ctx, relative_path)
    if path is None:
        return ToolResult(
            text=f"[error: '{relative_path}' is outside the run's "
            "artifacts directory]")
    if not path.is_file():
        return ToolResult(text=f"[error: '{relative_path}' not found]")
    return path.read_text()


# ----------------------------------------------------- registry exports

WORKFLOW_TOOLS = {
    "workflow_start": tool_workflow_start,
    "workflow_list": tool_workflow_list,
    "workflow_switch": tool_workflow_switch,
    "workflow_status": tool_workflow_status,
    "workflow_artifact_write": tool_workflow_artifact_write,
    "workflow_artifact_read": tool_workflow_artifact_read,
    # phase_advance is dynamic — injected per turn by
    # refresh_workflow_tools when a run is active.
}

WORKFLOW_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "workflow_start",
            "description": (
                "Start a new run of a workflow. The workflow must be "
                "registered (i.e., a kind:workflow skill is installed)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "slug": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workflow_list",
            "description": (
                "List workflow runs across all conversations. Filter "
                "by workflow name or status."),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow": {"type": "string"},
                    "status": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workflow_switch",
            "description": (
                "Set the current workflow run for this conversation."),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                },
                "required": ["run_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workflow_status",
            "description": (
                "Show the current run: phase, status, valid next "
                "phases with their when: annotations, recent history."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workflow_artifact_write",
            "description": (
                "Write content to a relative path under the current "
                "run's artifacts/ directory."),
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
        "function": {
            "name": "workflow_artifact_read",
            "description": (
                "Read content from a relative path under the current "
                "run's artifacts/ directory."),
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

- [ ] **Step 5.4: Merge workflow tools into the global registry**

Modify `src/decafclaw/tools/__init__.py`:

```python
# Add to the imports block (after the existing tool imports):
from .workflow_tools import WORKFLOW_TOOL_DEFINITIONS, WORKFLOW_TOOLS

# Update the TOOLS dict assignment (line 32-38) to include:
TOOLS = {**CORE_TOOLS, **CHECKLIST_TOOLS,
         **CONVERSATION_TOOLS, **WORKSPACE_TOOLS, **SHELL_TOOLS,
         **HTTP_TOOLS,
         **SKILL_TOOLS,
         **HEARTBEAT_TOOLS, **HEALTH_TOOLS,
         **DELEGATE_TOOLS, **ATTACHMENT_TOOLS, **EMAIL_TOOLS,
         **NOTIFICATION_TOOLS, **CANVAS_TOOLS, **NOTES_TOOLS,
         **WORKFLOW_TOOLS}

# Update TOOL_DEFINITIONS (line 39-50):
TOOL_DEFINITIONS = (CORE_TOOL_DEFINITIONS
                    + CHECKLIST_TOOL_DEFINITIONS
                    + CONVERSATION_TOOL_DEFINITIONS + WORKSPACE_TOOL_DEFINITIONS
                    + SHELL_TOOL_DEFINITIONS
                    + HTTP_TOOL_DEFINITIONS + SKILL_TOOL_DEFINITIONS
                    + HEARTBEAT_TOOL_DEFINITIONS
                    + HEALTH_TOOL_DEFINITIONS
                    + DELEGATE_TOOL_DEFINITIONS + ATTACHMENT_TOOL_DEFINITIONS
                    + EMAIL_TOOL_DEFINITIONS
                    + NOTIFICATION_TOOL_DEFINITIONS
                    + CANVAS_TOOL_DEFINITIONS
                    + NOTES_TOOL_DEFINITIONS
                    + WORKFLOW_TOOL_DEFINITIONS)
```

- [ ] **Step 5.5: Run tests, expect pass**

Run: `pytest tests/test_workflow_tools.py -v`
Expected: PASS — 10 passed

- [ ] **Step 5.6: Lint + commit**

Run: `make lint && make typecheck`
Expected: clean

```bash
git add src/decafclaw/tools/workflow_tools.py src/decafclaw/tools/__init__.py tests/test_workflow_tools.py
git commit -m "$(cat <<'EOF'
feat(workflow): always-loaded engine tools + dynamic phase_advance

workflow_start/list/switch/status, workflow_artifact_read/write, and
the dynamically-regenerated phase_advance whose enum + descriptions
reflect the current run's current phase per turn. Static tools
registered in tools/__init__.py; phase_advance is injected via
refresh_workflow_tools() (wired in Task 6).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Dynamic-tool refresh hook + skill loader branch

**Files:**
- Modify: `src/decafclaw/tool_definitions.py`
- Modify: `src/decafclaw/skills/__init__.py`
- Create: `tests/test_workflow_skill_loader.py`

Two integrations: (1) wire `refresh_workflow_tools(ctx)` into the per-turn dynamic-refresh path; (2) recognize `kind: workflow` in `parse_skill_md`, route to `load_workflow`, and register the result.

- [ ] **Step 6.1: Inspect current `refresh_dynamic_tools` to know where to hook**

Read `src/decafclaw/tool_definitions.py:38-82` first to see the current loop. The hook goes immediately after the existing skill provider loop, before the function returns.

- [ ] **Step 6.2: Write failing test for skill loader workflow branch**

```python
# tests/test_workflow_skill_loader.py
"""Tests for the skill-loader branch that recognizes kind:workflow."""

from pathlib import Path

import pytest

from decafclaw.skills import parse_skill_md
from decafclaw.workflow import registry


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


_SKILL_WORKFLOW = """---
name: test-wf
description: A test workflow.
kind: workflow
user-invocable: true
workflow:
  initial-phase: a
---
body
"""

_PHASE_A = """---
kind: inline
tools: []
next-phases:
  - id: b
---
A prompt
"""

_PHASE_B = """---
kind: inline
tools: []
---
B prompt
"""


def _write(tmp_path: Path, files: dict[str, str]) -> Path:
    sk = tmp_path / "test-wf"
    sk.mkdir()
    (sk / "SKILL.md").write_text(files["SKILL.md"])
    phases = sk / "phases"
    phases.mkdir()
    for name, content in files.items():
        if name == "SKILL.md":
            continue
        (phases / name).write_text(content)
    return sk / "SKILL.md"


def test_parse_skill_md_workflow_registers_definition(tmp_path: Path):
    path = _write(tmp_path, {
        "SKILL.md": _SKILL_WORKFLOW,
        "a.md": _PHASE_A,
        "b.md": _PHASE_B,
    })
    info = parse_skill_md(path)
    assert info is not None
    assert info.name == "test-wf"
    assert registry.get("test-wf") is not None
    assert registry.get("test-wf").initial_phase == "a"


def test_parse_skill_md_workflow_invalid_skips_registration(
        tmp_path: Path, caplog):
    bad_phase_a = """---
kind: inline
next-phases:
  - id: ghost
---
"""
    path = _write(tmp_path, {
        "SKILL.md": _SKILL_WORKFLOW,
        "a.md": bad_phase_a,
        "b.md": _PHASE_B,
    })
    with caplog.at_level("WARNING"):
        info = parse_skill_md(path)
    # SkillInfo still returned (skill loader is lenient), but the
    # workflow registry is NOT populated for an invalid workflow
    assert registry.get("test-wf") is None
    assert any("workflow" in rec.message.lower()
               for rec in caplog.records)
```

- [ ] **Step 6.3: Run test, expect fail**

Run: `pytest tests/test_workflow_skill_loader.py -v`
Expected: FAIL — registry is empty because `parse_skill_md` doesn't handle `kind: workflow`

- [ ] **Step 6.4: Add the kind:workflow branch to `parse_skill_md`**

In `src/decafclaw/skills/__init__.py`, after line 95 (after the existing name/description check), add a workflow branch:

```python
# In parse_skill_md, after the name/description validation but before
# the existing SkillInfo construction. Roughly after line 96.

    # If this is a workflow skill, attempt to load+register it
    if meta.get("kind") == "workflow":
        from decafclaw.workflow.loader import LoaderError, load_workflow
        from decafclaw.workflow import registry as _wf_registry
        skill_dir = path.parent
        try:
            wf_def = load_workflow(skill_dir)
        except LoaderError as exc:
            log.warning(
                "[workflow] skipping '%s' — invalid workflow: %s",
                name, exc)
        else:
            _wf_registry.register(wf_def)
```

- [ ] **Step 6.5: Run skill-loader test, expect pass**

Run: `pytest tests/test_workflow_skill_loader.py -v`
Expected: PASS — 2 passed

- [ ] **Step 6.6: Wire `refresh_workflow_tools` into per-turn dynamic refresh**

In `src/decafclaw/tool_definitions.py`, locate the `refresh_dynamic_tools(ctx)` function (around line 38-82). After the existing loop that calls each skill provider, add:

```python
# (After the skill dynamic providers loop, before the function returns)

    # Workflow engine per-turn tool refresh — injects the dynamic
    # phase_advance schema reflecting the current run's current phase.
    from .tools.workflow_tools import refresh_workflow_tools
    refresh_workflow_tools(ctx)
```

- [ ] **Step 6.7: Add integration test confirming phase_advance shows up after workflow_start**

```python
# Append to tests/test_workflow_tools.py

@pytest.mark.asyncio
async def test_refresh_workflow_tools_injects_phase_advance(tmp_path: Path):
    """After workflow_start, refresh_workflow_tools should add
    phase_advance to ctx.tools.extra_definitions with the right enum."""
    from decafclaw.tools.workflow_tools import refresh_workflow_tools

    registry.register(_two_phase_wf())
    ctx = _ctx_for(tmp_path)
    ctx.tools = SimpleNamespace(extra={}, extra_definitions=[])

    refresh_workflow_tools(ctx)
    assert "phase_advance" not in ctx.tools.extra

    await tool_workflow_start(ctx, name="demo", slug="t1")
    refresh_workflow_tools(ctx)
    assert "phase_advance" in ctx.tools.extra
    defs = [d for d in ctx.tools.extra_definitions
            if d["function"]["name"] == "phase_advance"]
    assert len(defs) == 1
    enum_vals = defs[0]["function"]["parameters"]["properties"][
        "target_phase_id"]["enum"]
    assert set(enum_vals) == {"b", "c"}
```

- [ ] **Step 6.8: Run all workflow tests, expect pass**

Run: `pytest tests/test_workflow_*.py -v`
Expected: PASS — all green

- [ ] **Step 6.9: Lint + commit**

Run: `make check`
Expected: clean

```bash
git add src/decafclaw/skills/__init__.py src/decafclaw/tool_definitions.py tests/test_workflow_skill_loader.py tests/test_workflow_tools.py
git commit -m "$(cat <<'EOF'
feat(workflow): skill loader recognizes kind:workflow + per-turn refresh

parse_skill_md routes kind:workflow skills through load_workflow and
registers the WorkflowDef. Invalid workflows log a warning and are
skipped (the SkillInfo is still returned so the skill loader stays
lenient). refresh_dynamic_tools now calls refresh_workflow_tools,
which injects a current-phase-specific phase_advance schema when a
run is active.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Subagent dispatch + output verification wiring

**Files:**
- Modify: `src/decafclaw/workflow/subagent.py` (replace stub with implementation)
- Modify: `src/decafclaw/workflow/engine.py` (add `enter_phase` that triggers subagent dispatch)
- Modify: `src/decafclaw/tools/workflow_tools.py` (wire subagent dispatch into the post-transition flow)
- Modify: `tests/test_workflow_engine.py` (add subagent dispatch flow tests)

Build the subagent dispatcher on top of `delegate.py`'s primitives. The child runs the phase prompt with the phase's tool whitelist; on completion the engine verifies outputs and applies the next transition.

- [ ] **Step 7.1: Read `delegate.py` to know which primitives to reuse**

Read `src/decafclaw/tools/delegate.py:127-260` — the `_run_child_turn()` helper. We can't call `tool_delegate_task` directly because it builds the child prompt from "task" arg + activated skills; we need full control over the child system prompt and tool whitelist. Plan: extract or duplicate the `setup()` callback pattern, but inject our phase prompt as the child's "task" message, and override the whitelist via `child_ctx.tools.allowed`.

- [ ] **Step 7.2: Write failing test for subagent dispatch happy path + missing-output failure**

```python
# Append to tests/test_workflow_engine.py

@pytest.mark.asyncio
async def test_subagent_dispatch_happy_path(tmp_path: Path, monkeypatch):
    """Dispatching a subagent phase writes the artifact and advances
    to the next phase."""
    from decafclaw.workflow import subagent as wf_subagent
    from decafclaw.workflow.runs import create_run, load_run
    from decafclaw.workflow.engine import dispatch_and_finalize_subagent

    wf = _subagent_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="sub", slug="x",
                       initial_phase="g")

    # Stub the child-agent runner to "produce" the output file
    async def fake_run_child(*, ctx, workspace, state, phase):
        artifacts_dir = (workspace / "workflows" / state.workflow
                         / "runs" / state.run_id / "artifacts"
                         / phase.id)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "sources.md").write_text("fetched")
        return "done"

    monkeypatch.setattr(wf_subagent, "_run_child", fake_run_child)

    ctx = SimpleNamespace(config=SimpleNamespace(workspace_path=ws))
    await dispatch_and_finalize_subagent(ctx, ws, state, phase_id="g")
    reloaded = load_run(ws, state.run_id)
    assert reloaded.current_phase == "d"
    assert reloaded.status == RunStatus.DONE
    assert reloaded.history[-1]["from"] == "g"
    assert reloaded.history[-1]["to"] == "d"


@pytest.mark.asyncio
async def test_subagent_dispatch_missing_output_sets_error(
        tmp_path: Path, monkeypatch):
    from decafclaw.workflow import subagent as wf_subagent
    from decafclaw.workflow.engine import dispatch_and_finalize_subagent
    from decafclaw.workflow.runs import create_run, load_run

    wf = _subagent_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="sub", slug="x",
                       initial_phase="g")

    async def fake_run_child(*, ctx, workspace, state, phase):
        # Subagent "completes" but doesn't write the output
        return "incomplete"

    monkeypatch.setattr(wf_subagent, "_run_child", fake_run_child)

    ctx = SimpleNamespace(config=SimpleNamespace(workspace_path=ws))
    await dispatch_and_finalize_subagent(ctx, ws, state, phase_id="g")
    reloaded = load_run(ws, state.run_id)
    assert reloaded.status == RunStatus.ERROR
    assert "sources.md" in (reloaded.error or "")
    assert reloaded.current_phase == "g"  # didn't advance
```

- [ ] **Step 7.3: Run, expect ImportError on `dispatch_and_finalize_subagent`**

Run: `pytest tests/test_workflow_engine.py::test_subagent_dispatch_happy_path -v`
Expected: FAIL — ImportError or AttributeError

- [ ] **Step 7.4: Implement `subagent.py` and `engine.dispatch_and_finalize_subagent`**

Replace the stub in `src/decafclaw/workflow/subagent.py`:

```python
# src/decafclaw/workflow/subagent.py
"""Workflow-aware subagent dispatcher.

Built on the same low-level primitives as tools/delegate.py's
_run_child_turn but with workflow-specific setup: the child's system
prompt is the phase body (or activates subagent-skill:), and the
child's allowed_tools is exactly the phase's tools whitelist.

The child runs inline within a single agent turn from the parent's
perspective — the parent's turn pauses on PAUSED_SUBAGENT, the child
runs its own iteration loop, then the parent resumes with the
artifact present and applies the auto-advance transition.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import replace
from pathlib import Path

from .types import PhaseDef, RunState

log = logging.getLogger(__name__)


# Tools that children may never have, even if the phase whitelist
# includes them. Children are workflow workers, not orchestrators.
_BLOCKED_FOR_CHILDREN = {
    "delegate_task", "activate_skill", "refresh_skills",
    "tool_search", "workflow_start", "workflow_switch",
    "workflow_list",
}


async def _run_child(*, ctx, workspace: Path, state: RunState,
                     phase: PhaseDef) -> str:
    """Spawn a child agent to execute the phase. Returns child's text.

    Public entrypoint — test code monkeypatches this to avoid spinning
    up the real LLM client during unit tests. Real implementation
    wires through agent.run_agent_turn / TurnRunner with a custom
    setup callback that sets the child's system prompt to the phase
    body and the child's allowed_tools to the phase whitelist.
    """
    from ..agent import run_agent_turn
    from ..context import Context
    from ..conversation_manager import TurnKind
    from ..tools import TOOLS

    config = ctx.config

    # Resolve the prompt and any auto-activated skill. If
    # subagent-skill: is set, the child boots with that skill's body
    # in its system prompt and the skill's tools.py contributing tools;
    # otherwise, the phase body itself is the system prompt.
    skill_to_activate: str | None = phase.subagent_skill
    if skill_to_activate:
        skill_map = {s.name: s for s in config.discovered_skills}
        skill = skill_map.get(skill_to_activate)
        if skill is None:
            raise ValueError(
                f"subagent-skill '{skill_to_activate}' not found in "
                "discovered skills")
        prompt = skill.body or ""
    else:
        prompt = phase.prompt or ""

    parent_conv = getattr(ctx, "conv_id", None) \
        or getattr(ctx, "channel_id", "")
    child_conv_id = f"{parent_conv}--wf-{phase.id}-{secrets.token_hex(4)}"

    child_config = replace(
        config,
        agent=replace(config.agent,
                      max_tool_iterations=config.agent.child_max_tool_iterations),
        system_prompt=prompt,
    )
    child_config.discovered_skills = []

    def setup(child_ctx):
        child_ctx.config = child_config
        child_ctx.is_child = True
        child_ctx.skip_reflection = True
        child_ctx.skills.activated = set()
        # Resolve the phase whitelist against the live registry
        all_tool_names = set(TOOLS) | set(getattr(ctx.tools, "extra", {}))
        allowed = _resolve_phase_tools(all_tool_names, phase.tools)
        allowed -= _BLOCKED_FOR_CHILDREN
        child_ctx.tools.allowed = allowed
        child_ctx.tools.extra = {}
        child_ctx.tools.extra_definitions = []
        child_ctx.on_stream_chunk = None
        # Share the parent's event bus so progress is visible
        child_ctx.event_context_id = (
            getattr(ctx, "event_context_id", None)
            or getattr(ctx, "context_id", None))

    child_ctx = Context.for_task(config=config, channel_id=parent_conv,
                                  conv_id=child_conv_id, setup=setup)
    result = await run_agent_turn(
        child_ctx, kind=TurnKind.CHILD_AGENT, message=prompt)
    return result or ""


def _resolve_phase_tools(all_names: set[str],
                         patterns: list[str]) -> set[str]:
    """Expand glob patterns against the live tool registry."""
    import fnmatch
    if not patterns:
        return set()
    matched: set[str] = set()
    for pat in patterns:
        if "*" in pat or "?" in pat:
            matched |= {n for n in all_names if fnmatch.fnmatch(n, pat)}
        elif pat in all_names:
            matched.add(pat)
    return matched
```

Then add `dispatch_and_finalize_subagent` to `engine.py`:

```python
# Append to src/decafclaw/workflow/engine.py

async def dispatch_and_finalize_subagent(ctx, workspace: Path,
                                          state: RunState,
                                          phase_id: str) -> None:
    """Run a subagent phase end-to-end: spawn child, verify outputs,
    advance on success or set ERROR on failure.

    Called by the tool layer when a transition lands on a subagent
    phase (the agent is then expected to wait for completion via
    PAUSED_SUBAGENT status before the next turn).
    """
    from . import subagent as wf_subagent

    wf = registry.get(state.workflow)
    if wf is None:
        raise ValueError(f"workflow '{state.workflow}' not registered")
    phase = wf.phase(phase_id)
    if phase is None:
        raise ValueError(f"phase '{phase_id}' not in workflow")

    async with run_lock(state.run_id):
        try:
            await wf_subagent._run_child(
                ctx=ctx, workspace=workspace,
                state=state, phase=phase)
        except Exception as exc:
            log.exception("[workflow] subagent failed")
            state.status = RunStatus.ERROR
            state.error = f"subagent crashed: {exc}"
            save_run(workspace, state)
            return

        missing = verify_subagent_outputs(workspace, state, phase_id)
        if missing:
            state.status = RunStatus.ERROR
            state.error = (f"subagent did not produce required "
                           f"outputs: {', '.join(missing)}")
            save_run(workspace, state)
            return

        # Auto-advance via single edge
        if len(phase.next_phases) != 1:
            state.status = RunStatus.ERROR
            state.error = (
                f"subagent phase '{phase_id}' must have exactly one "
                "edge for auto-advance")
            save_run(workspace, state)
            return
        target = phase.next_phases[0].id
        _apply_transition(workspace, wf, state, edge_idx=0,
                          target=target,
                          reason="subagent complete",
                          gate_response=None)
```

- [ ] **Step 7.5: Run subagent dispatch tests, expect pass**

Run: `pytest tests/test_workflow_engine.py -v`
Expected: PASS — all tests including the two new ones

- [ ] **Step 7.6: Lint + commit**

Run: `make check`
Expected: clean

```bash
git add src/decafclaw/workflow/subagent.py src/decafclaw/workflow/engine.py tests/test_workflow_engine.py
git commit -m "$(cat <<'EOF'
feat(workflow): subagent dispatch with output verification

dispatch_and_finalize_subagent spawns a child agent via _run_child
(wires into the existing agent.run_agent_turn + Context.for_task
infrastructure with a custom setup callback). On successful child
return, verify_subagent_outputs checks declared outputs; missing
files set state.status=ERROR. On success, the run auto-advances
along the single next-phases edge.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: ContextComposer overlay + phase-boundary clearing

**Files:**
- Create: `src/decafclaw/workflow/context.py`
- Modify: `src/decafclaw/context_composer.py`
- Modify: `src/decafclaw/context_cleanup.py`
- Create: `tests/test_workflow_context.py`

Adds the `WorkflowOverlay` that the composer consults to apply per-phase context-profile overrides and inject the phase-prompt system section. Adds `clear_tool_results_in_range()` to clear tool messages between phase boundaries.

- [ ] **Step 8.1: Write failing tests for overlay shape + composer integration**

```python
# tests/test_workflow_context.py
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
    EdgeDef, PhaseDef, PhaseKind, WorkflowDef,
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
    ctx = SimpleNamespace(
        config=SimpleNamespace(workspace_path=tmp_path / "ws"),
        skills=SimpleNamespace(data={}),
    )
    (tmp_path / "ws").mkdir()
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
        {"role": "user", "content": "phase A → B"},
        {"role": "tool", "name": "z", "content": "current phase output",
         "tool_call_id": "3"},
    ]
    stats = clear_tool_results_in_range(
        history, start_idx=2, end_idx=5,
        preserve_tools={"notes_append", "checklist_create"},
    )
    assert stats.cleared >= 2
    # Pre-range and post-range messages untouched
    assert history[6]["content"] == "current phase output"
    assert "tool output cleared" in history[2]["content"]
```

- [ ] **Step 8.2: Run tests, expect ImportError**

Run: `pytest tests/test_workflow_context.py -v`
Expected: FAIL — ImportError

- [ ] **Step 8.3: Implement `workflow/context.py`**

```python
# src/decafclaw/workflow/context.py
"""ContextComposer integration for workflow runs.

The composer calls consult_workflow_overlay(ctx) once per compose().
The overlay returns the phase-prompt section to append to the system
prompt, the context-profile overrides to apply during composition,
and a phase-boundary flag for tool-result clearing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import registry
from .runs import load_run


@dataclass
class WorkflowOverlay:
    phase_prompt_section: str
    context_profile_overrides: dict
    phase_boundary: bool = False
    phase_boundary_range: tuple[int, int] | None = None
    # Range is (start_idx_inclusive, end_idx_exclusive) into history
    # for clearing; set when phase_boundary=True. Composer computes
    # the actual range from history; overlay just signals intent.

    phase_id: str = ""
    workflow_name: str = ""
    run_id: str = ""


def _format_phase_section(state, phase, wf) -> str:
    parts = [
        f"<workflow_phase run=\"{state.run_id}\" "
        f"phase=\"{phase.id}\" kind=\"{phase.kind.value}\">",
        f"You are in phase '{phase.id}' of workflow '{wf.name}'.",
        "",
        "Phase prompt:",
        phase.prompt,
        "",
    ]
    if phase.next_phases:
        parts.append("Available transitions (use phase_advance):")
        for edge in phase.next_phases:
            when = edge.when.strip() or "(only option)"
            gated = " [gated]" if edge.gate else ""
            parts.append(f"  - {edge.id}{gated} — {when}")
        parts.append("")
        parts.append("No other transition targets are available "
                     "from this phase.")
    else:
        parts.append("This is a terminal phase — no further "
                     "transitions are possible.")
    parts.append("</workflow_phase>")
    return "\n".join(parts)


def consult_workflow_overlay(ctx) -> WorkflowOverlay | None:
    """Return the workflow overlay for the current turn, or None when
    no run is active."""
    run_id = (getattr(ctx, "skills", None)
              and (ctx.skills.data or {}).get("current_workflow_run"))
    if not run_id:
        return None
    workspace: Path = ctx.config.workspace_path
    state = load_run(workspace, run_id)
    if state is None:
        return None
    wf = registry.get(state.workflow)
    if wf is None:
        return None
    phase = wf.phase(state.current_phase)
    if phase is None:
        return None

    phase_boundary = bool(
        phase.context_profile.get("clear-prior-phase-tools", True))

    return WorkflowOverlay(
        phase_prompt_section=_format_phase_section(state, phase, wf),
        context_profile_overrides=dict(phase.context_profile),
        phase_boundary=phase_boundary,
        phase_id=phase.id,
        workflow_name=wf.name,
        run_id=state.run_id,
    )
```

- [ ] **Step 8.4: Add `clear_tool_results_in_range` to context_cleanup.py**

Locate `src/decafclaw/context_cleanup.py:77` (the existing `clear_old_tool_results` function). Add a sibling function:

```python
# In src/decafclaw/context_cleanup.py — add after clear_old_tool_results

def clear_tool_results_in_range(history: list[dict], start_idx: int,
                                end_idx: int,
                                preserve_tools: set[str] | None = None
                                ) -> ClearStats:
    """Stub tool-result messages within [start_idx, end_idx).

    Like clear_old_tool_results, but scoped to a specific range
    instead of the "old enough by count" heuristic. Used by the
    workflow engine to clear prior-phase tool outputs at phase
    boundaries.

    Preserves messages whose `name` (tool name) is in preserve_tools,
    and messages that are not role=='tool'.
    """
    preserve = preserve_tools or set()
    stats = ClearStats()
    end_idx = min(end_idx, len(history))
    for i in range(start_idx, end_idx):
        msg = history[i]
        if msg.get("role") != "tool":
            continue
        if msg.get("name") in preserve:
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        size = len(content)
        if size == 0:
            continue
        msg["content"] = f"[tool output cleared: {size} bytes]"
        stats.cleared += 1
        stats.bytes_freed += size
    return stats
```

- [ ] **Step 8.5: Read `context_composer.py` carefully before modifying**

Read `src/decafclaw/context_composer.py` end-to-end (it's the most touchy file in this PR). Identify:
- The exact line ranges of `_compose_vault_references` and `_compose_notes` (overlay should follow the same call shape)
- Where `mode == ComposerMode.INTERACTIVE` branches gate memory/wiki/notes (so overlay overrides can short-circuit them)
- Where the system prompt sections are assembled (where to append `phase_prompt_section`)
- How the function returns `ComposedContext` (so overlay-aware diagnostic fields can be added if needed)

Do NOT modify the file blindly from the pseudo-code below — match its real structure.

- [ ] **Step 8.6: Wire overlay into `ContextComposer.compose()`**

In `src/decafclaw/context_composer.py`, add an `_consult_workflow_overlay()` call inside `compose()` (around line 320-325, after preempt_matches, before tools), and propagate its effects:

```python
# Inside ContextComposer.compose(), after preempt_matches and before
# the tools assembly. Pseudocode shape — exact placement depends on
# the existing branches.

        # ... existing preempt_matches handling ...

        # Workflow overlay (None if no run active or feature unused)
        from .workflow.context import consult_workflow_overlay
        wf_overlay = None
        if mode == ComposerMode.INTERACTIVE:
            wf_overlay = consult_workflow_overlay(ctx)

        # ... existing memory retrieval, vault references, notes, etc.
        # When wf_overlay is not None, each section consults overlay
        # to decide whether to inject:

        if wf_overlay is None or \
                wf_overlay.context_profile_overrides.get(
                    "memory-retrieval") != "off":
            # ... existing memory retrieval logic ...
            pass

        if wf_overlay is None or \
                wf_overlay.context_profile_overrides.get(
                    "notes-injection") != "off":
            # ... existing notes injection logic ...
            pass

        # ... etc for vault-injection-mode and decision-slice ...

        # If overlay present and phase_boundary is true, run
        # clear_tool_results_in_range to clear tool messages from the
        # most recent phase boundary marker. The composer locates the
        # boundary by walking back through history looking for the
        # most recent transition (a synthetic marker the engine could
        # write, or by scanning for the previous phase's last
        # phase_advance assistant message).

        # System prompt: append wf_overlay.phase_prompt_section to the
        # assembled system prompt sections (same channel that injects
        # <skill_catalog>, etc.).
```

The exact placement of these calls depends on the existing structure of `compose()`. Look at how `_compose_vault_references` and `_compose_notes` are invoked and follow the same pattern. The phase-boundary clearing is best implemented by walking `history` backwards to find the most recent assistant message that called `phase_advance` and clearing tool messages between then and the current end.

Open implementation choice: write the phase boundary as a synthetic marker into history at the moment of transition (cleaner) vs. detect it by scanning (decoupled but more code). Recommendation: synthetic marker — engine adds a single `{role: "workflow_phase_boundary", "from": ..., "to": ...}` message to history when transitioning. Composer scans for the last such marker, clears tool results between it and now. **Decision deferred to plan-execution time**; the test in Step 8.1 only exercises `clear_tool_results_in_range` directly, not the composer wiring.

- [ ] **Step 8.7: Run overlay + cleanup tests, expect pass**

Run: `pytest tests/test_workflow_context.py -v`
Expected: PASS — 4 passed

- [ ] **Step 8.8: Run the full test suite to check no regressions**

Run: `make test`
Expected: PASS — no regressions in existing tests

- [ ] **Step 8.9: Lint + commit**

Run: `make check`
Expected: clean

```bash
git add src/decafclaw/workflow/context.py src/decafclaw/context_composer.py src/decafclaw/context_cleanup.py tests/test_workflow_context.py
git commit -m "$(cat <<'EOF'
feat(workflow): ContextComposer overlay + phase-boundary clearing

WorkflowOverlay returns the phase-prompt section (workflow_phase XML
block) and per-phase context-profile overrides. compose() consults
the overlay in INTERACTIVE mode and conditionally skips memory
retrieval, vault references, notes injection, and decision slice
based on the overrides. clear_tool_results_in_range stubs tool
messages within a [start, end) range — driven by the engine's
phase-boundary signal.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Demo workflow skill + docs + eval

**Files:**
- Create: `src/decafclaw/skills/workflow_demo/SKILL.md`
- Create: `src/decafclaw/skills/workflow_demo/phases/*.md` (4-5 phase files)
- Create: `docs/workflows.md`
- Modify: `docs/index.md`
- Modify: `CLAUDE.md`
- Create: `evals/workflow_routing.yaml`

The demo proves the engine end-to-end. Final shape decided here: a "topic research + summary" workflow with one subagent phase (gather), one inline phase with branching (draft → review OR back to gather), one gated edge (review → publish), one terminal phase (publish).

- [ ] **Step 9.1: Decide demo workflow concrete shape**

The demo workflow is named `research_brief`. Phases:

| Phase | Kind | Outgoing edges | Purpose |
|---|---|---|---|
| `gather` | subagent | → draft | Fetch + summarize sources via tabstack (`outputs: [sources.md]`) |
| `draft` | inline | → review (`when: draft is complete`), → gather (`when: sources thin`) | Write the brief; can request more research |
| `review` | inline | → publish (`when: looks good, gated`), → draft (gate on-deny) | Present draft to user; gate fires for approval |
| `publish` | inline (terminal) | — | Write final brief to vault |

This exercises: subagent phase + outputs, multi-edge agent routing with `when:`, backward edge (draft → gather), edge-level gate with on-deny, terminal phase.

- [ ] **Step 9.2: Write SKILL.md and phase files**

```yaml
# src/decafclaw/skills/workflow_demo/SKILL.md
---
name: research_brief
description: "Research a topic and produce a short brief — demo of the workflow engine."
kind: workflow
user-invocable: true
argument-hint: "[start|list|switch|status] <topic>"
workflow:
  initial-phase: gather
---

Research a topic and produce a short written brief.

When invoked as `!research_brief start <topic>` or `/research_brief start <topic>`,
call `workflow_start` with name=`research_brief` and slug derived from the topic.
After that, call `phase_advance` to route between phases based on what each phase
produces. Use `workflow_status` if you ever lose track of where you are.

User said: $ARGUMENTS
```

```yaml
# src/decafclaw/skills/workflow_demo/phases/gather.md
---
kind: subagent
tools: [tabstack_research, tabstack_extract_markdown, vault_read]
outputs: [sources.md]
next-phases:
  - id: draft
---

You are a research subagent for the `research_brief` workflow. Your job is to
research the topic given by the parent agent and write a structured summary to
`artifacts/gather/sources.md`.

Procedure:
1. Use `tabstack_research` to gather 4-8 high-quality sources on the topic.
2. For each source, capture: title, URL, 1-2 sentence summary in your own words,
   and any key facts/quotes (with attribution).
3. Write `sources.md` with a top-level heading naming the topic, then one
   `## Source: <title>` section per source. End with a `## Key themes` section
   listing 3-5 themes that emerged across the sources.

When the file is written, return — the parent workflow will advance automatically.
```

```yaml
# src/decafclaw/skills/workflow_demo/phases/draft.md
---
kind: inline
tools: [vault_read, workflow_artifact_read, workflow_artifact_write, notes_append, notes_read]
context-profile:
  memory-retrieval: off
  clear-prior-phase-tools: true
next-phases:
  - id: review
    when: |
      The draft is written, covers the topic clearly, and is ready for the
      user to review.
  - id: gather
    when: |
      The source material is too thin to support a clear brief — go back
      and fetch more research before drafting can finish.
---

You are drafting a research brief on the topic. Read the source summary from
`artifacts/gather/sources.md` (use `workflow_artifact_read`).

Compose a brief of 400-600 words covering:
1. A one-paragraph framing of the topic.
2. 2-3 sections of body covering the main themes.
3. A short "Open questions" list at the end.

Write the draft to `artifacts/draft/brief.md` via `workflow_artifact_write`.

Before advancing:
- Use `notes_append` to record a 1-2 sentence summary of what you wrote and any
  decisions you made (these notes persist across phase boundaries).
- Then call `phase_advance` with target `review` if you're satisfied, or
  `gather` if the sources turned out to be insufficient.
```

```yaml
# src/decafclaw/skills/workflow_demo/phases/review.md
---
kind: inline
tools: [vault_read, workflow_artifact_read]
next-phases:
  - id: publish
    when: |
      The user has approved the draft and it can be published to the vault.
    gate:
      type: review
      message: "Approve the research brief?"
      approve-label: "Looks good"
      deny-label: "Needs changes"
      on-deny: draft
---

Read the draft from `artifacts/draft/brief.md` and present it to the user in
your response, prefaced with a single sentence framing what they're about to
read.

Then call `phase_advance` with target `publish` and a brief `reason` — the
gate will surface the Approve / Needs Changes buttons. If the user approves,
the workflow continues to `publish`. If they deny, you'll re-enter `draft`
to revise.
```

```yaml
# src/decafclaw/skills/workflow_demo/phases/publish.md
---
kind: inline
tools: [vault_write, workflow_artifact_read]
---

Read the final draft from `artifacts/draft/brief.md`. Write it to a new vault
page at `vault://briefs/{slug}.md` with appropriate frontmatter (title from
the topic, `tags: [research, brief]`, `summary` from the first paragraph).

Report the vault page path to the user. The workflow is now complete.
```

- [ ] **Step 9.3: Manually verify demo workflow loads without errors**

Run: `python -c "from decafclaw.workflow.loader import load_workflow; from pathlib import Path; wf = load_workflow(Path('src/decafclaw/skills/workflow_demo')); print(wf.name, list(wf.phases))"`
Expected: `research_brief ['draft', 'gather', 'publish', 'review']`

- [ ] **Step 9.4: Write docs/workflows.md**

```markdown
# Workflows

Workflows are declarative multi-phase agent procedures authored as
`kind: workflow` skills. The workflow engine drives a state machine,
constrains tool catalogs per phase, applies context-composer
overrides per phase, dispatches subagent phases to isolated children,
and routes edges via the dynamically-generated `phase_advance` tool.

See [`spec`](dev-sessions/2026-05-19-2121-workflow-engine/spec.md) for
the design rationale and [`research_brief`](../src/decafclaw/skills/workflow_demo/)
for a working example.

## Authoring a workflow

A workflow lives in a single skill directory:

\`\`\`
skills/{name}/
  SKILL.md            # workflow shell
  phases/
    phase_a.md
    phase_b.md
    ...
\`\`\`

### SKILL.md

Sets `kind: workflow` and points at the initial phase. The body is the
optional user-invocable command handler text.

\`\`\`yaml
---
name: my_workflow
description: Short description.
kind: workflow
user-invocable: true
workflow:
  initial-phase: gather
---

Optional command-handler prose. Use `$ARGUMENTS` for command args.
\`\`\`

### Phase files

Each `phases/<stem>.md` defines a phase with `id: <stem>`. Frontmatter
holds wiring; body holds the prompt.

#### Inline phase (default)

\`\`\`yaml
---
kind: inline                   # default; can omit
tools: [vault_read, vault_write]
context-profile:
  memory-retrieval: off        # inherit | off
  notes-injection: inherit     # inherit | off
  clear-prior-phase-tools: true  # default true
next-phases:
  - id: review
    when: "Draft complete, ready for user review."
  - id: research
    when: "Source material is thin — gather more."
---

Prompt body. Tell the agent what this phase does and how to know when
to call phase_advance.
\`\`\`

#### Subagent phase

\`\`\`yaml
---
kind: subagent
tools: [tabstack_*, vault_read]
outputs: [sources.md]
next-phases:
  - id: draft
---

Prompt body — instructions for the subagent. The engine spawns a
child agent with the listed tools, runs this prompt, then verifies
the listed output files exist before advancing.
\`\`\`

Subagent phases must have **exactly one** `next-phases` edge and **no
gates** on outgoing edges (gates are user-facing; the user does not
see the subagent).

`subagent-skill: <name>` is the escape hatch — instead of an inline
prompt, the subagent activates a named skill.

#### Edge-level gates

\`\`\`yaml
next-phases:
  - id: publish
    when: "User has reviewed and approved the draft."
    gate:
      type: review
      message: "Approve the draft?"
      approve-label: "Looks good"
      deny-label: "Needs changes"
      on-deny: draft   # implicit on-approve = edge.id
\`\`\`

When the agent calls `phase_advance(publish, ...)`, the engine fires
the gate. Approve → transition to `publish`; Deny → transition to
`draft`.

## Engine tools

Always loaded:

| Tool | Purpose |
|---|---|
| `workflow_start(name, slug)` | Start a new run. |
| `workflow_list(workflow, status)` | List runs across all conversations. |
| `workflow_switch(run_id)` | Set the conversation's current run. |
| `workflow_status` | Show current run, valid transitions with `when:` text, recent history. |
| `workflow_artifact_read/write` | I/O scoped to the run's `artifacts/` directory. |

Dynamically injected when a run is active:

| Tool | Purpose |
|---|---|
| `phase_advance(target_phase_id, reason)` | Canonical transition tool. Schema enum + descriptions reflect the current phase's `next-phases`. |

## Validation

The loader rejects (logs warning, skips the workflow):

- Missing `workflow.initial-phase`
- Undefined edge targets (`next-phases.id` not in phases)
- Multi-edge phases missing `when:` on any edge
- Subagent phases with multiple edges or gated edges
- Subagent phases missing `outputs:` (unless `subagent-skill:` is set)
- Gate `on-deny` targets that don't exist

## Run state

Each run lives at `workspace/workflows/{name}/runs/{run-id}/` with
`state.json` (current phase, transition history, status) and
`artifacts/` (phase outputs). Runs survive across conversations —
`workflow_switch <run-id>` reattaches.

## Cross-phase context

Phase-boundary tool-result clearing (default on) prunes the prior
phase's tool outputs from the composer's view. To carry forward
non-trivial findings, instruct the agent to use the always-loaded
`notes_append` before calling `phase_advance` — notes survive both
tool clearing and compaction.

## Limitations (v1)

- Only `gate: review` is supported (no input widgets yet)
- Edges use LLM-routed `when:` strings; no code-evaluated conditions
- Workflows can't nest (no sub-workflows)
- `workflow_list` walks the filesystem — fine for tens of runs, not
  hundreds
```

- [ ] **Step 9.5: Link new doc in docs/index.md**

Add a line under the appropriate section in `docs/index.md`:

```markdown
- [workflows.md](workflows.md) — declarative multi-phase workflows via `kind: workflow` skills.
```

- [ ] **Step 9.6: Add convention note to CLAUDE.md**

Append a paragraph to the "Skills" section of the project `CLAUDE.md`:

```markdown
- **Workflow skills** ([docs/workflows.md](docs/workflows.md)) — `kind: workflow` in SKILL.md plus `phases/*.md` files declare a graph-based multi-phase task. Engine constrains tools + context per phase, dispatches subagents for `kind: subagent` phases, and routes edges via a dynamically-generated `phase_advance` enum. Bundled example: `skills/workflow_demo/` (`research_brief`).
```

- [ ] **Step 9.7: Write eval case**

```yaml
# evals/workflow_routing.yaml
name: Workflow Engine Routing
description: |
  Verify that the dynamic phase_advance enum + when: annotations
  steer the LLM to the right target in a branching phase. Uses the
  bundled research_brief workflow's draft phase, which has two edges
  (review when complete / gather when thin).

setup:
  max_tool_iterations: 8
  max_tool_errors: 2
  reflection_enabled: false

cases:
  - name: route_to_review_when_draft_complete
    messages:
      - role: user
        content: |
          Start a research_brief workflow on the topic "the history of
          movable type". The gather phase has already produced a
          rich sources.md with 6 sources and 4 themes — assume that's
          done. You are now in the draft phase. You've written a
          clear 500-word brief covering the topic with two strong
          themes and a closing open-questions section. The brief is
          satisfying and complete. What do you do next?
    expect_tool: phase_advance
    expect_tool_args:
      target_phase_id: review
    max_tool_calls: 3

  - name: route_to_gather_when_sources_thin
    messages:
      - role: user
        content: |
          You're in the draft phase of a research_brief workflow on
          "the cultural impact of moveable type printing". You opened
          the sources.md from the gather phase and found only 2
          sources, both about the mechanical engineering of the
          printing press with nothing on cultural impact. You can't
          write a meaningful brief from this. What do you do next?
    expect_tool: phase_advance
    expect_tool_args:
      target_phase_id: gather
    max_tool_calls: 3
```

- [ ] **Step 9.8: Run lint, typecheck, and full test suite one more time**

Run: `make check && make test`
Expected: clean

- [ ] **Step 9.9: Run the eval (real-LLM; may take 2-3 minutes)**

Run: `make eval ARGS="--file evals/workflow_routing.yaml"`
(Or whatever the project's eval-runner convention is — check `Makefile` first.)
Expected: both cases pass.

Note: if the eval framework requires the workflow to actually be running on the agent's machine to exercise `phase_advance`'s dynamic enum, this eval may need the bundled `research_brief` skill loaded — confirm against the eval runner's setup conventions.

- [ ] **Step 9.10: Commit demo + docs + eval**

```bash
git add src/decafclaw/skills/workflow_demo/ docs/workflows.md docs/index.md CLAUDE.md evals/workflow_routing.yaml
git commit -m "$(cat <<'EOF'
feat(workflow): demo workflow (research_brief), docs, eval case

Bundled research_brief workflow demonstrates every engine feature:
subagent phase with outputs, multi-edge inline phase with when:
routing, backward edge for revision loops, edge-level gate with
on-deny, terminal phase. Adds docs/workflows.md, indexes the new
doc, notes the convention in CLAUDE.md, and ships an eval case
guarding the phase_advance routing surface.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] **Step 10.1: Full test suite + eval-history check**

Run: `make test && make eval-history`
Expected: all tests pass, eval history shows no regressions.

- [ ] **Step 10.2: Smoke test the workflow end-to-end manually**

Start the agent (`make run` or `make dev` — coordinate with Les so only one instance is running). In a conversation:

```
!research_brief start The history of movable type
```

Walk through: gather (subagent), draft (inline routing), review (gated), publish (terminal). Verify:
- `workspace/workflows/research_brief/runs/<run-id>/state.json` updates at each step
- `artifacts/gather/sources.md` is produced by the subagent
- The Approve / Needs Changes buttons surface in the UI at the review gate
- `workflow_status` always shows the right next-phase options

Document any rough edges in `docs/dev-sessions/2026-05-19-2121-workflow-engine/notes.md` for the retro.

- [ ] **Step 10.3: Push branch + open PR**

```bash
git push -u origin feat/255-workflow-engine
gh pr create --title "feat(workflow): first-class workflow engine (#255)" --body "$(cat <<'EOF'
## Summary

Implements issue #255 — declarative multi-phase workflows authored as `kind: workflow` skills. Phases live in `phases/*.md` files with frontmatter (tools, kind, gate, next-phases with `when:`) and body prompt. Graph-based routing via a dynamically-regenerated `phase_advance` tool whose enum reflects the current phase's `next-phases`. Edge-level review gates, inline + subagent phase kinds, per-phase ContextComposer overrides, atomic run persistence at `workspace/workflows/{name}/runs/{run-id}/`.

Spec: [`docs/dev-sessions/2026-05-19-2121-workflow-engine/spec.md`](docs/dev-sessions/2026-05-19-2121-workflow-engine/spec.md)

Bundles a demo workflow (`research_brief`) that exercises every engine feature.

## Test plan

- [ ] `make test` — all green (new tests at `tests/test_workflow_*.py`)
- [ ] `make check` — lint + typecheck pass
- [ ] Manual smoke: `!research_brief start <topic>` walks through gather → draft → review → publish
- [ ] Run `evals/workflow_routing.yaml` — both cases pass

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Implementation notes for the executing engineer

- **Frequent commits**: every task ends in a commit. Don't batch.
- **TDD discipline**: every new function gets a failing test first. If the test order in this plan feels redundant for a tiny helper, write the test anyway — it's the discipline that catches regressions.
- **No `asyncio.sleep` in tests** (per CLAUDE.md). The only sleep in this plan is the `await asyncio.sleep(0)` yield in Task 2's lock test, which is the right primitive for proving lock serialization.
- **Workspace fixture**: each test creates its own `tmp_path / "workspace"` to avoid cross-test contamination. The `_workspace()` and `_ctx_for()` helpers in the test files are local — don't promote them to conftest.py until at least two test files need them.
- **Registry hygiene**: the workflow registry is module-global. Tests use `registry.clear()` in an `autouse` fixture to avoid bleed-through between tests. The skill loader populates it at startup; don't re-load workflows in production code.
- **The composer integration in Task 8 has one open implementation choice** (synthetic phase-boundary marker vs. scan-based detection). Default to the synthetic marker — write a `{"role": "workflow_phase_boundary", "from": ..., "to": ..., "timestamp": ...}` message into the conversation history at the moment of transition. The composer reads backwards from the end of history to find the most recent such marker, then calls `clear_tool_results_in_range(history, marker_idx + 1, current_idx, preserve_tools)`. This is cleaner than rescanning all assistant messages every turn.
- **Don't touch the `project` skill.** Per the spec, migration is explicitly out of scope. The workflow engine ships alongside, not in place of.
- **If any task fails ≥2 times the same way, stop and ask** (per CLAUDE.md). Don't pile retries on the same broken approach.
