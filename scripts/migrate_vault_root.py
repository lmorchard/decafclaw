#!/usr/bin/env python3
"""Move the decafclaw agent content from one vault root to another.

Typical use: unifying the agent's vault with the user's Obsidian vault.

Dry-run by default. Pass --apply to execute.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from", dest="src", required=True, type=Path,
        help="Current vault root (contains agent/)",
    )
    parser.add_argument(
        "--to", dest="dst", required=True, type=Path,
        help="New vault root (must already exist, must not contain agent/)",
    )
    parser.add_argument(
        "--config", type=Path,
        default=Path("data/decafclaw/config.json"),
        help="Path to config.json to update (default: %(default)s)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually perform the migration (default: dry-run)",
    )
    args = parser.parse_args(argv)

    src = args.src.resolve()
    dst = args.dst.resolve()
    config_path = args.config

    src_agent = src / "agent"
    dst_agent = dst / "agent"

    # Guard: source agent/ must exist
    if not src_agent.exists():
        print(
            f"ERROR: source agent folder not found: {src_agent}",
            file=sys.stderr,
        )
        return 1

    # Guard: target vault root must exist
    if not dst.exists():
        print(
            f"ERROR: target vault root does not exist: {dst}",
            file=sys.stderr,
        )
        return 1

    # Guard: target agent/ must NOT already exist
    if dst_agent.exists():
        print(
            f"ERROR: target agent folder already exists: {dst_agent}",
            file=sys.stderr,
        )
        return 1

    # Guard: config file must exist
    if not config_path.exists():
        print(
            f"ERROR: config not found: {config_path}",
            file=sys.stderr,
        )
        return 1

    if not args.apply:
        print("DRY RUN (pass --apply to execute):")
        print(f"  move  {src_agent} -> {dst_agent}")
        print(f"  patch {config_path}: vault_path = {dst}")
        return 0

    print(f"Moving {src_agent} -> {dst_agent}")
    shutil.move(str(src_agent), str(dst_agent))

    print(f"Updating {config_path}: vault.vault_path = {dst}")
    config = json.loads(config_path.read_text())
    config.setdefault("vault", {})
    config["vault"]["vault_path"] = str(dst)
    config_path.write_text(json.dumps(config, indent=2) + "\n")

    print()
    print("Migration complete. Run `make reindex` to rebuild the embedding index.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
