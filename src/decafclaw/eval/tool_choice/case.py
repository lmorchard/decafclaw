"""Tool-choice eval case schema and YAML loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Case:
    """One disambiguation scenario.

    See docs/eval-loop.md for authoring guidance.
    """
    name: str
    scenario: str           # the user message the model sees
    expected: str           # the single tool name asserted to be correct
    near_miss: list[str] = field(default_factory=list)  # ≥1 tool to compare against
    notes: str = ""


_REQUIRED_FIELDS = ("name", "scenario", "expected", "near_miss")


def _parse_one(raw: dict, source: Path) -> Case:
    missing = [f for f in _REQUIRED_FIELDS if f not in raw or not raw[f]]
    if missing:
        raise ValueError(
            f"{source}: case missing required field(s) {missing}: {raw!r}"
        )
    near_miss = raw["near_miss"]
    if not isinstance(near_miss, list) or not near_miss:
        raise ValueError(
            f"{source}: 'near_miss' must be a non-empty list of tool names "
            f"(got {near_miss!r})"
        )
    return Case(
        name=str(raw["name"]),
        scenario=str(raw["scenario"]),
        expected=str(raw["expected"]),
        near_miss=[str(t) for t in near_miss],
        notes=str(raw.get("notes", "")),
    )


def load_cases(path: Path) -> list[Case]:
    """Load tool-choice cases from a YAML file or directory of YAMLs.

    Each YAML is a list of mappings; each mapping must have the
    required fields (``name``, ``scenario``, ``expected``,
    ``near_miss``). ``notes`` is optional. Raises ``ValueError`` on
    malformed input.
    """
    if path.is_dir():
        files = sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml"))
    else:
        files = [path]

    cases: list[Case] = []
    for file in files:
        with file.open() as f:
            raw = yaml.safe_load(f)
        if raw is None:
            continue
        if not isinstance(raw, list):
            raise ValueError(
                f"{file}: top-level YAML must be a list of cases (got {type(raw).__name__})"
            )
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError(f"{file}: case entries must be mappings, got {item!r}")
            cases.append(_parse_one(item, file))
    return cases
