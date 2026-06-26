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
    requires_skills: tuple[str, ...] = ()


REGISTRY: dict[str, WorkflowSpec] = {}


def workflow(
    name: str,
    *,
    model: str = "vertex-gemini-flash",
    requires_skills: tuple[str, ...] | list[str] = (),
):
    # Strings are sequences of characters in Python, so `tuple("tabstack")`
    # would silently produce `('t', 'a', 'b', 's', 't', 'a', 'c', 'k')` and
    # later fail activation in a baffling way. Catch the bare-string mistake
    # at decoration time (i.e., import time) with a clear suggestion.
    if isinstance(requires_skills, str):
        raise TypeError(
            f"@workflow({name!r}): requires_skills must be a sequence of "
            f"skill names, not a single string. Did you mean "
            f"requires_skills=({requires_skills!r},) ?")

    def deco(fn):
        if name in REGISTRY:
            raise ValueError(f"workflow {name!r} already registered")
        REGISTRY[name] = WorkflowSpec(
            name=name,
            fn=fn,
            model=model,
            requires_skills=tuple(requires_skills),
        )
        return fn
    return deco


def get_workflow(name: str) -> WorkflowSpec | None:
    return REGISTRY.get(name)


def workflow_commands() -> list[str]:
    """Names invocable as /<name>. Used by the command bridge."""
    return list(REGISTRY.keys())
