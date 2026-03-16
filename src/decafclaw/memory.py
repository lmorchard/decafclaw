"""Memory operations — read and write markdown memory files."""

import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def memory_dir(config) -> Path:
    """Compute the memory directory path for the agent."""
    return config.workspace_path / "memories"


def save_entry(config, channel_name: str, channel_id: str,
               thread_id: str, tags: list[str], content: str) -> str:
    """Append a memory entry to today's file."""
    now = datetime.now()
    base = memory_dir(config) / str(now.year)
    base.mkdir(parents=True, exist_ok=True)

    filepath = base / f"{now:%Y-%m-%d}.md"
    tag_str = ", ".join(tags) if tags else "untagged"

    entry = f"\n## {now:%Y-%m-%d %H:%M}\n\n"
    if channel_name or channel_id:
        entry += f"- **channel:** {channel_name} ({channel_id})\n"
    if thread_id:
        entry += f"- **thread:** {thread_id}\n"
    entry += f"- **tags:** {tag_str}\n"
    entry += f"\n{content}\n"

    with open(filepath, "a") as f:
        f.write(entry)

    log.info(f"Saved memory tagged [{tag_str}]")
    return f"Saved memory tagged [{tag_str}]"


def _parse_entries(text: str) -> list[str]:
    """Split markdown text into individual memory entries on ## headers."""
    parts = text.split("\n## ")
    entries = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Re-add the header prefix if it was split off
        if not part.startswith("## "):
            part = "## " + part
        entries.append(part)
    return entries


def search_entries(config, query: str, context_lines: int = 3) -> str:
    """Search all memory files using case-insensitive substring matching.

    Returns whole entries when a match is found within any line of the entry.
    """
    base = memory_dir(config)
    if not base.exists():
        return f"No memories found matching '{query}'"

    query_lower = query.lower()
    results = []

    for filepath in sorted(base.rglob("*.md")):
        rel_path = filepath.relative_to(base)
        for entry in _parse_entries(filepath.read_text()):
            if query_lower in entry.lower():
                results.append(f"### {rel_path}\n\n{entry}")

    if not results:
        return f"No memories found matching '{query}'"

    return "\n\n".join(results)


def recent_entries(config, n: int = 5) -> str:
    """Return the last N memory entries."""
    base = memory_dir(config)
    if not base.exists():
        return "No memories found"

    entries = []
    for filepath in sorted(base.rglob("*.md"), reverse=True):
        for entry in reversed(_parse_entries(filepath.read_text())):
            entries.append(entry)
            if len(entries) >= n:
                break
        if len(entries) >= n:
            break

    if not entries:
        return "No memories found"

    return "\n\n".join(entries)
