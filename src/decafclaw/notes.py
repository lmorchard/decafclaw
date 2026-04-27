"""Per-conversation scratchpad — append-only markdown notes.

A cheap persistent place for the agent to jot "user said X", "we
decided Y", "try Z next turn" without polluting the vault or
overloading the checklist. Notes are auto-injected into context at
turn start so the model doesn't pay a tool call per turn to read
them. See ``docs/notes.md`` and #299.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Marker prefix for note lines. Same character we already use for
# checklist entries so the file is markdown-readable in editors.
_LINE_PREFIX = "- "
_SEPARATOR = " — "


@dataclass(frozen=True)
class Note:
    timestamp: str  # ISO-8601 UTC
    text: str

    def to_line(self) -> str:
        return f"{_LINE_PREFIX}{self.timestamp}{_SEPARATOR}{self.text}"


def notes_path(config, conv_id: str) -> Path:
    """Resolve the per-conversation notes file. Colocated with the
    conversation archive + sidecars under ``workspace/conversations/``
    as ``{conv_id}.notes.md``. Sandboxed — strips traversal characters
    and falls back to a sentinel name on empty input."""
    base_dir = (config.workspace_path / "conversations").resolve()
    safe = conv_id.replace("/", "").replace("\\", "").replace("..", "")
    if not safe:
        return base_dir / "_invalid.notes.md"
    path = (base_dir / f"{safe}.notes.md").resolve()
    if not path.is_relative_to(base_dir):
        return base_dir / "_invalid.notes.md"
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sanitize(text: str) -> str:
    """Collapse newlines so each note stays on a single line."""
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()


def append_note(
    config,
    conv_id: str,
    text: str,
    *,
    now: str | None = None,
    max_chars: int = 1024,
    max_total_entries: int = 0,
) -> Note:
    """Append one note. Truncates at ``max_chars``. Returns the Note
    that was actually written so the caller can echo back exactly what
    landed (post-truncation, post-sanitize).

    When ``max_total_entries > 0`` and the file would exceed that line
    count after the append, the oldest entries are dropped so the file
    stays bounded — keeps long-running conversations from accumulating
    unbounded IO/CPU per ``read_notes`` (which the composer calls every
    interactive turn). Trim happens via tmp-file + ``os.replace`` so
    a crash mid-trim can't corrupt the file.

    Raises ``ValueError`` if ``text`` is empty after sanitization.
    """
    sanitized = _sanitize(text)
    if not sanitized:
        raise ValueError("note text is empty")
    if max_chars > 0 and len(sanitized) > max_chars:
        sanitized = sanitized[:max_chars]
    note = Note(timestamp=now or _now_iso(), text=sanitized)
    path = notes_path(config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Fast path: just append.
    if max_total_entries <= 0:
        with path.open("a", encoding="utf-8") as f:
            f.write(note.to_line() + "\n")
        return note

    # Bounded path: read existing, build the new tail, atomic-rewrite
    # iff we'd otherwise exceed the cap. For files under cap we still
    # take the cheap append path so steady-state cost is one write.
    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()
    if len(existing_lines) + 1 <= max_total_entries:
        with path.open("a", encoding="utf-8") as f:
            f.write(note.to_line() + "\n")
        return note

    keep = existing_lines[-(max_total_entries - 1):]
    keep.append(note.to_line())
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return note


def _parse_line(line: str) -> Note | None:
    """Reverse of ``Note.to_line``. Lenient — returns None on lines
    that don't fit the expected shape (legacy edits, manual writes)."""
    raw = line.rstrip("\n")
    if not raw.startswith(_LINE_PREFIX):
        return None
    body = raw[len(_LINE_PREFIX):]
    sep_idx = body.find(_SEPARATOR)
    if sep_idx < 0:
        return None
    timestamp = body[:sep_idx]
    text = body[sep_idx + len(_SEPARATOR):]
    if not timestamp or not text:
        return None
    return Note(timestamp=timestamp, text=text)


def read_notes(
    config,
    conv_id: str,
    *,
    limit: int | None = None,
    max_chars: int | None = None,
) -> list[Note]:
    """Return notes oldest-first, truncated to the most recent set
    that fits the limits.

    ``limit``: max number of entries (None = all).
    ``max_chars``: max sum of `text` lengths across the returned set.
        When the cap is exceeded the OLDEST entries are dropped first
        until the cap is satisfied.
    """
    path = notes_path(config, conv_id)
    if not path.exists():
        return []
    notes: list[Note] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        note = _parse_line(line)
        if note is not None:
            notes.append(note)

    if limit is not None and limit > 0:
        notes = notes[-limit:]

    if max_chars is not None and max_chars > 0:
        # Drop oldest until the sum of text lengths is under cap.
        total = sum(len(n.text) for n in notes)
        while notes and total > max_chars:
            dropped = notes.pop(0)
            total -= len(dropped.text)

    return notes


def format_notes_for_context(notes: list[Note]) -> str:
    """Render the inject block. Returns "" when notes is empty."""
    if not notes:
        return ""
    lines = ["[Conversation notes — your scratchpad for this conversation]", ""]
    lines.extend(n.to_line() for n in notes)
    return "\n".join(lines)
