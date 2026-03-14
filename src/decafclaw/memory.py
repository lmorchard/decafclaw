"""Memory operations — read and write markdown memory files."""

import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def memory_dir(config, user_id: str) -> Path:
    """Compute the memory directory path for a user."""
    return Path(config.data_home) / "workspace" / config.agent_id / "memories" / user_id


def save_entry(config, user_id: str, channel_name: str, channel_id: str,
               thread_id: str, tags: list[str], content: str) -> str:
    """Append a memory entry to today's file."""
    if not user_id:
        return "[error: no user_id in context]"

    now = datetime.now()
    base = memory_dir(config, user_id) / str(now.year)
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

    log.info(f"Saved memory for {user_id} tagged [{tag_str}]")
    return f"Saved memory tagged [{tag_str}]"


def search_entries(config, user_id: str, query: str, context_lines: int = 3) -> str:
    """Search all memory files for a user using case-insensitive substring matching."""
    if not user_id:
        return "[error: no user_id in context]"

    base = memory_dir(config, user_id)
    if not base.exists():
        return f"No memories found matching '{query}'"

    query_lower = query.lower()
    results = []

    # Collect all .md files sorted by path (chronological)
    md_files = sorted(base.rglob("*.md"))

    for filepath in md_files:
        lines = filepath.read_text().splitlines()
        matched_lines = set()

        # Find all matching line indices
        for i, line in enumerate(lines):
            if query_lower in line.lower():
                # Add the match and its context window
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                for j in range(start, end):
                    matched_lines.add(j)

        if matched_lines:
            # Build output with contiguous blocks
            rel_path = filepath.relative_to(base)
            block_lines = []
            sorted_indices = sorted(matched_lines)

            for idx, line_num in enumerate(sorted_indices):
                # Add separator between non-contiguous blocks
                if idx > 0 and line_num > sorted_indices[idx - 1] + 1:
                    block_lines.append("---")
                block_lines.append(lines[line_num])

            results.append(f"### {rel_path}\n\n" + "\n".join(block_lines))

    if not results:
        return f"No memories found matching '{query}'"

    return "\n\n".join(results)


def recent_entries(config, user_id: str, n: int = 5) -> str:
    """Return the last N memory entries for a user."""
    if not user_id:
        return "[error: no user_id in context]"

    base = memory_dir(config, user_id)
    if not base.exists():
        return "No memories found"

    # Collect all .md files sorted by path descending (most recent first)
    md_files = sorted(base.rglob("*.md"), reverse=True)

    entries = []
    for filepath in md_files:
        text = filepath.read_text()
        # Split on entry headers (## YYYY-MM-DD HH:MM)
        parts = text.split("\n## ")
        # First part may be empty or have no header, skip it
        for part in reversed(parts):
            part = part.strip()
            if not part:
                continue
            # Re-add the header prefix if it was split off
            if not part.startswith("## "):
                part = "## " + part
            entries.append(part)
            if len(entries) >= n:
                break
        if len(entries) >= n:
            break

    if not entries:
        return "No memories found"

    return "\n\n".join(entries)
