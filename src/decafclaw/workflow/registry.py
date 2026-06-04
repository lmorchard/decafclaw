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
