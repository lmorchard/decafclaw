"""Deterministic helpers for eval axis tagging + per-turn context diagnostics.

Pure functions — no LLM calls, no I/O. `runner.py` wires these into the eval
result bundle. Per-turn diagnostics reuse the context sidecar written by
`agent.py:_write_diagnostics` (see #531); axis aggregation powers the
failure-mode scorecard (see #528).
"""

from __future__ import annotations

CANONICAL_AXES: frozenset[str] = frozenset(
    {"retrieval", "routing", "answer_quality", "workflow_discipline"}
)

# Read-shaped tool calls whose named arg identifies the file/page read.
READ_TOOL_ARGS: dict[str, str] = {"vault_read": "page", "workspace_read": "path"}


def parse_axes(case: dict) -> list[str]:
    """Return the axis tags declared on an eval case's top-level ``tests:`` key.

    Absent → ``[]``. A bare string → a one-element list. A list passes through.
    Every value must be in :data:`CANONICAL_AXES`; an unknown value raises
    ``ValueError`` rather than silently vanishing from the scorecard (mirrors
    #663's strict ``setup.*`` handling).
    """
    raw = case.get("tests")
    if raw is None:
        return []
    axes = [raw] if isinstance(raw, str) else list(raw)
    for axis in axes:
        if axis not in CANONICAL_AXES:
            raise ValueError(
                f"unknown axis {axis!r} in tests: — must be one of "
                f"{sorted(CANONICAL_AXES)}"
            )
    return axes
