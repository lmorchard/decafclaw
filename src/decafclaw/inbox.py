import json
import os
import uuid
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from decafclaw.conversation_paths import sidecar_path

def _read_inbox(config, conv_id: str) -> list[dict]:
    path = sidecar_path(config, conv_id, "inbox.jsonl")
    if not path.exists():
        return []
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                lines.append(json.loads(line))
    return lines

def _write_inbox(config, conv_id: str, messages: list[dict]) -> None:
    path = sidecar_path(config, conv_id, "inbox.jsonl")
    if not messages:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, separators=(",", ":")) + "\n")
    os.replace(tmp, path)

def _append_inbox(config, conv_id: str, message: dict) -> None:
    path = sidecar_path(config, conv_id, "inbox.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(message, separators=(",", ":")) + "\n")
