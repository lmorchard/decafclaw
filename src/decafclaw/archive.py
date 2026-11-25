"""Conversation archive — append-only JSONL files per conversation."""

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from .conversation_paths import sidecar_path
from .wake_replay import normalize_suppressed_wakes

log = logging.getLogger(__name__)

# Roles that are valid in LLM message history. Metadata roles (model,
# reflection, etc.) are stored in the archive but filtered out before
# sending to the LLM.
LLM_ROLES = {"system", "user", "assistant", "tool"}


def now_iso() -> str:
    """UTC timestamp in the canonical archive format (#1829).

    Every row this module stamps goes through here, so a single archive
    never mixes timezones. Rows written before #1829 used naive local
    time (``datetime.now()``), while background events and confirmation
    requests used UTC — and the two orders disagree by the local UTC
    offset, so lexicographic comparisons silently dropped the newest
    turns. See :func:`parse_timestamp` for the reader-side counterpart.
    """
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse any timestamp this project has ever written, as aware UTC.

    Three shapes exist in the wild:

    * ``2026-11-25T08:22:53.077847+00:00`` — current canonical (#1829)
    * ``2026-11-25T08:22:53Z`` — older UTC writers (background, confirms)
    * ``2026-11-25T00:22:53.077847`` — pre-#1829 naive **local** time

    The naive form is interpreted as UTC so comparisons stay total and
    well-ordered. That is knowingly approximate for legacy rows (they are
    off by the host's UTC offset), but it is monotonic *within* a legacy
    file, which is all the ordering callers need, and it is stable across
    hosts. Returns ``None`` for missing or unparseable values.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def archive_path(config, conv_id: str) -> Path:
    """Compute the archive file path for a conversation."""
    return sidecar_path(config, conv_id, "archive.jsonl")


def append_message(config, conv_id: str, message: dict):
    """Append a message to the conversation archive with timestamp."""
    path = archive_path(config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Add timestamp if not already present
    if "timestamp" not in message:
        message = {**message, "timestamp": now_iso()}
    with open(path, "a") as f:
        f.write(json.dumps(message) + "\n")


def _compacted_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "compacted.jsonl")


def _write_jsonl_atomic(path: Path, messages: list[dict]):
    """Write a complete JSONL file atomically, stamping missing timestamps."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Stage to a temp file in the same directory, then os.replace() into
    # place, so a mid-write failure (e.g. disk full) can never leave a
    # truncated file that a reader could load.
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for msg in messages:
                if "timestamp" not in msg:
                    msg = {**msg, "timestamp": now_iso()}
                f.write(json.dumps(msg) + "\n")
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def write_compacted_history(config, conv_id: str, messages: list[dict]):
    """Write compacted working history to a sidecar file (archive is unchanged)."""
    _write_jsonl_atomic(_compacted_path(config, conv_id), messages)


def write_archive(config, conv_id: str, messages: list[dict]):
    """Write a complete conversation archive (#1581 conversation fork).

    Used to seed a new conversation with a sliced history. The caller owns
    the slice; this only guarantees an atomic write body.
    """
    _write_jsonl_atomic(archive_path(config, conv_id), messages)


def read_archive_file(path: Path) -> list[dict]:
    """Read a conversation archive file, skipping corrupt lines.

    Path-based counterpart to :func:`read_archive` for callers that already
    hold an archive path (offline scans, surveys) rather than a conv id.
    Silent-wake rows are normalized on read so every replay surface sees the
    persisted suppression flag and never the raw sentinel (#1852).
    """
    messages = []
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("Skipping corrupt JSONL line %d in %s", lineno, path)
    return normalize_suppressed_wakes(messages)


def read_compacted_history(config, conv_id: str) -> list[dict] | None:
    """Read compacted working history if available, else return None."""
    path = _compacted_path(config, conv_id)
    if not path.exists():
        return None
    messages = read_archive_file(path)
    return messages or None


def read_archive(config, conv_id: str) -> list[dict]:
    """Read all messages from a conversation archive."""
    path = archive_path(config, conv_id)
    if not path.exists():
        return []
    return read_archive_file(path)


def iter_archive_messages(config, conv_id: str) -> Iterator[dict]:
    """Yield messages from a conversation archive, skipping corrupt lines.

    Streams the JSONL file, so a caller that scans for matches does not hold
    the whole archive in memory.
    """
    path = archive_path(config, conv_id)
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                log.warning("Skipping corrupt JSONL line %d in %s", lineno, path)


def restore_history(config, conv_id: str) -> list[dict] | None:
    """Load history from compacted sidecar + newer archive entries, or full archive.

    Metadata records (compaction, model, mode) are excluded from restored history.
    Returns the restored message list, or None if no archive exists.
    """
    compacted = read_compacted_history(config, conv_id)
    if compacted:
        full = read_archive(config, conv_id)
        # Compare parsed instants, not raw strings: archives hold a mix of
        # naive-local (pre-#1829) and UTC-offset stamps, and lexicographic
        # order across those two shapes drops real turns (#1829).
        last_ts = parse_timestamp(compacted[-1].get("timestamp"))
        newer = []
        for m in full:
            if m.get("role") in ("compaction", "model", "mode"):
                continue
            ts = parse_timestamp(m.get("timestamp"))
            if ts is not None and last_ts is not None and ts > last_ts:
                newer.append(m)
        return compacted + newer

    archived = read_archive(config, conv_id)
    if not archived:
        return None
    restored = [m for m in archived if m.get("role") not in ("compaction", "model", "mode")]
    return restored if restored else None
