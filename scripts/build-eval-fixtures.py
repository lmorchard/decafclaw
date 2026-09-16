#!/usr/bin/env python3
"""Build embedding fixtures for eval tests.

Reads cat-facts.txt and generates a pre-indexed SQLite DB so eval
runs don't need to re-embed 97 facts every time.

Usage: uv run python scripts/build-eval-fixtures.py
"""

import asyncio
import dataclasses
import shutil
import tempfile
from pathlib import Path

from decafclaw.config import Config, load_config
from decafclaw.embeddings import index_entry


def fixture_config(config: Config, tmp: str) -> Config:
    """Return a copy of ``config`` pointed at a throwaway data directory.

    ``data_home`` and ``agent_id`` are NOT fields on ``Config`` -- they live on
    ``config.agent``. ``Config`` is not frozen, so assigning them directly (as
    this script used to) silently creates undeclared attributes that nothing
    reads: the redirect no-ops and ``index_entry`` writes to the real
    ``data/{agent_id}/workspace/embeddings.db``, polluting the live embedding
    index. See #856.
    """
    return dataclasses.replace(
        config,
        agent=dataclasses.replace(config.agent, data_home=tmp, id="fixture"),
    )


async def main():
    config = load_config()

    facts_file = Path("evals/fixtures/cat-facts.txt")
    output_db = Path("evals/fixtures/cat-facts-embeddings.db")

    if not facts_file.exists():
        print(f"Error: {facts_file} not found")
        return

    facts = [f.strip() for f in facts_file.read_text().strip().split("\n") if f.strip()]
    print(f"Indexing {len(facts)} cat facts...")

    # Build in a temp directory, then move the DB out
    with tempfile.TemporaryDirectory() as tmp:
        build_config = fixture_config(config, tmp)

        for i, fact in enumerate(facts):
            # Format like a real memory entry so embeddings match the same space
            entry = (
                f"## 2026-01-01 00:00\n\n"
                f"- **channel:** fixture (fixture)\n"
                f"- **tags:** cat, animal, fact\n\n"
                f"{fact}"
            )
            await index_entry(build_config, "cat-facts.txt", entry)
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(facts)}")

        # Move DB to final location. Derived from the config, not
        # hand-assembled: the old literal was Path(tmp)/"workspace"/"fixture",
        # but workspace_path is data_home/agent_id/workspace -- wrong order, so
        # the move would have failed even once the redirect worked.
        built_db = build_config.workspace_path / "embeddings.db"
        shutil.move(str(built_db), str(output_db))

    print(f"Done: {output_db}")


if __name__ == "__main__":
    asyncio.run(main())
