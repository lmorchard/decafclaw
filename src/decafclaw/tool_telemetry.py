"""Tool-usage telemetry (#310).

A fail-open EventBus subscriber that appends one metadata-only JSONL record
per tool call to ``{workspace}/tool_usage.jsonl`` (path/enable via
``config.telemetry``). We only record names, sizes, counts, and outcome —
never tool args or return bodies.

The subscriber consumes ``tool_end`` events, which the publish site in
``tool_execution.py`` enriches with ``conv_id`` / ``duration_ms`` /
``input_bytes``. Outcome is inferred from the ``result_text`` prefix
(``[error…]`` / ``[cancelled…]``) since tool results encode failure as a
string, not a structured field.

``make tool-usage-report`` (``python -m decafclaw.tool_telemetry``) ranks
tools by calls and flags never-called ones as consolidation candidates.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_source(name: str, config) -> tuple[str, str]:
    """Classify a tool by origin → (source, detail).

    ``("mcp", server)`` for ``mcp__<server>__<tool>`` names, ``("skill",
    owner)`` for names in ``config.skill_tool_owners``, else ``("core", "")``.
    """
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        server = parts[1] if len(parts) >= 3 else "unknown"
        return ("mcp", server)
    owner = getattr(config, "skill_tool_owners", {}).get(name)
    if owner:
        return ("skill", owner)
    return ("core", "")


def infer_outcome(result_text: str) -> str:
    """Infer call outcome from the result-text prefix."""
    text = (result_text or "").lstrip()
    if text.startswith("[cancelled"):
        return "cancelled"
    if text.startswith("[error"):
        return "error"
    return "success"


def _usage_path(config) -> Path:
    return config.workspace_path / config.telemetry.tool_usage_path


def record_from_event(event: dict, config) -> dict:
    """Build a telemetry record from a ``tool_end`` event (metadata only)."""
    name = event.get("tool", "")
    source, detail = classify_source(name, config)
    result_text = event.get("result_text", "") or ""
    return {
        "timestamp": _now_iso(),
        "conv_id": event.get("conv_id", ""),
        "tool": name,
        "source": source,
        "source_detail": detail,
        "outcome": infer_outcome(result_text),
        "duration_ms": event.get("duration_ms", 0),
        "input_bytes": event.get("input_bytes", 0),
        "output_bytes": len(result_text.encode("utf-8")),
    }


def append_record(config, record: dict) -> None:
    """Append one record as JSONL. Fail-open — never propagates."""
    try:
        path = _usage_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:  # fail-open: telemetry must never break a turn
        log.debug("tool telemetry write failed: %s", exc)


def make_tool_telemetry_subscriber(config) -> Callable[[dict], Awaitable[None]]:
    """EventBus subscriber: records each ``tool_end`` event. Fail-open."""
    async def handle(event: dict) -> None:
        try:
            if event.get("type") != "tool_end":
                return
            append_record(config, record_from_event(event, config))
        except Exception as exc:  # fail-open
            log.debug("tool telemetry subscriber error: %s", exc)

    return handle


# -- reporting ----------------------------------------------------------------


def load_records(config) -> list[dict]:
    path = _usage_path(config)
    if not path.exists():
        return []
    records = []
    # Stream line-by-line — the log is append-only and unrotated, so avoid
    # materializing the whole file as one string + a splitlines list.
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def aggregate(records: list[dict]) -> dict[str, dict]:
    """Aggregate per-tool stats: calls, unique convs, errors, last-called."""
    calls: dict[str, int] = defaultdict(int)
    errors: dict[str, int] = defaultdict(int)
    convs: dict[str, set] = defaultdict(set)
    last: dict[str, str] = {}
    source: dict[str, str] = {}
    for r in records:
        tool = r.get("tool", "")
        calls[tool] += 1
        if r.get("outcome") == "error":
            errors[tool] += 1
        conv = r.get("conv_id")
        if conv:
            convs[tool].add(conv)
        ts = r.get("timestamp", "")
        if ts and ts > last.get(tool, ""):
            last[tool] = ts
        source.setdefault(tool, r.get("source", ""))
    stats = {}
    for tool in calls:
        n = calls[tool]
        stats[tool] = {
            "calls": n,
            "unique_convs": len(convs[tool]),
            "errors": errors[tool],
            "error_rate": errors[tool] / n if n else 0.0,
            "last_called": last.get(tool, ""),
            "source": source.get(tool, ""),
        }
    return stats


def known_tool_names(config) -> set[str]:
    """All tool names we can enumerate offline: core + skill-owned.

    MCP tools are only knowable when their servers are connected, so
    unused-MCP detection is out of reach for this offline report.
    """
    from .tools import TOOL_DEFINITIONS
    names = {td.get("function", {}).get("name", "") for td in TOOL_DEFINITIONS}
    names |= set(getattr(config, "skill_tool_owners", {}).keys())
    names.discard("")
    return names


def format_report(stats: dict[str, dict], unused: set[str]) -> str:
    lines = ["# Tool usage report", ""]
    total = sum(s["calls"] for s in stats.values())
    lines.append(f"Total calls recorded: {total} across {len(stats)} tools")
    lines.append("")
    lines.append(f"{'tool':<32} {'calls':>6} {'convs':>6} {'err%':>6}  last-called")
    lines.append("-" * 72)
    for tool, s in sorted(stats.items(), key=lambda kv: kv[1]["calls"], reverse=True):
        lines.append(
            f"{tool:<32} {s['calls']:>6} {s['unique_convs']:>6} "
            f"{s['error_rate'] * 100:>5.0f}%  {s['last_called']}"
        )
    lines.append("")
    lines.append(f"## Unused tools ({len(unused)}) — consolidation candidates")
    lines.append("(core + skill tools never called; MCP tools not covered offline)")
    if unused:
        for name in sorted(unused):
            lines.append(f"  - {name}")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def build_report(config) -> str:
    records = load_records(config)
    stats = aggregate(records)
    unused = known_tool_names(config) - set(stats.keys())
    return format_report(stats, unused)


def main() -> None:
    from .config import load_config
    config = load_config()
    print(build_report(config))


if __name__ == "__main__":
    main()
