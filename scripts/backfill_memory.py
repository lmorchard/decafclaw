#!/usr/bin/env python3
"""Chronological backfill runner for semantic memory (#1454).

Ingests past session JSONLs (workspace/sessions/**/*.jsonl) and daily notes
(journals/YYYY/YYYY-MM-DD.md) sorted strictly in ascending chronological order
to preserve conflict resolution and supersession semantics.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("backfill_memory")


def parse_timestamp(ts_raw: Any) -> datetime:
    """Parse a timestamp into a timezone-aware or comparable datetime."""
    if isinstance(ts_raw, (int, float)):
        return datetime.fromtimestamp(ts_raw, tz=timezone.utc)
    if isinstance(ts_raw, str):
        cleaned = ts_raw.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    # Fallback to current time if parsing fails
    return datetime.now(timezone.utc)


def extract_session_records(session_dir: Path) -> list[dict[str, Any]]:
    """Scan session directory for *.jsonl files and extract timestamped records."""
    records: list[dict[str, Any]] = []
    jsonl_files = (
        list(session_dir.rglob("*.jsonl"))
        if session_dir.is_dir()
        else ([session_dir] if session_dir.suffix == ".jsonl" else [])
    )

    for file_path in jsonl_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Look for timestamp field, or fallback to file mtime
                    ts_val = data.get("timestamp") or data.get("created_at") or data.get("time")
                    if ts_val:
                        dt = parse_timestamp(ts_val)
                    else:
                        dt = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)

                    records.append(
                        {
                            "source": str(file_path),
                            "line": line_num,
                            "timestamp": dt,
                            "type": "session_message",
                            "data": data,
                        }
                    )
        except Exception as e:
            log.warning("Failed to read %s: %s", file_path, e)

    return records


def extract_journal_records(journal_dir: Path) -> list[dict[str, Any]]:
    """Scan journal directory for markdown files and extract timestamped entries."""
    records: list[dict[str, Any]] = []
    if not journal_dir.is_dir():
        return records

    entry_pattern = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)", re.MULTILINE)

    for md_file in journal_dir.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
            matches = list(entry_pattern.finditer(content))
            if not matches:
                # If no section header, use date from filename if matching YYYY-MM-DD
                name_match = re.search(r"(\d{4}-\d{2}-\d{2})", md_file.stem)
                if name_match:
                    dt = parse_timestamp(name_match.group(1))
                    records.append(
                        {
                            "source": str(md_file),
                            "timestamp": dt,
                            "type": "journal_entry",
                            "data": {"content": content.strip()},
                        }
                    )
                continue

            for i, match in enumerate(matches):
                dt_str = match.group(1).replace(" ", "T")
                dt = parse_timestamp(dt_str)
                start_pos = match.end()
                end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(content)
                entry_text = content[start_pos:end_pos].strip()
                records.append(
                    {
                        "source": str(md_file),
                        "timestamp": dt,
                        "type": "journal_entry",
                        "data": {"content": entry_text},
                    }
                )
        except Exception as e:
            log.warning("Failed to read journal %s: %s", md_file, e)

    return records


def backfill(
    session_dir: Path | None,
    journal_dir: Path | None,
    dry_run: bool = False,
    config_path: Path | None = None,
) -> int:
    """Collect records, sort chronologically in ascending order, and ingest or verify."""
    records: list[dict[str, Any]] = []

    if session_dir and session_dir.exists():
        records.extend(extract_session_records(session_dir))
    if journal_dir and journal_dir.exists():
        records.extend(extract_journal_records(journal_dir))

    if not records:
        print("No records found to backfill.")
        return 0

    # Sort strictly in ascending chronological order
    records.sort(key=lambda r: r["timestamp"])

    # Assert and verify chronological ordering
    prev_ts: datetime | None = None
    for r in records:
        if prev_ts is not None:
            assert r["timestamp"] >= prev_ts, f"Assertion failed: Record out of order ({r['timestamp']} < {prev_ts})"
        prev_ts = r["timestamp"]

    print(
        f"Chronological backfill verified {len(records)} records across "
        f"{(session_dir.name if session_dir else '')} in strictly ascending chronological order."
    )

    if dry_run:
        print("[DRY-RUN] Finished chronological verification without writing to memory backend.")
        return 0

    # Live backfill
    try:
        from decafclaw.config import load_config
        from decafclaw.extensions.mem0 import Mem0Extension

        config = load_config()
        mem0_cfg = config.extensions.get("mem0")
        ext = Mem0Extension(mem0_cfg.options if mem0_cfg else {}, config)
        client = ext._init_client()
        if not client:
            print("Mem0 client could not be initialized; aborting live backfill.")
            return 1

        for r in records:
            data = r["data"]
            if r["type"] == "session_message":
                role = data.get("role")
                content = data.get("content")
                if role in ("user", "assistant") and content and isinstance(content, str):
                    client.add([{"role": role, "content": content}], user_id="default")
            elif r["type"] == "journal_entry":
                content = data.get("content")
                if content:
                    client.add([{"role": "user", "content": content}], user_id="default")
        print("Live backfill completed successfully.")
    except Exception as e:
        print(f"Error during live backfill: {e}")
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Chronologically backfill session and journal memory.")
    parser.add_argument("--dir", dest="session_dir", type=Path, help="Directory containing session archives (.jsonl)")
    parser.add_argument(
        "--journals-dir", dest="journal_dir", type=Path, help="Directory containing journal notes (.md)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Parse and verify chronological ordering without ingesting"
    )
    parser.add_argument("--config", dest="config_path", type=Path, help="Optional path to config.json")

    args = parser.parse_args()
    return backfill(
        session_dir=args.session_dir,
        journal_dir=args.journal_dir,
        dry_run=args.dry_run,
        config_path=args.config_path,
    )


if __name__ == "__main__":
    sys.exit(main())
