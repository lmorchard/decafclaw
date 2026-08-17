"""Telemetry log rotation."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parse_iso(s: str) -> datetime:
    """Parse ISO8601 string to aware datetime."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _partition_by_age(records: list[dict], retention_days: int) -> tuple[list[dict], list[dict]]:
    """Split records into (old, recent) by timestamp against retention."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=retention_days)
    old: list[dict] = []
    recent: list[dict] = []
    for r in records:
        ts = r.get("timestamp", "")
        try:
            dt = _parse_iso(ts)
        except (ValueError, TypeError):
            # Malformed timestamp — keep it, don't silently lose data
            recent.append(r)
            continue
        if dt < cutoff:
            old.append(r)
        else:
            recent.append(r)
    return old, recent


def rotate_if_needed(path: Path, retention_days: int) -> None:
    """Opportunistic rotation. Old records go to path.parent/archive/{stem}_{YYYY-MM}.jsonl."""
    if not path.exists():
        return

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=retention_days)
    needs_rotation = False

    # Check cheaply before materializing everything
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                dt = _parse_iso(rec.get("timestamp", ""))
                if dt < cutoff:
                    needs_rotation = True
                    break
            except (json.JSONDecodeError, ValueError, TypeError):
                continue

    if not needs_rotation:
        return

    # Materialize and partition
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

    old, recent = _partition_by_age(records, retention_days)
    if not old:
        return

    # Group old records by year-month
    by_month: dict[str, list[dict]] = {}
    for r in old:
        try:
            dt = _parse_iso(r.get("timestamp", ""))
            key = dt.strftime("%Y-%m")
            by_month.setdefault(key, []).append(r)
        except (ValueError, TypeError):
            continue

    archive = path.parent / "archive"
    archive.mkdir(parents=True, exist_ok=True)

    # Write to archive files
    for month_key, recs in by_month.items():
        archive_path = archive / f"{path.stem}_{month_key}{path.suffix}"
        with archive_path.open("a", encoding="utf-8") as f:
            for rec in recs:
                f.write(json.dumps(rec) + "\n")

    # Atomic rewrite of the primary file
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, dir=str(path.parent)) as tf:
        tmp_path = Path(tf.name)
        try:
            for r in recent:
                tf.write(json.dumps(r) + "\n")
            tf.flush()
            os.replace(str(tmp_path), str(path))
        except Exception:
            tf.close()
            if tmp_path.exists():
                tmp_path.unlink()
            raise
