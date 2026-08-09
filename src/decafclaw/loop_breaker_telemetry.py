"""Loop-breaker telemetry subscriber (#645).

A fail-open EventBus subscriber that appends one record per loop_breaker event
to ``{workspace}/telemetry/loop_breaker.jsonl`` (path/enable via
``config.telemetry``). Used to monitor autonomous tool-call thrash trips and
differentiate signal buckets ("repeat" vs "error_surge").

``make loop-breaker-stats`` (``python -m decafclaw.loop_breaker_telemetry``)
aggregates recent rows.
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


def _metrics_path(config) -> Path:
    return config.workspace_path / config.telemetry.loop_breaker_path


def record_from_event(event: dict) -> dict:
    """Build a record from a ``loop_breaker`` event (drops the type)."""
    rec = {k: v for k, v in event.items() if k != "type"}
    rec["timestamp"] = _now_iso()
    return rec


def append_record(config, record: dict) -> None:
    """Append one record as JSONL. Fail-open — never propagates."""
    try:
        path = _metrics_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:  # fail-open: telemetry must never break a turn
        log.debug("loop_breaker metrics write failed: %s", exc)


def make_loop_breaker_subscriber(config) -> Callable[[dict], Awaitable[None]]:
    """EventBus subscriber: records each ``loop_breaker`` event. Fail-open."""
    async def handle(event: dict) -> None:
        if not config.telemetry.loop_breaker_enabled:
            return
        try:
            if event.get("type") != "loop_breaker":
                return
            append_record(config, record_from_event(event))
        except Exception as exc:  # fail-open
            log.debug("loop_breaker metrics subscriber error: %s", exc)

    return handle


# -- reporting ----------------------------------------------------------------


def load_records(config) -> list[dict]:
    path = _metrics_path(config)
    if not path.exists():
        return []
    records = []
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


def aggregate(records: list[dict]) -> dict:
    """Aggregate loop_breaker stats over a list of records."""
    n = len(records)
    actions: dict[str, int] = defaultdict(int)
    signals: dict[str, int] = defaultdict(int)

    for r in records:
        action = r.get("action", "unknown")
        signal = r.get("signal", "unknown")
        actions[action] += 1
        signals[signal] += 1

    return {
        "total_events": n,
        "actions": dict(actions),
        "signals": dict(signals),
    }


def format_stats(stats: dict) -> str:
    lines = ["# Loop Breaker metrics", ""]
    lines.append(f"Events recorded: {stats['total_events']}")
    lines.append("")
    lines.append("By action (rung):")
    for action in ("nudge", "redirect", "stop"):
        lines.append(f"  {action:<15} {stats['actions'].get(action, 0)}")
    for other, count in sorted(stats["actions"].items()):
        if other not in ("nudge", "redirect", "stop"):
            lines.append(f"  {other:<15} {count}")

    lines.append("")
    lines.append("By signal (trip condition):")
    for signal in ("repeat", "error_surge"):
        lines.append(f"  {signal:<15} {stats['signals'].get(signal, 0)}")
    for other, count in sorted(stats["signals"].items()):
        if other not in ("repeat", "error_surge"):
            lines.append(f"  {other:<15} {count}")

    return "\n".join(lines)


def build_stats_report(config) -> str:
    return format_stats(aggregate(load_records(config)))


def main() -> None:
    from .config import load_config
    config = load_config()
    print(build_stats_report(config))


if __name__ == "__main__":
    main()
