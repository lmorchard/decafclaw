"""Per-conversation sticky-slot state — sidecar persistence + operations.

The sticky slot is a web-only, single-widget surface pinned above the chat
input, backed by a JSON sidecar at
``workspace/conversations/{conv_id}/sticky.json``. State shape:

    {"schema_version": 1, "widget_type": "markdown_document" | null,
     "data": {...} | null}

A new pin replaces the previous one (single slot). Mutation functions emit
``sticky_set`` / ``sticky_clear`` events. Disk I/O is fail-open: corrupt or
missing files are treated as an empty (cleared) slot. Mirrors ``canvas.py``.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .conversation_paths import sidecar_path
from .widgets import get_widget_registry

log = logging.getLogger(__name__)

EmitFn = Callable[[str, dict], Awaitable[None]]


def empty_sticky_state() -> dict:
    """Return a fresh empty (cleared) sticky-slot state dict."""
    return {"schema_version": 1, "widget_type": None, "data": None}


def _sticky_sidecar_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "sticky.json")


def read_sticky_state(config, conv_id: str) -> dict:
    """Read sticky state from disk; fail-open with empty state on any error."""
    path = _sticky_sidecar_path(config, conv_id)
    if not path.exists():
        return empty_sticky_state()
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to read sticky state for %s; treating as empty",
                    conv_id, exc_info=True)
        return empty_sticky_state()


def write_sticky_state(config, conv_id: str, state: dict) -> bool:
    """Write sticky state via tmp-file-then-rename for atomicity."""
    path = _sticky_sidecar_path(config, conv_id)
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, indent=2) + "\n")
        tmp.replace(path)
        return True
    except OSError:
        log.warning("Failed to write sticky state for %s", conv_id, exc_info=True)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError as cleanup_exc:
            log.debug("sticky tmp cleanup failed: %s", cleanup_exc)
        return False


@dataclass
class StickyOpResult:
    """Outcome of a sticky state operation."""
    ok: bool
    text: str = ""
    error: str = ""
