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


def _validate_widget_for_sticky(widget_type: str, data: dict) -> str | None:
    """Return an error message string, or None if validation passes."""
    registry = get_widget_registry()
    if registry is None:
        return "widget registry not initialized"
    descriptor = registry.get(widget_type)
    if descriptor is None:
        return f"widget '{widget_type}' not registered"
    if "sticky" not in descriptor.modes:
        return f"widget '{widget_type}' does not support sticky mode"
    if descriptor.accepts_input:
        # The sticky slot is display-only (v1): input widgets stay inline so
        # the agent's pause-and-ask flow is unambiguous. A widget that both
        # declares sticky mode and accepts input is a misconfiguration.
        return f"widget '{widget_type}' accepts input; the sticky slot is display-only"
    ok, msg = registry.validate(widget_type, data)
    if not ok:
        return f"schema validation failed: {msg}"
    return None


async def _emit_sticky(emit: EmitFn | None, conv_id: str, payload: dict) -> None:
    """Publish a sticky event for subscribed clients. Fail-open."""
    if emit is None:
        return
    try:
        await emit(conv_id, payload)
    except Exception:
        log.warning("sticky emit failed for %s", conv_id, exc_info=True)


async def set_sticky(config, conv_id: str, widget_type: str, data: dict,
                     emit: EmitFn | None = None) -> StickyOpResult:
    """Pin a widget into the sticky slot, replacing any previous occupant."""
    err = _validate_widget_for_sticky(widget_type, data)
    if err:
        return StickyOpResult(ok=False, error=err)
    registry = get_widget_registry()
    if registry is not None:
        data = registry.normalize(widget_type, data)
    state = {"schema_version": 1, "widget_type": widget_type, "data": data}
    if not write_sticky_state(config, conv_id, state):
        return StickyOpResult(ok=False, error="failed to write sticky state to disk")
    await _emit_sticky(emit, conv_id, {
        "type": "sticky_set",
        "widget_type": widget_type,
        "data": data,
    })
    return StickyOpResult(ok=True, text="sticky widget pinned")


async def clear_sticky(config, conv_id: str,
                       emit: EmitFn | None = None) -> StickyOpResult:
    """Clear the sticky slot; hides it."""
    if not write_sticky_state(config, conv_id, empty_sticky_state()):
        return StickyOpResult(ok=False, error="failed to write sticky state to disk")
    await _emit_sticky(emit, conv_id, {"type": "sticky_clear"})
    return StickyOpResult(ok=True, text="sticky slot cleared")
