"""Conversation archive — append-only JSONL files per conversation."""

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Roles that are valid in LLM message history. Metadata roles (model,
# reflection, etc.) are stored in the archive but filtered out before
# sending to the LLM.
LLM_ROLES = {"system", "user", "assistant", "tool"}


def archive_path(config, conv_id: str) -> Path:
    """Compute the archive file path for a conversation."""
    return config.workspace_path / "conversations" / f"{conv_id}.jsonl"


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
    return config.workspace_path / "conversations" / f"{conv_id}.compacted.jsonl"


def write_compacted_history(config, conv_id: str, messages: list[dict]):
    """Write compacted working history to a sidecar file (archive is unchanged)."""
    path = _compacted_path(config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for msg in messages:
            if "timestamp" not in msg:
                msg = {**msg, "timestamp": datetime.now().isoformat()}
            f.write(json.dumps(msg) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, skipping corrupt lines."""
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
    messages = _read_jsonl(path)
    return messages or None


def read_archive(config, conv_id: str) -> list[dict]:
    """Read all messages from a conversation archive."""
    path = archive_path(config, conv_id)
    if not path.exists():
        return []
    return _read_jsonl(path)


def restore_history(config, conv_id: str) -> list[dict] | None:
    """Load history from compacted sidecar + newer archive entries, or full archive.

    Returns the restored message list, or None if no archive exists.
    """
    compacted = read_compacted_history(config, conv_id)
    if compacted:
        full = read_archive(config, conv_id)
        last_ts = compacted[-1].get("timestamp", "")
        newer = [m for m in full if m.get("timestamp", "") > last_ts]
        return compacted + newer

    archived = read_archive(config, conv_id)
    return archived if archived else None
