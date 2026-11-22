"""Approval counters over the audit log (#1641 Phase 0).

The approval pipeline publishes ``approval_decision`` and ``approval_human``
events; the audit-log subscriber writes them to ``audit.jsonl``. This module
turns that JSONL into the small surface the design needs to measure a change
before trusting it: prompts per period, judge decline rate, human approval
rate, and a per-tier breakdown.

Usage::

    decafclaw approvals report [--days 7] [--json] [--path FILE]
"""

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


def audit_file_paths(path: Path, backups: int = 0) -> list[Path]:
    """The audit log plus its rotation backups, existing only."""
    candidates = [path]
    candidates.extend(path.with_name(f"{path.name}.{index}") for index in range(1, max(0, backups) + 1))
    return [candidate for candidate in candidates if candidate.exists()]


def load_records(paths: list[Path]) -> list[dict]:
    """Parse approval records, skipping malformed lines and other events."""
    records: list[dict] = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("event") in ("approval_decision", "approval_human"):
                records.append(record)
    return records


def _timestamp(record: dict) -> datetime | None:
    raw = record.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def summarize(records: list[dict], *, days: int = 7, now: datetime | None = None) -> dict:
    """Count prompts, verdicts, and tiers inside the trailing window."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(0, days))
    decisions: list[dict] = []
    human: list[dict] = []
    for record in records:
        stamp = _timestamp(record)
        if stamp is None or stamp < cutoff:
            continue
        if record.get("event") == "approval_decision":
            decisions.append(record)
        else:
            human.append(record)

    approved = sum(1 for record in human if record.get("approved") is True)
    denied = len(human) - approved
    judge_allowed = sum(1 for record in decisions if record.get("tier") == "aux_allow")
    judge_declined = sum(1 for record in decisions if record.get("tier") == "aux_decline")
    judge_total = judge_allowed + judge_declined

    by_tier: Counter = Counter(str(record.get("tier") or "") for record in decisions)
    human_by_tier: Counter = Counter(str(record.get("tier") or "") for record in human)
    by_decision: Counter = Counter(str(record.get("decision") or "") for record in decisions)

    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    return {
        "window_days": days,
        "human_prompts": len(human),
        "human_approved": approved,
        "human_denied": denied,
        "human_approval_rate": rate(approved, len(human)),
        "judge_allowed": judge_allowed,
        "judge_declined": judge_declined,
        "judge_decline_rate": rate(judge_declined, judge_total),
        "read_only_auto_approved": by_tier.get("read_only", 0),
        "activity_new_prompts": by_tier.get("activity_new", 0),
        "decisions": len(decisions),
        "by_tier": dict(sorted(by_tier.items())),
        "human_by_tier": dict(sorted(human_by_tier.items())),
        "by_decision": dict(sorted(by_decision.items())),
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def format_text(summary: dict, paths: list[Path]) -> str:
    lines = [
        f"Approval report — last {summary['window_days']} days",
        "audit: " + ", ".join(str(path) for path in paths),
        "",
        f"human prompts:  {summary['human_prompts']}",
        f"  approved:     {summary['human_approved']} ({_percent(summary['human_approval_rate'])})",
        f"  denied:       {summary['human_denied']}",
        f"judge allowed:  {summary['judge_allowed']}",
        f"judge declined: {summary['judge_declined']} ({_percent(summary['judge_decline_rate'])})",
        f"read-only auto: {summary['read_only_auto_approved']}",
        f"new activity:   {summary['activity_new_prompts']}",
        "",
        "by tier:",
    ]
    for tier, count in summary["by_tier"].items():
        lines.append(f"  {tier or '(none)':<28} {count}")
    if not summary["by_tier"]:
        lines.append("  (no decisions)")
    lines.append("")
    lines.append("by decision:")
    for decision, count in summary["by_decision"].items():
        lines.append(f"  {decision or '(none)':<28} {count}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="decafclaw approvals")
    subparsers = parser.add_subparsers(dest="command", required=True)
    report = subparsers.add_parser("report", help="show approval counters from the audit log")
    report.add_argument("--days", type=int, default=7)
    report.add_argument("--json", action="store_true")
    report.add_argument("--path", default="", help="audit log path (default: the resolved config)")

    replay = subparsers.add_parser(
        "replay", help="replay archived shell commands through the current decision pipeline (dry run)"
    )
    replay.add_argument("--root", default="", help="agent data dir or workspace dir (default: the resolved config)")
    replay.add_argument("--limit", type=int, default=200, help="conversations to sample (default 200)")
    replay.add_argument("--all", action="store_true", help="replay every archive instead of sampling")
    replay.add_argument("--seed", type=int, default=0, help="sampling seed (default 0)")
    replay.add_argument("--json", action="store_true")
    replay.add_argument(
        "--workspace",
        default="",
        help="workspace path the archived commands ran in (default: the replay root)",
    )
    replay.add_argument("--activity-window", type=int, default=None)
    replay.add_argument("--no-read-only", action="store_true", help="disable the read-only tier for the replay")
    replay.add_argument("--aux-enabled", choices=["auto", "on", "off"], default="auto")
    replay.add_argument(
        "--check-denials", action="store_true", help="exit 1 when an archived human denial would be silently allowed"
    )
    replay.add_argument("--show-denials", action="store_true", help="list every archived human denial")
    replay.add_argument("--denials-out", default="", help="write the denial report as JSON to this path")
    args = parser.parse_args(argv)

    if args.command == "replay":
        from .approval_replay import run_cli

        return run_cli(args)

    if args.path:
        paths = [Path(args.path)]
    else:
        from .config import load_config

        config = load_config()
        paths = audit_file_paths(config.workspace_path / config.audit_log.path, config.audit_log.max_backups)

    summary = summarize(load_records(paths), days=args.days)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(format_text(summary, paths))
    return 0
