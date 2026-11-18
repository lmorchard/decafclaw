#!/usr/bin/env python3
"""Chronological backfill runner for semantic memory (#1454).

Ingests past session JSONLs (workspace/sessions/**/*.jsonl) and daily notes
(journals/YYYY/YYYY-MM-DD.md) sorted strictly in ascending chronological order
to preserve conflict resolution and supersession semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Disable mem0 telemetry to prevent remote egress and migration store locking conflicts
os.environ.setdefault("MEM0_TELEMETRY", "false")

log = logging.getLogger("backfill_memory")


def load_state(state_file: Path) -> dict[str, Any]:
    """Load backfill progress checkpoint state."""
    if state_file.is_file():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Could not read backfill state file %s: %s", state_file, e)
    return {"completed": {}, "last_timestamp": None}


def save_state(state_file: Path, state: dict[str, Any]) -> None:
    """Atomically save backfill progress checkpoint state."""
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_file.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp_path.replace(state_file)
    except Exception as e:
        log.warning("Could not save backfill state file %s: %s", state_file, e)


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


def extract_page_records(pages_dir: Path) -> list[dict[str, Any]]:
    """Scan a directory of markdown pages and extract records using file modification time."""
    records: list[dict[str, Any]] = []
    if not pages_dir.is_dir():
        return records

    for md_file in pages_dir.rglob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8").strip()
            if not content:
                continue
            mtime = md_file.stat().st_mtime
            dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            records.append(
                {
                    "source": str(md_file),
                    "timestamp": dt,
                    "type": "page_entry",
                    "data": {"content": content},
                }
            )
        except Exception as e:
            log.warning("Failed to read page %s: %s", md_file, e)

    return records


def backfill(
    session_dirs: list[Path] | Path | None = None,
    journal_dirs: list[Path] | Path | None = None,
    pages_dirs: list[Path] | Path | None = None,
    dry_run: bool = False,
    config_path: Path | None = None,
    qdrant_path: Path | None = None,
    since: str | None = None,
    limit: int | None = None,
    state_file: Path | None = None,
    reset_state: bool = False,
) -> int:
    """Collect records, sort chronologically in ascending order, and ingest or verify."""
    records: list[dict[str, Any]] = []

    # Normalize single paths to lists
    s_dirs = [session_dirs] if isinstance(session_dirs, Path) else (session_dirs or [])
    j_dirs = [journal_dirs] if isinstance(journal_dirs, Path) else (journal_dirs or [])
    p_dirs = [pages_dirs] if isinstance(pages_dirs, Path) else (pages_dirs or [])

    for sd in s_dirs:
        if sd and sd.exists():
            records.extend(extract_session_records(sd))
    for jd in j_dirs:
        if jd and jd.exists():
            records.extend(extract_journal_records(jd))
    for pd in p_dirs:
        if pd and pd.exists():
            records.extend(extract_page_records(pd))

    if since:
        since_dt = parse_timestamp(since)
        records = [r for r in records if r["timestamp"] >= since_dt]

    if not records:
        print("No records found to backfill.")
        return 0

    # Sort strictly in ascending chronological order
    records.sort(key=lambda r: r["timestamp"])

    if limit and limit > 0:
        records = records[:limit]

    # Assert and verify chronological ordering
    prev_ts: datetime | None = None
    for r in records:
        if prev_ts is not None:
            assert r["timestamp"] >= prev_ts, f"Assertion failed: Record out of order ({r['timestamp']} < {prev_ts})"
        prev_ts = r["timestamp"]

    source_names = ", ".join(d.name for d in (s_dirs + j_dirs + p_dirs) if d)
    print(
        f"Chronological backfill verified {len(records)} records across "
        f"{source_names} in strictly ascending chronological order."
    )

    if dry_run:
        print("[DRY-RUN] Finished chronological verification without writing to memory backend.")
        return 0

    # Live backfill with persistent checkpointing
    state_path = state_file or Path("data/mem0_backfill_state.json")
    state = {"completed": {}, "last_timestamp": None} if reset_state else load_state(state_path)
    completed_map: dict[str, Any] = state.setdefault("completed", {})

    if completed_map and not reset_state:
        print(f"Resuming backfill: found {len(completed_map)} previously ingested records in {state_path}.", flush=True)

    try:
        from decafclaw.config import load_config
        from decafclaw.extensions.mem0 import Mem0Extension

        config = load_config()
        mem0_cfg = config.extensions.get("mem0")
        opts = dict(mem0_cfg.options if mem0_cfg else {})
        if qdrant_path:
            opts["qdrant_path"] = str(qdrant_path)
            print(f"Staging mode: writing Qdrant store to {qdrant_path}")
        ext = Mem0Extension(opts, config)
        client = ext._init_client()
        if not client:
            print("Mem0 client could not be initialized; aborting live backfill.")
            return 1

        total = len(records)
        print(f"Starting live backfill of {total} records into Mem0...", flush=True)
        processed_count = 0
        skipped_count = 0
        error_count = 0

        for i, r in enumerate(records, start=1):
            data = r["data"]
            dt_str = r["timestamp"].strftime("%Y-%m-%d %H:%M")
            src_name = Path(r["source"]).name

            role = "user"
            content = ""
            if r["type"] == "session_message":
                role = data.get("role")
                content = data.get("content")
                if role not in ("user", "assistant") or not content or not isinstance(content, str):
                    continue
            elif r["type"] in ("journal_entry", "page_entry"):
                content = data.get("content", "")
                if not content or not isinstance(content, str):
                    continue

            # Deterministic idempotency key: sha256(source + timestamp + role + content)
            rec_key = hashlib.sha256(f"{r['source']}:{dt_str}:{role}:{content}".encode()).hexdigest()

            if rec_key in completed_map:
                skipped_count += 1
                continue

            res = None
            print(f"[{i}/{total}] Processing {dt_str} ({src_name})...", flush=True)

            try:
                res = client.add([{"role": role, "content": content}], user_id="default")
                processed_count += 1
                facts_count = len(res.get("results", [])) if isinstance(res, dict) else 0
                completed_map[rec_key] = {
                    "timestamp": dt_str,
                    "source": r["source"],
                    "facts": facts_count,
                }
                state["last_timestamp"] = dt_str
                save_state(state_path, state)
            except KeyboardInterrupt:
                print(
                    f"\n[INTERRUPTED] Gracefully stopped at record {i}/{total} ({dt_str}). State safely saved.",
                    flush=True,
                )
                print("Run the same command again to resume without repeating processed records.", flush=True)
                save_state(state_path, state)
                return 0
            except Exception as e:
                error_count += 1
                print(f"[{i}/{total}] Error at {dt_str} ({src_name}): {e}", flush=True)
                continue

            # Report extracted/updated facts
            if res and isinstance(res, dict):
                for item in res.get("results", []):
                    event = item.get("event", "ADD")
                    mem = item.get("memory", "")
                    if mem:
                        print(f"  [{dt_str}] -> [{event}] {mem}", flush=True)

            # Heartbeat progress reporting every 25 records or at completion
            if i % 25 == 0 or i == total:
                pct = (i / total) * 100
                print(f"Progress: [{i}/{total} ({pct:.1f}%)] | Current date: {dt_str} | Source: {src_name}", flush=True)

        print(
            f"Live backfill finished: processed {processed_count} new records, "
            f"skipped {skipped_count} existing records, with {error_count} errors.",
            flush=True,
        )
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Backfill stopped cleanly.", flush=True)
        return 0
    except Exception as e:
        print(f"Error during live backfill: {e}")
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Chronologically backfill session and journal memory.")
    parser.add_argument(
        "--dir",
        "--session-dirs",
        dest="session_dirs",
        type=Path,
        nargs="+",
        help="One or more directories containing session archives (.jsonl)",
    )
    parser.add_argument(
        "--journals-dir",
        "--journal-dirs",
        dest="journal_dirs",
        type=Path,
        nargs="+",
        help="One or more directories containing chronological journal/stream notes (.md)",
    )
    parser.add_argument(
        "--pages-dir",
        "--pages-dirs",
        dest="pages_dirs",
        type=Path,
        nargs="+",
        help="Optional directories containing evergreen/wiki pages (.md)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Parse and verify chronological ordering without ingesting"
    )
    parser.add_argument("--config", dest="config_path", type=Path, help="Optional path to config.json")
    parser.add_argument(
        "--qdrant-path",
        dest="qdrant_path",
        type=Path,
        help="Staging path for Qdrant store (e.g. data/mem0_qdrant_staging)",
    )
    parser.add_argument("--since", dest="since", type=str, help="Only backfill records with timestamp >= YYYY-MM-DD")
    parser.add_argument("--limit", dest="limit", type=int, help="Limit to first N chronological records")
    parser.add_argument(
        "--state-file",
        dest="state_file",
        type=Path,
        help="Optional checkpoint statefile path (default: data/mem0_backfill_state.json)",
    )
    parser.add_argument(
        "--reset-state",
        dest="reset_state",
        action="store_true",
        help="Ignore and reset existing progress statefile",
    )

    args = parser.parse_args()
    return backfill(
        session_dirs=args.session_dirs,
        journal_dirs=args.journal_dirs,
        pages_dirs=args.pages_dirs,
        dry_run=args.dry_run,
        config_path=args.config_path,
        qdrant_path=args.qdrant_path,
        since=args.since,
        limit=args.limit,
        state_file=args.state_file,
        reset_state=args.reset_state,
    )


if __name__ == "__main__":
    sys.exit(main())
