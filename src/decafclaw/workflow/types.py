"""Workflow step-primitive engine — core type definitions.

Types here are the shared vocabulary between loader, engine, step_executors,
and conv_state. Keep this module import-clean: no decafclaw internals beyond
stdlib and dataclasses.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class StepKind(str, Enum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    USER_INPUT = "user_input"
    ROUTE = "route"
    SUBAGENT = "subagent"
    PYTHON = "python"


class RunStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    PAUSED_USER_INPUT = "paused_user_input"   # renamed from PAUSED_GATE
    PAUSED_SUBAGENT = "paused_subagent"


@dataclass(frozen=True)
class EdgeRef:
    """A ``to: step_id`` reference with an optional ``if:`` Jinja condition."""
    to: str             # target step id; "" = terminal
    if_expr: str = ""   # empty = unconditional / default fallback


@dataclass(frozen=True)
class RouteChoice:
    """One option in a route/user_input choices list."""
    id: str             # enum value
    to: str             # target step id; "" = terminal
    when: str = ""      # LLM-facing description (route) or label hint
    label: str = ""     # button label (user_input choice mode only)


@dataclass(frozen=True)
class StepDef:
    """Immutable definition of a single workflow step."""
    id: str
    kind: StepKind
    config: dict[str, Any]          # kind-specific config (schema, prompt, tool, args, …)
    next_edges: tuple[EdgeRef, ...] = ()    # for non-route, non-user_input kinds
    choices: tuple[RouteChoice, ...] = ()   # for route + user_input(choice) kinds
    description: str = ""           # author-facing docstring


@dataclass
class WorkflowState:
    """Mutable runtime state for one workflow run in one conversation.

    Persisted to JSON via to_json/from_json. The ``state`` dict is
    keyed by step_id — each entry is whatever the step executor returned.
    The ``transitions`` list is the engine-internal step log (not
    visible to workflow authors via Jinja templates).
    """
    workflow: str           # workflow name (matches WorkflowDef.name)
    run_id: str             # uuid
    conv_id: str
    initial_step: str
    current_step: str
    status: RunStatus
    state: dict[str, Any]   # step_id → step output
    transitions: list[dict] # engine-internal step transition log
    pending: dict[str, Any] = field(default_factory=dict)  # pause-state
    updated_at: str = ""    # ISO-8601 timestamp, written by conv_state helpers

    def to_json(self) -> str:
        data = dataclasses.asdict(self)
        data["status"] = self.status.value
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, text: str) -> WorkflowState:
        data = json.loads(text)
        data["status"] = RunStatus(data["status"])
        return cls(**data)


@dataclass(frozen=True)
class WorkflowDef:
    """Immutable parsed workflow definition. Built by the loader."""
    name: str
    description: str
    initial_step: str
    steps: tuple[StepDef, ...]
    skill_dir: Path | None           # filesystem path to the skill directory

    @property
    def steps_by_id(self) -> dict[str, StepDef]:
        return {s.id: s for s in self.steps}
