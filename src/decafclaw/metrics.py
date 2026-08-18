import asyncio
import json
import logging
import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decafclaw.tool_telemetry import infer_outcome

log = logging.getLogger(__name__)

# In-memory metrics for Prometheus (using simple thread-safe structs since asyncio is single-threaded)
# Format: _prom_metrics["metric_name"]["label1=val1,label2=val2"] = value
# For histograms/summaries, we can store _sum and _count
_prom_counters: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
_prom_sums: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
_prom_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db(config) -> sqlite3.Connection:
    path = config.workspace_path / "metrics.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            metric_name TEXT,
            labels TEXT,
            value REAL
        )
        """
    )
    return conn


def record_metric(config: Any, metric_name: str, labels: dict[str, str], value: float, metric_type: str = "counter") -> None:
    """Record a metric to Prometheus in-memory structs, SQLite, and JSONL log."""
    # 1. Prometheus in-memory
    label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))

    if metric_type == "counter":
        _prom_counters[metric_name][label_str] += value
    elif metric_type == "histogram":
        _prom_sums[f"{metric_name}_sum"][label_str] += value
        _prom_counts[f"{metric_name}_count"][label_str] += 1
        # also update a generic counter for simple access if needed
    elif metric_type == "gauge":
        _prom_counters[metric_name][label_str] = value

    # 2. SQLite
    try:
        conn = _get_db(config)
        conn.execute(
            "INSERT INTO metrics (timestamp, metric_name, labels, value) VALUES (?, ?, ?, ?)",
            (_now_iso(), metric_name, json.dumps(labels), value)
        )
        conn.commit()
    except Exception as exc:
        log.debug("metrics sqlite write failed: %s", exc)

    # 3. JSONL
    try:
        path = config.workspace_path / "metrics.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            record = {
                "timestamp": _now_iso(),
                "metric_name": metric_name,
                "labels": labels,
                "value": value,
                "type": metric_type,
            }
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        log.debug("metrics jsonl write failed: %s", exc)


def make_metrics_subscriber(config) -> Callable:
    """EventBus subscriber for metrics."""
    async def handle(event: dict) -> None:
        try:
            event_type = event.get("type")

            if event_type == "llm_end":
                model = event.get("model", "unknown")
                duration_ms = event.get("duration_ms", 0.0)
                record_metric(config, "llm_call_latency_ms", {"model": model}, duration_ms, "histogram")
                record_metric(config, "llm_calls_total", {"model": model}, 1, "counter")

            elif event_type == "tool_end":
                tool = event.get("tool", "unknown")
                result_text = event.get("result_text", "")
                outcome = infer_outcome(result_text)
                duration_ms = event.get("duration_ms", 0.0)

                record_metric(config, "tool_duration_ms", {"tool": tool, "outcome": outcome}, duration_ms, "histogram")
                record_metric(config, "tool_calls_total", {"tool": tool, "outcome": outcome}, 1, "counter")
                if outcome == "error":
                    record_metric(config, "errors_total", {"component": "tool", "name": tool}, 1, "counter")

            elif event_type == "loop_breaker":
                action = event.get("action", "unknown")
                record_metric(config, "loop_breaker_trips_total", {"action": action}, 1, "counter")

        except Exception as exc:
            log.debug("metrics subscriber error: %s", exc)

    return handle


def format_prometheus_metrics() -> str:
    """Return metrics in Prometheus text format."""
    lines = []

    for metric_name, labels_dict in _prom_counters.items():
        if metric_name.endswith("_total"):
            lines.append(f"# TYPE {metric_name} counter")
        else:
            lines.append(f"# TYPE {metric_name} gauge")

        for label_str, val in labels_dict.items():
            if label_str:
                lines.append(f"{metric_name}{{{label_str}}} {val}")
            else:
                lines.append(f"{metric_name} {val}")

    for metric_name, labels_dict in _prom_sums.items():
        base_name = metric_name[:-4]  # remove _sum
        lines.append(f"# TYPE {base_name} histogram")
        for label_str, val in labels_dict.items():
            count_name = f"{base_name}_count"
            count = _prom_counts[count_name][label_str]
            if label_str:
                lines.append(f"{metric_name}{{{label_str}}} {val}")
                lines.append(f"{count_name}{{{label_str}}} {count}")
            else:
                lines.append(f"{metric_name} {val}")
                lines.append(f"{count_name} {count}")

    return "\n".join(lines) + "\n"
