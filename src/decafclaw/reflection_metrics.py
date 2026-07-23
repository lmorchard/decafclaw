"""Reflection cost/effectiveness telemetry (#409).

A fail-open EventBus subscriber that appends one **metadata-only** JSONL
record per judge-eligible turn to ``{workspace}/reflection/metrics.jsonl``
(path/enable via ``config.telemetry``). It answers "does reflection earn its
keep" with data: pass-first fraction (pure overhead), loop-exhausted fraction
(waste), whether retries meaningfully change the answer (genuine value), and
judge token cost.

Privacy: we record the outcome bucket, retry count, judge token totals, a
first-vs-final response *delta* (lengths + token-overlap ratio — not the
bodies), and a short critique *fingerprint* (first ~120 chars, to spot
repeating rejection patterns). No response bodies or prompt contents.

``make reflection-stats`` (``python -m decafclaw.reflection_metrics``)
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

FINGERPRINT_MAX = 120


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def response_delta(first: str, final: str) -> tuple[int, float]:
    """Compare the first and final response cheaply.

    Returns ``(char_delta, overlap_ratio)`` where char_delta is the signed
    length change and overlap_ratio is the Jaccard overlap of whitespace
    tokens (1.0 = identical wording, 0.0 = disjoint). Two empty strings count
    as fully overlapping.
    """
    char_delta = len(final) - len(first)
    a, b = set(first.split()), set(final.split())
    union = a | b
    overlap = len(a & b) / len(union) if union else 1.0
    return char_delta, round(overlap, 4)


def classify_outcome(*, first_response: str | None, last_error: str,
                     retry_count: int, exhausted: bool,
                     final_content: str) -> str | None:
    """Decide the outcome bucket for a judge-eligible turn.

    Returns ``None`` when no row should be emitted (eligible turn that never
    reached the judge and produced a non-empty answer — e.g. an end_turn
    path). Callers pass state captured on the turn runner.
    """
    if first_response is None:
        # Judge never evaluated. The only reflect-gate decline that reaches a
        # non-child, non-cancelled final is empty content; anything else isn't
        # a reflection turn.
        if not (final_content or "").strip():
            return "skipped_empty"
        return None
    if exhausted:
        return "loop_exhausted"
    if last_error:
        return "errored"
    if retry_count == 0:
        return "passed_first"
    return "passed_after_retry"


def _metrics_path(config) -> Path:
    return config.workspace_path / config.telemetry.reflection_metrics_path


def record_from_event(event: dict) -> dict:
    """Build a record from a ``reflection_turn`` event (drops the type)."""
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
        log.debug("reflection metrics write failed: %s", exc)


def make_reflection_metrics_subscriber(config) -> Callable[[dict], Awaitable[None]]:
    """EventBus subscriber: records each ``reflection_turn`` event. Fail-open."""
    async def handle(event: dict) -> None:
        try:
            if event.get("type") != "reflection_turn":
                return
            append_record(config, record_from_event(event))
        except Exception as exc:  # fail-open
            log.debug("reflection metrics subscriber error: %s", exc)

    return handle


# -- reporting ----------------------------------------------------------------


def load_records(config) -> list[dict]:
    path = _metrics_path(config)
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


def aggregate(records: list[dict]) -> dict:
    """Aggregate reflection stats over a list of records."""
    n = len(records)
    buckets: dict[str, int] = defaultdict(int)
    total_retries = 0
    total_judge_tokens = 0
    for r in records:
        buckets[r.get("outcome", "unknown")] += 1
        total_retries += r.get("retry_count", 0)
        total_judge_tokens += (r.get("judge_prompt_tokens", 0)
                               + r.get("judge_completion_tokens", 0))
    return {
        "total_turns": n,
        "buckets": dict(buckets),
        "pass_first_rate": buckets["passed_first"] / n if n else 0.0,
        "loop_exhausted_rate": buckets["loop_exhausted"] / n if n else 0.0,
        "mean_retries": total_retries / n if n else 0.0,
        "total_judge_tokens": total_judge_tokens,
        "mean_judge_tokens": total_judge_tokens / n if n else 0.0,
    }


def format_stats(stats: dict) -> str:
    lines = ["# Reflection metrics", ""]
    lines.append(f"Turns recorded: {stats['total_turns']}")
    lines.append("")
    lines.append("Outcome buckets:")
    for bucket in ("passed_first", "passed_after_retry", "loop_exhausted",
                   "errored", "skipped_empty"):
        lines.append(f"  {bucket:<20} {stats['buckets'].get(bucket, 0)}")
    for other, count in sorted(stats["buckets"].items()):
        if other not in ("passed_first", "passed_after_retry", "loop_exhausted",
                         "errored", "skipped_empty"):
            lines.append(f"  {other:<20} {count}")
    lines.append("")
    lines.append(f"pass-first rate:     {stats['pass_first_rate'] * 100:.1f}%  (pure overhead)")
    lines.append(f"loop-exhausted rate: {stats['loop_exhausted_rate'] * 100:.1f}%  (waste + bad UX)")
    lines.append(f"mean retries:        {stats['mean_retries']:.2f}")
    lines.append(f"judge tokens total:  {stats['total_judge_tokens']:,}")
    lines.append(f"judge tokens/turn:   {stats['mean_judge_tokens']:.0f}")
    return "\n".join(lines)


def build_stats_report(config) -> str:
    return format_stats(aggregate(load_records(config)))


def main() -> None:
    from .config import load_config
    config = load_config()
    print(build_stats_report(config))


if __name__ == "__main__":
    main()
