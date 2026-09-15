"""Prometheus metrics for the agent loop (#10).

In-memory counters and latency sums, scraped by ``GET /metrics`` in Prometheus
text format. Everything is derived from EventBus events the agent loop already
publishes (``llm_end``, ``tool_end``, ``loop_breaker``) — there are no metric
call sites in the loop itself.

Deliberately memory-only: Prometheus owns retention and querying on its side of
the scrape, so a durable sidecar here would be a second copy of data the scrape
target already has. Counters therefore reset on restart, which Prometheus
handles for ``rate()``/``increase()`` via counter-reset detection. Lifetime
totals across restarts are not available, by design.

Fail-open: a metrics error must never break a turn.
"""

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from decafclaw.tool_telemetry import infer_outcome

log = logging.getLogger(__name__)

# Keyed by metric name, then by the rendered label string:
# _prom_counters["llm_calls_total"]['model="opus-5"'] = 3.0
_prom_counters: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
_prom_sums: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
_prom_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))


def _escape_label_value(value: str) -> str:
    """Escape a label value for the Prometheus text exposition format.

    A raw double quote, backslash, or line feed produces a malformed line, and
    Prometheus fails the *entire* scrape on one parse error — so a single
    oddly-named model config would silently drop every metric, not just its
    own. Backslash first, or the escapes introduced below get re-escaped.
    """
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _format_labels(labels: dict[str, str]) -> str:
    return ",".join(f'{k}="{_escape_label_value(v)}"' for k, v in sorted(labels.items()))


def record_metric(
    metric_name: str,
    labels: dict[str, str],
    value: float,
    metric_type: str = "counter",
) -> None:
    """Fold one observation into the exposition state.

    Synchronous and allocation-only — safe to call from the event loop, which
    matters because ``EventBus.publish`` awaits its subscribers inline on the
    publishing coroutine.
    """
    label_str = _format_labels(labels)
    if metric_type == "counter":
        _prom_counters[metric_name][label_str] += value
    elif metric_type == "histogram":
        _prom_sums[f"{metric_name}_sum"][label_str] += value
        _prom_counts[f"{metric_name}_count"][label_str] += 1
    elif metric_type == "gauge":
        _prom_counters[metric_name][label_str] = value


def make_metrics_subscriber() -> Callable[[dict], Awaitable[None]]:
    """EventBus subscriber: turns ``llm_end`` / ``tool_end`` / ``loop_breaker``
    into metrics. Fail-open.
    """
    async def handle(event: dict) -> None:
        try:
            event_type = event.get("type")

            if event_type == "llm_end":
                model = event.get("model") or ""
                duration_ms = event.get("duration_ms") or 0.0
                record_metric("llm_call_latency_ms", {"model": model}, duration_ms, "histogram")
                record_metric("llm_calls_total", {"model": model}, 1, "counter")

            elif event_type == "tool_end":
                tool = event.get("tool") or ""
                outcome = infer_outcome(event.get("result_text") or "")
                duration_ms = event.get("duration_ms") or 0.0
                labels = {"tool": tool, "outcome": outcome}

                record_metric("tool_duration_ms", labels, duration_ms, "histogram")
                record_metric("tool_calls_total", labels, 1, "counter")
                if outcome == "error":
                    record_metric(
                        "errors_total", {"component": "tool", "name": tool}, 1, "counter")

            elif event_type == "loop_breaker":
                action = event.get("action") or ""
                record_metric("loop_breaker_trips_total", {"action": action}, 1, "counter")

        except Exception as exc:  # fail-open
            log.debug("metrics subscriber error: %s", exc)

    return handle


def format_prometheus_metrics() -> str:
    """Render the in-memory state in Prometheus text format."""
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
        base_name = metric_name[:-4]  # strip _sum
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
