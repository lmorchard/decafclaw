#!/usr/bin/env python3
"""Migrate per-conversation dirs into the session-scoped layout (#1112).

Moves conversations/{conv_id}/ (post-#576 per-conversation dirs; flat
{conv_id}.SUFFIX files are handled by migrate_sidecars_to_dirs) into the
session-scoped layout:

- standalone conversation C:  conversations/{C}/  ->  sessions/{C}/
- delegated child C2 = "{parent}--child-{hex}":
      conversations/{C2}/  ->  sessions/{parent}/C2/

Idempotent — a target that already exists is skipped, never overwritten.
Non-destructive to conversation content: only directory moves.

Usage:
  python scripts/migrate_sessions.py [--dry-run]
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path

# Add project root to path (mirror migrate_to_vault.py)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decafclaw.config import load_config  # noqa: E402
from decafclaw.conversation_paths import _is_child_conv_id, _safe_conv_id, conversations_root  # noqa: E402


def migrate_sessions(conversations_dir: Path, *, dry_run: bool) -> int:
    """Move conversation dirs into the session layout. Idempotent:
    skips a dir whose target already exists. Returns count moved
    (or would-move count under dry_run)."""
    moved = 0
    if not conversations_dir.is_dir():
        return 0
    sessions_root = conversations_dir.parent / "sessions"
    for entry in sorted(conversations_dir.iterdir()):
        if not entry.is_dir():
            continue
        conv_id = entry.name
        if _is_child_conv_id(conv_id):
            sid = conv_id.split("--child-", 1)[0]
            target = sessions_root / _safe_conv_id(sid) / _safe_conv_id(conv_id)
        else:
            target = sessions_root / _safe_conv_id(conv_id)
        if target.exists():
            continue
        if dry_run:
            print(f"  WOULD MOVE: {entry} -> {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(target))
            print(f"  MOVED: {entry} -> {target}")
        moved += 1
    return moved


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate conversations into the session-scoped layout")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    config = load_config()
    conversations_dir = conversations_root(config)
    print(f"Conversations dir: {conversations_dir}")
    print(f"Sessions root: {conversations_dir.parent / 'sessions'}")
    print(f"Dry run: {args.dry_run}")
    if not args.dry_run:
        print("WARNING: this moves conversation directories. Back up conversations/ (or run --dry-run) first.")
    print()
    count = migrate_sessions(conversations_dir, dry_run=args.dry_run)
    if args.dry_run:
        print(f"\n{count} conversation dir(s) would be moved. Re-run without --dry-run to apply.")
    else:
        print(f"\n{count} conversation dir(s) moved.")


if __name__ == "__main__":
    main()
