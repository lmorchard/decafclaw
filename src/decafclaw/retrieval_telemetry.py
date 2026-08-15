"""Retrieval-event telemetry (#197 Phase 0 — measurement foundation for the
self-improving vault arc).

A fail-open EventBus subscriber that appends one JSONL record per
``retrieval_event`` to ``{workspace}/telemetry/retrieval.jsonl`` (path/enable
via ``config.telemetry``). Each event carries the *full* scored candidate
list considered during a turn's memory retrieval — not just what got
injected — tagged with an ``included`` verdict and, for dropped candidates,
a ``drop_reason`` (``"score"`` for below the composite-score threshold,
``"budget"`` for trimmed by the token budget). Published once per
interactive turn from ``ContextComposer._compose_vault_retrieval``.

This is Phase 0 of #197: later phases (notably Phase 5's importance
formula) consume the aggregated form of this stream to learn which vault
pages are actually useful versus dead weight.

``make retrieval-report`` (``python -m decafclaw.retrieval_telemetry``)
aggregates per-page retrieval/include/drop counts alongside a point-in-time
vault-health snapshot (frontmatter coverage).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from .telemetry_rotation import rotate_if_needed

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retrieval_path(config) -> Path:
    return config.workspace_path / config.telemetry.retrieval_path


def record_from_event(event: dict) -> dict:
    """Build a telemetry record from a ``retrieval_event`` event."""
    return {
        "timestamp": _now_iso(),
        "conv_id": event.get("conv_id", ""),
        "candidates": event.get("candidates", []),
    }


def append_record(config, record: dict) -> None:
    """Append one record as JSONL. Fail-open — never propagates."""
    try:
        path = _retrieval_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        rotate_if_needed(path, getattr(config.telemetry, "retention_days", 30))
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:  # fail-open: telemetry must never break a turn
        log.debug("retrieval telemetry write failed: %s", exc)


def make_retrieval_telemetry_subscriber(config) -> Callable[[dict], Awaitable[None]]:
    """EventBus subscriber: records each ``retrieval_event`` event. Fail-open."""
    async def handle(event: dict) -> None:
        try:
            if event.get("type") != "retrieval_event":
                return
            append_record(config, record_from_event(event))
        except Exception as exc:  # fail-open
            log.debug("retrieval telemetry subscriber error: %s", exc)

    return handle


# -- reporting ----------------------------------------------------------------


def load_records(config) -> list[dict]:
    path = _retrieval_path(config)
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
    """Aggregate per-page stats: retrieval_count, include_count, drop-by-reason.

    ``retrieval_count`` is how many times a page appeared as a scored
    candidate (whether or not it survived); ``include_count`` is how many
    of those it actually survived the score threshold and budget trim.
    """
    retrieval_count: dict[str, int] = defaultdict(int)
    include_count: dict[str, int] = defaultdict(int)
    drop_score: dict[str, int] = defaultdict(int)
    drop_budget: dict[str, int] = defaultdict(int)
    source_type: dict[str, str] = {}
    for r in records:
        for c in r.get("candidates", []):
            path = c.get("file_path", "")
            if not path:
                continue
            retrieval_count[path] += 1
            source_type.setdefault(path, c.get("source_type", ""))
            if c.get("included"):
                include_count[path] += 1
            else:
                reason = c.get("drop_reason")
                if reason == "score":
                    drop_score[path] += 1
                elif reason == "budget":
                    drop_budget[path] += 1
    stats = {}
    for path in retrieval_count:
        n = retrieval_count[path]
        stats[path] = {
            "retrieval_count": n,
            "include_count": include_count[path],
            "include_rate": include_count[path] / n if n else 0.0,
            "drop_score": drop_score[path],
            "drop_budget": drop_budget[path],
            "source_type": source_type.get(path, ""),
        }
    return stats


def _empty_vault_health() -> dict:
    return {
        "total_pages": 0,
        "with_importance": 0,
        "coverage_pct": 0.0,
        "missing_importance": 0,
        "graph_orphans": 0,
    }


def vault_health(config) -> dict:
    """Point-in-time vault-health snapshot: frontmatter coverage + graph orphans.

    Reads pages directly from the vault (not the telemetry log). Two
    distinct metrics, easy to conflate (#197 P0-M2 — the original
    ``orphans`` name below was misleading):

    - ``missing_importance``: pages with no ``importance`` frontmatter
      field. Feeds importance-coverage tracking, not the link graph.
    - ``graph_orphans``: pages with zero inbound ``[[wiki-links]]``, per
      the persistent backlink index (``backlinks.load_index``). This is
      the genuine graph-orphan count that Phase 5's importance formula
      also treats as a low-importance signal.

    Fail-open: a missing/unreadable vault returns zeros.
    """
    from .backlinks import load_index
    from .frontmatter import parse_frontmatter

    try:
        vault = config.vault_root
        if not vault.is_dir():
            return _empty_vault_health()

        journal_dir = config.vault_agent_journal_dir
        backlink_index = load_index(config)
        total = 0
        with_importance = 0
        graph_orphans = 0
        for filepath in vault.rglob("*.md"):
            try:
                if filepath.resolve().is_relative_to(journal_dir.resolve()):
                    continue
            except (ValueError, OSError):
                pass
            try:
                text = filepath.read_text(encoding="utf-8")
            except OSError:
                continue
            metadata, _ = parse_frontmatter(text)
            total += 1
            if metadata.get("importance") is not None:
                with_importance += 1
            rel = filepath.relative_to(vault).as_posix()
            if not backlink_index.get(rel):
                graph_orphans += 1

        coverage_pct = (with_importance / total * 100) if total else 0.0
        return {
            "total_pages": total,
            "with_importance": with_importance,
            "coverage_pct": coverage_pct,
            "missing_importance": total - with_importance,
            "graph_orphans": graph_orphans,
        }
    except Exception as exc:  # fail-open
        log.debug("vault health snapshot failed: %s", exc)
        return _empty_vault_health()


def format_report(stats: dict[str, dict], health: dict) -> str:
    lines = ["# Retrieval telemetry report", ""]
    total = sum(s["retrieval_count"] for s in stats.values())
    lines.append(f"Total candidate appearances: {total} across {len(stats)} pages")
    lines.append("")
    lines.append(
        f"{'file_path':<40} {'retrieved':>9} {'included':>9} {'inc%':>6} "
        f"{'drop-score':>10} {'drop-budget':>11}"
    )
    lines.append("-" * 90)
    for path, s in sorted(stats.items(), key=lambda kv: kv[1]["retrieval_count"], reverse=True):
        lines.append(
            f"{path:<40} {s['retrieval_count']:>9} {s['include_count']:>9} "
            f"{s['include_rate'] * 100:>5.0f}% {s['drop_score']:>10} {s['drop_budget']:>11}"
        )
    lines.append("")
    lines.append("## Vault health")
    lines.append(f"  Pages: {health['total_pages']}")
    lines.append(
        f"  With importance frontmatter: {health['with_importance']} "
        f"({health['coverage_pct']:.0f}%)"
    )
    lines.append(f"  Missing importance frontmatter: {health['missing_importance']}")
    lines.append(f"  Graph orphans (zero inbound links): {health['graph_orphans']}")
    return "\n".join(lines)


def build_report(config) -> str:
    records = load_records(config)
    stats = aggregate(records)
    health = vault_health(config)
    return format_report(stats, health)


def main() -> None:
    from .config import load_config
    config = load_config()
    print(build_report(config))


if __name__ == "__main__":
    main()
