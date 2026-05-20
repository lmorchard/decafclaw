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
        # str-Enum already serializes as its value via json.dumps, but
        # we set it explicitly so round-trip stays correct if RunStatus
        # is ever changed to not inherit from str.
        d["status"] = self.status.value
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> RunState:
        d = json.loads(raw)
        d["status"] = RunStatus(d["status"])
        return cls(**d)
