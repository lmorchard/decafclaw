"""Generated, read-only inventory of eval suites (#1834).

Derived from the YAML at read time — never stored — so there is no
inventory document to drift. Case counts and axis tags come from parsed
YAML (not grep), and the freshness columns come from git and
``evals/history.jsonl``.

Invoked from ``decafclaw.eval.__main__`` via ``--inventory`` (and
``--inventory --json``); see ``make eval-inventory``. Reporting only: this
module never writes and is deliberately not part of ``make check``.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

import yaml

from .diagnostics import parse_axes
from .history import HISTORY_PATH, read_history

DASH = "—"

# Config override paths that make a case depend on which model/provider
# runs it. Prefixes match dotted paths (``providers.vertex.project``), so a
# whole ``providers`` or ``model_configs`` map set as a literal counts too.
MODEL_OVERRIDE_PREFIXES = (
    "providers",
    "model_configs",
    "default_model",
    "llm.model",
    "llm.url",
    "reflection.model",
    "reflection.verifier_model",
    "reflection.url",
)


@dataclass(frozen=True)
class SuiteInfo:
    """One row of the inventory."""

    file: str  # POSIX path relative to the evals root
    tier: str  # choice | smoke | behavioral
    cases: int
    axes: tuple[str, ...]  # sorted union of parsed ``tests:`` tags
    untagged: bool
    model_gated: int  # cases overriding a model/provider config field
    last_touched: str
    last_run: str | None
    pass_rate: float | None

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "tier": self.tier,
            "cases": self.cases,
            "axes": list(self.axes),
            "untagged": self.untagged,
            "model_gated": self.model_gated,
            "last_touched": self.last_touched,
            "last_run": self.last_run,
            "pass_rate": self.pass_rate,
        }


def load_suite_cases(path: Path) -> list[dict]:
    """Parse one suite YAML into its case mappings.

    The runner treats a top-level list as cases; anything else is an
    authoring error the inventory should surface rather than silently
    reporting zero.
    """
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{path}: top-level YAML must be a list of cases (got {type(raw).__name__})")
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"{path}: case entries must be mappings, got {type(item).__name__}")
    return raw


def suite_tier(rel: PurePosixPath, case_count: int) -> str:
    """Derive the tier from location and size (never hand-maintained)."""
    if rel.parts and rel.parts[0] == "tool_choice":
        return "choice"
    if case_count == 1:
        return "smoke"
    return "behavioral"


def _is_model_override(key: str) -> bool:
    return any(key == prefix or key.startswith(prefix + ".") for prefix in MODEL_OVERRIDE_PREFIXES)


def count_model_gated(cases: list[dict]) -> int:
    """Cases whose ``setup.config_overrides`` touch a model/provider field."""
    gated = 0
    for case in cases:
        setup = case.get("setup") or {}
        if not isinstance(setup, dict):
            continue
        overrides = setup.get("config_overrides") or {}
        if isinstance(overrides, dict) and any(_is_model_override(str(key)) for key in overrides):
            gated += 1
    return gated


def git_last_touched(path: Path) -> str:
    """Committer date of the last commit touching ``path``, or ``—``."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return DASH
    return result.stdout.strip() or DASH


def _history_lookup(rel: str, records: list[dict]) -> tuple[str | None, float | None]:
    """Latest run timestamp and per-file pass rate, oldest record first.

    ``history.jsonl`` stores per-file keys as basenames today; also accept
    the relative path in case that format gets richer later.
    """
    name = PurePosixPath(rel).name
    for record in reversed(records):
        per_file = record.get("per_file") or {}
        counts = per_file.get(rel) or per_file.get(name)
        if not counts:
            continue
        total = counts.get("total") or 0
        passed = counts.get("passed") or 0
        rate = round(passed / total, 4) if total else None
        return record.get("timestamp"), rate
    return None, None


def build_inventory(
    root: Path = Path("evals"),
    *,
    history_path: Path = HISTORY_PATH,
    last_touched: Callable[[Path], str] = git_last_touched,
) -> list[SuiteInfo]:
    """Summarize every ``*.yaml`` suite under ``root``, sorted by path.

    ``root`` may also be a single YAML file, matching the runner's path
    argument.
    """
    root = Path(root)
    if root.is_file():
        paths, base = [root], root.parent
    else:
        paths, base = sorted(root.rglob("*.yaml")), root
    records = read_history(history_path)
    suites: list[SuiteInfo] = []
    for path in paths:
        rel = path.relative_to(base).as_posix()
        cases = load_suite_cases(path)
        axes = sorted({axis for case in cases for axis in parse_axes(case)})
        last_run, pass_rate = _history_lookup(rel, records)
        suites.append(
            SuiteInfo(
                file=rel,
                tier=suite_tier(PurePosixPath(rel), len(cases)),
                cases=len(cases),
                axes=tuple(axes),
                untagged=not axes,
                model_gated=count_model_gated(cases),
                last_touched=last_touched(path),
                last_run=last_run,
                pass_rate=pass_rate,
            )
        )
    return suites


def render_table(suites: list[SuiteInfo]) -> str:
    """Human-readable table. Never a failing gate — output may change freely."""
    if not suites:
        return "No eval suites found under the given path.\n"

    headers = ("File", "Tier", "Cases", "Axes", "Model-gated", "Last touched", "Last run", "Pass rate")
    rows = [
        (
            s.file,
            s.tier,
            str(s.cases),
            ", ".join(s.axes) if s.axes else "untagged",
            str(s.model_gated),
            s.last_touched or DASH,
            s.last_run or DASH,
            f"{s.pass_rate:.1%}" if s.pass_rate is not None else DASH,
        )
        for s in suites
    ]
    widths = [max(len(row[i]) for row in (headers, *rows)) for i in range(len(headers))]

    def _line(values: tuple[str, ...]) -> str:
        return "  ".join(value.ljust(width) for value, width in zip(values, widths))

    lines = [_line(headers), "  ".join("-" * width for width in widths)]
    lines.extend(_line(row) for row in rows)
    lines.append("")
    lines.append(f"{len(suites)} suites, {sum(s.cases for s in suites)} cases")
    return "\n".join(lines) + "\n"


def render_json(suites: list[SuiteInfo]) -> str:
    """Stable machine-readable output for a later dashboard."""
    payload = {
        "suites": [s.to_dict() for s in suites],
        "totals": {"suites": len(suites), "cases": sum(s.cases for s in suites)},
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
