"""Vault journal parsing — daily-file entry extraction and context rendering.

Extracted from ``tools.py`` (#1369). Parses the daily journal files
(``<journal_dir>/YYYY/YYYY-MM-DD.md``) written by ``vault_journal_append``,
splitting them into timestamped entries on the ``## YYYY-MM-DD HH:MM`` headers,
and renders recent entries as a single context block.

No dependency on the confirmation gate, tool results, or event machinery —
those stay in ``tools.py``.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

# Entry header written by ``vault_journal_append``: ``## YYYY-MM-DD HH:MM``.
# The date pattern makes false positives from body markdown negligible.
_JOURNAL_ENTRY_HEADER = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*$", re.MULTILINE)


@dataclass
class RecentJournalEntry:
    """One parsed journal entry: its header timestamp + full text block."""

    timestamp: datetime  # naive local, matching how entries are written
    text: str  # header + metadata bullets + body, whitespace-stripped


def read_recent_journal_entries(
    config,
    *,
    now: datetime,
    max_hours: int,
    max_entries: int,
) -> list[RecentJournalEntry]:
    """Most-recent journal entries written within ``max_hours`` of ``now``.

    Reads only the daily files (``<journal_dir>/YYYY/YYYY-MM-DD.md``) whose
    dates fall in the window, so a 24h window touches at most two files and
    crossing midnight is handled. Timestamps come from the
    ``## YYYY-MM-DD HH:MM`` entry headers, parsed as naive local datetimes to
    match ``vault_journal_append``'s ``datetime.now()`` writes. Missing files
    are skipped. Returns up to ``max_entries`` entries, chronological
    (oldest first) so the LLM reads them in order.
    """
    if max_hours <= 0 or max_entries <= 0:
        return []

    cutoff = now - timedelta(hours=max_hours)
    journal_dir = config.vault_agent_journal_dir

    entries: list[RecentJournalEntry] = []
    # Iterate each date the window touches (inclusive of both ends).
    day = cutoff.date()
    end = now.date()
    while day <= end:
        path = journal_dir / str(day.year) / f"{day:%Y-%m-%d}.md"
        day += timedelta(days=1)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        matches = list(_JOURNAL_ENTRY_HEADER.finditer(text))
        for i, m in enumerate(matches):
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if ts < cutoff:
                continue
            block_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[m.start() : block_end].strip()
            entries.append(RecentJournalEntry(timestamp=ts, text=block))

    entries.sort(key=lambda e: e.timestamp)
    return entries[-max_entries:]


def format_recent_journal_for_context(
    entries: list[RecentJournalEntry],
    max_hours: int,
) -> str:
    """Render recent journal entries as a single context block."""
    header = f"Recent journal entries (written in the last {max_hours}h, most recent last):"
    body = "\n\n".join(e.text for e in entries)
    return f"{header}\n\n{body}"
