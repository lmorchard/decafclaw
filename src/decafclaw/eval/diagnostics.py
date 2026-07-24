"""Deterministic helpers for eval axis tagging + per-turn context diagnostics.

Pure functions — no LLM calls, no I/O. `runner.py` wires these into the eval
result bundle. Per-turn diagnostics reuse the context sidecar written by
`agent.py:_write_diagnostics` (see #531); axis aggregation powers the
failure-mode scorecard (see #528).
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

CANONICAL_AXES: frozenset[str] = frozenset(
    {"retrieval", "routing", "answer_quality", "workflow_discipline"}
)

# Read-shaped tool calls whose named arg identifies the file/page read.
READ_TOOL_ARGS: dict[str, str] = {"vault_read": "page", "workspace_read": "path"}

_WIKI_RE = re.compile(r"\[\[([^\]]+)\]\]")


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


def aggregate_by_axis(test_results: list[dict], cases: list[dict]) -> dict:
    """Pass-rate per failure-mode axis for the #528 scorecard.

    ``test_results[i]`` pairs with ``cases[i]`` by index. A case's axes come
    from :func:`parse_axes`; an untagged case counts toward ``"untagged"``. A
    multi-axis case counts once toward each of its axes (so summed axis totals
    may exceed the test count). Only axes that actually appear are emitted.
    """
    buckets: dict[str, dict] = {}
    for result, case in zip(test_results, cases):
        axes = parse_axes(case) or ["untagged"]
        passed = result.get("status") == "pass"
        for axis in axes:
            b = buckets.setdefault(
                axis, {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0}
            )
            b["total"] += 1
            b["passed"] += 1 if passed else 0
            b["failed"] += 0 if passed else 1
    for b in buckets.values():
        b["pass_rate"] = round(b["passed"] / b["total"], 4) if b["total"] else 0.0
    return buckets


def detect_files_read(tool_calls: list[tuple[str, dict]]) -> list[str]:
    """File/page identifiers read this turn, from read-shaped tool calls.

    ``tool_calls`` is the ``(name, parsed_args)`` list from
    ``runner._collect_tool_calls``. Order-preserving, deduped, empties dropped.
    """
    out: list[str] = []
    for name, args in tool_calls:
        arg_key = READ_TOOL_ARGS.get(name)
        if not arg_key:
            continue
        value = args.get(arg_key)
        if value and value not in out:
            out.append(value)
    return out


def detect_files_cited(response: str, known_paths: list[str]) -> list[str]:
    """Heuristic: which known files/pages the final response cites.

    A ``known_path`` counts as cited if its full path, basename, or stem
    (case-insensitive) is a substring of ``response``. Additionally, every
    ``[[PageName]]`` wiki-mention in the response is included. Documented as a
    heuristic — substring matching can over- or under-count (#531).
    """
    lower = response.lower()
    cited: list[str] = []
    for path in known_paths:
        p = PurePosixPath(path)
        needles = {path.lower(), p.name.lower(), p.stem.lower()}
        if any(n and n in lower for n in needles):
            if path not in cited:
                cited.append(path)
    for m in _WIKI_RE.findall(response):
        name = m.strip()
        if name and name not in cited:
            cited.append(name)
    return cited
