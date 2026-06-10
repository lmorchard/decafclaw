"""Workflow registry + @workflow decorator.

A workflow is its own first-class concept — NOT a skill. It borrows only
the command-invocation plumbing. Orchestrators register at import time.
"""
import dataclasses
from typing import Any, Awaitable, Callable


@dataclasses.dataclass
class WorkflowSpec:
    name: str
    fn: Callable[[Any], Awaitable[Any]]
    model: str = "vertex-gemini-flash"


REGISTRY: dict[str, WorkflowSpec] = {}


def workflow(name: str, *, model: str = "vertex-gemini-flash"):
    def deco(fn):
        if name in REGISTRY:
            raise ValueError(f"workflow {name!r} already registered")
        REGISTRY[name] = WorkflowSpec(name=name, fn=fn, model=model)
        return fn
    return deco


def get_workflow(name: str) -> WorkflowSpec | None:
    return REGISTRY.get(name)


def workflow_commands() -> list[str]:
    """Names invocable as /<name>. Used by the command bridge."""
    return list(REGISTRY.keys())
