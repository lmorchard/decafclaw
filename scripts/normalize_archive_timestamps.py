#!/usr/bin/env python3
"""Rewrite legacy naive-local archive timestamps to canonical UTC (#1829).

Archives written before #1829 stamped rows with ``datetime.now()`` — naive
*local* time — while background events, confirmation requests, and notes
stamped with UTC. The two orders disagree by the host's UTC offset, so
lexicographic comparisons (§ the bug) dropped the newest turns from the
transcript and mis-counted compaction boundaries.

The reader side now parses instants instead of comparing strings, so this
script is not required for correctness — it exists to remove the remaining
ambiguity on disk so tooling that greps/sorts raw timestamps behaves.

For each naive timestamp we subtract the *actual* host UTC offset in effect
at that instant (DST-aware via ``astimezone``), recovering the true UTC
moment rather than naively assuming a fixed offset.

Idempotent: rows already carrying an offset (``+00:00``, ``Z``, or any other
offset) are left untouched, so a second run is a no-op.

Usage:
  python scripts/normalize_archive_timestamps.py [--dry-run] [--limit N]
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decafclaw.config import load_config  # noqa: E402


def normalize_one(value):
    """Return (converted_or_original, changed)."""
    if not isinstance(value, str) or not value:
        return value, False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value, False
    if parsed.tzinfo is not None:
        return value, False
    # Naive => it was local wall time. Attach the local zone, then convert.
    local = parsed.astimezone()  # naive astimezone() assumes local time
    return local.astimezone(timezone.utc).isoformat(), True


def rewrite_file(path: Path, *, dry_run: bool) -> tuple[int, int]:
    """Rewrite one JSONL file. Returns (rows_scanned, rows_changed)."""
    scanned = 0
    changed = 0
    out_lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                out_lines.append(line)
                continue
            scanned += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # Corrupt rows are a separate problem (#torn-write); keep as-is.
                out_lines.append(line)
                continue
            if isinstance(row, dict) and "timestamp" in row:
                new_ts, did = normalize_one(row["timestamp"])
                if did:
                    row["timestamp"] = new_ts
                    changed += 1
                    out_lines.append(json.dumps(row) + "\n")
                    continue
            out_lines.append(line)
    if changed and not dry_run:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(out_lines)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return scanned, changed


def main():
    parser = argparse.ArgumentParser(description="Normalize legacy archive timestamps to UTC")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N conversations")
    args = parser.parse_args()

    config = load_config()
    sessions_dir = config.workspace_path / "sessions"
    print(f"Sessions dir: {sessions_dir}")
    print(f"Dry run: {args.dry_run}")
    if not args.dry_run:
        print("WARNING: rewrites archive files in place. Back up sessions/ first.")
    print()

    if not sessions_dir.is_dir():
        print("No sessions directory; nothing to do.")
        return

    convs = sorted(p for p in sessions_dir.iterdir() if p.is_dir())
    if args.limit:
        convs = convs[: args.limit]

    total_files = 0
    total_changed = 0
    for conv in convs:
        for name in ("archive.jsonl", "compacted.jsonl"):
            path = conv / name
            if not path.is_file():
                continue
            scanned, changed = rewrite_file(path, dry_run=args.dry_run)
            if changed:
                total_files += 1
                total_changed += changed
                verb = "WOULD REWRITE" if args.dry_run else "REWROTE"
                print(f"  {verb} {conv.name}/{name}: {changed}/{scanned} rows")

    print()
    if args.dry_run:
        print(f"{total_changed} row(s) in {total_files} file(s) would be normalized.")
    else:
        print(f"{total_changed} row(s) in {total_files} file(s) normalized.")


if __name__ == "__main__":
    main()
