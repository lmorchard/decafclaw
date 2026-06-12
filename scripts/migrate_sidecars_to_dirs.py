#!/usr/bin/env python3
"""Migrate flat per-conversation sidecars into the directory layout.

Moves workspace/conversations/{id}.SUFFIX → workspace/conversations/{id}/{filename}
for every sidecar type (archive, compacted, notes, decisions, context,
canvas, skills, skill_data, vault_grants). Idempotent — safe to re-run;
a flat file whose target already exists is skipped, not overwritten.

Usage:
  python scripts/migrate_sidecars_to_dirs.py [--dry-run]
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path

# Add project root to path (mirror migrate_to_vault.py)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decafclaw.config import load_config  # noqa: E402
from decafclaw.conversation_paths import SIDECAR_FILENAMES, _safe_conv_id  # noqa: E402


def migrate_sidecars(conversations_dir: Path, *, dry_run: bool) -> int:
    """Move flat {id}.SUFFIX files into {id}/{filename}. Idempotent:
    skips a file whose target already exists. Returns count moved
    (or would-move count under dry_run)."""
    if not conversations_dir.is_dir():
        return 0
    moved = 0
    for entry in sorted(conversations_dir.iterdir()):
        if not entry.is_file():
            continue
        match = next(((suf, fn) for suf, fn in SIDECAR_FILENAMES
                      if entry.name.endswith(suf)), None)
        if match is None:
            continue
        suffix, filename = match
        conv_id = entry.name[: -len(suffix)]
        target = conversations_dir / _safe_conv_id(conv_id) / filename
        if target.exists():
            print(f"  SKIP (exists): {target}")
            continue
        if dry_run:
            print(f"  WOULD MOVE: {entry} -> {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(target))
            print(f"  MOVED: {entry} -> {target}")
        moved += 1
    return moved


def main():
    parser = argparse.ArgumentParser(
        description="Migrate flat conversation sidecars into per-conversation dirs")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without making changes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")

    config = load_config()
    conversations_dir = config.workspace_path / "conversations"
    print(f"Conversations dir: {conversations_dir}")
    print(f"Dry run: {args.dry_run}")
    if not args.dry_run:
        print("WARNING: this moves files in place. Back up "
              "workspace/conversations/ (or run --dry-run) first.")
    print()
    count = migrate_sidecars(conversations_dir, dry_run=args.dry_run)
    if args.dry_run:
        print(f"\n{count} file(s) would be moved. Re-run without --dry-run to apply.")
    else:
        print(f"\n{count} file(s) moved.")


if __name__ == "__main__":
    main()
