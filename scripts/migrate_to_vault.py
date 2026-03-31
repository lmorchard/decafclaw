#!/usr/bin/env python3
"""Migrate wiki pages and memories into the unified vault structure.

Moves:
  workspace/wiki/**       → {vault}/agent/pages/
  workspace/memories/**   → {vault}/agent/journal/

Run after deploying the vault code. Idempotent — safe to re-run.

Usage:
  python scripts/migrate_to_vault.py [--dry-run]
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decafclaw.config import load_config  # noqa: E402

log = logging.getLogger(__name__)


def migrate_directory(src: Path, dst: Path, label: str, dry_run: bool) -> int:
    """Move all files from src to dst, preserving directory structure.

    Returns number of files moved.
    """
    if not src.exists():
        print(f"  {label}: source {src} does not exist, skipping")
        return 0

    count = 0
    for filepath in sorted(src.rglob("*")):
        if not filepath.is_file():
            continue
        rel = filepath.relative_to(src)
        target = dst / rel

        if target.exists():
            print(f"  SKIP (exists): {target}")
            continue

        if dry_run:
            print(f"  WOULD MOVE: {filepath} → {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(filepath), str(target))
            print(f"  MOVED: {filepath} → {target}")
        count += 1

    # Clean up empty directories in source
    if not dry_run and src.exists():
        for dirpath in sorted(src.rglob("*"), reverse=True):
            if dirpath.is_dir() and not any(dirpath.iterdir()):
                dirpath.rmdir()
        if src.is_dir() and not any(src.iterdir()):
            src.rmdir()
            print(f"  Removed empty directory: {src}")

    return count


def main():
    parser = argparse.ArgumentParser(description="Migrate wiki/memories to vault")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without making changes")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")

    config = load_config()
    workspace = config.workspace_path
    vault_root = config.vault_root
    agent_pages = config.vault_agent_pages_dir
    agent_journal = config.vault_agent_journal_dir

    print(f"Workspace: {workspace}")
    print(f"Vault root: {vault_root}")
    print(f"Agent pages: {agent_pages}")
    print(f"Agent journal: {agent_journal}")
    print(f"Dry run: {args.dry_run}")
    print()

    # Ensure vault directories exist
    if not args.dry_run:
        vault_root.mkdir(parents=True, exist_ok=True)
        agent_pages.mkdir(parents=True, exist_ok=True)
        agent_journal.mkdir(parents=True, exist_ok=True)

    # Migrate wiki pages → agent/pages/
    wiki_src = workspace / "wiki"
    print(f"--- Migrating wiki pages: {wiki_src} → {agent_pages} ---")
    wiki_count = migrate_directory(wiki_src, agent_pages, "wiki", args.dry_run)

    # Migrate memories → agent/journal/
    mem_src = workspace / "memories"
    print(f"\n--- Migrating memories: {mem_src} → {agent_journal} ---")
    mem_count = migrate_directory(mem_src, agent_journal, "memories", args.dry_run)

    print(f"\n--- Summary ---")
    print(f"Wiki pages: {wiki_count} file(s) {'would be ' if args.dry_run else ''}moved")
    print(f"Memories: {mem_count} file(s) {'would be ' if args.dry_run else ''}moved")

    if wiki_count + mem_count > 0 and not args.dry_run:
        print("\nMigration complete. Run 'make reindex' to rebuild the embeddings index.")
    elif args.dry_run and wiki_count + mem_count > 0:
        print("\nRe-run without --dry-run to apply changes.")
    else:
        print("\nNothing to migrate.")


if __name__ == "__main__":
    main()
