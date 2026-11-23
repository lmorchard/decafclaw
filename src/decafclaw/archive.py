"""Conversation archive — append-only JSONL files per conversation."""

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .conversation_paths import sidecar_path

log = logging.getLogger(__name__)

# Roles that are valid in LLM message history. Metadata roles (model,
# reflection, etc.) are stored in the archive but filtered out before
# sending to the LLM.
LLM_ROLES = {"system", "user", "assistant", "tool"}


def archive_path(config, conv_id: str) -> Path:
    """Compute the archive file path for a conversation."""
    return sidecar_path(config, conv_id, "archive.jsonl")


def append_message(config, conv_id: str, message: dict):
    """Append a message to the conversation archive with timestamp."""
    path = archive_path(config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Add timestamp if not already present
    if "timestamp" not in message:
        message = {**message, "timestamp": datetime.now().isoformat()}
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
                    msg = {**msg, "timestamp": datetime.now().isoformat()}
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
    return messages


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
        last_ts = compacted[-1].get("timestamp", "")
        newer = [
            m for m in full if m.get("timestamp", "") > last_ts and m.get("role") not in ("compaction", "model", "mode")
        ]
        return compacted + newer

    archived = read_archive(config, conv_id)
    if not archived:
        return None
    restored = [m for m in archived if m.get("role") not in ("compaction", "model", "mode")]
    return restored if restored else None
