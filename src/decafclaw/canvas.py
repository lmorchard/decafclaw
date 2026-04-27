"""Per-conversation canvas state — sidecar persistence + state operations.

The canvas is a web-only display surface backed by a JSON sidecar at
``workspace/conversations/{conv_id}.canvas.json``. State shape:

    {
      "schema_version": 1,
      "active_tab": "canvas_1" | null,
      "tabs": [{"id", "label", "widget_type", "data"}, ...],
    }

In Phase 3 the UI is single-tab; ``tabs`` has length 0 or 1. The shape is
preserved so a Phase 4 multi-tab UI can ship without a schema migration.

Mutation functions emit ``canvas_update`` events via the supplied
``emit`` callable (typically ``ConversationManager.emit``) so subscribed
WebSocket clients update live. All disk I/O is fail-open: corrupt or
missing files are treated as empty canvas state.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .widgets import get_widget_registry

log = logging.getLogger(__name__)

EmitFn = Callable[[str, dict], Awaitable[None]]


def empty_canvas_state() -> dict:
    """Return a fresh empty canvas-state dict."""
    return {"schema_version": 1, "active_tab": None, "tabs": []}


def _canvas_sidecar_path(config, conv_id: str) -> Path:
    """Path to the canvas JSON sidecar; guarded against directory traversal."""
    base_dir = (config.workspace_path / "conversations").resolve()
    # Reject empty or any conv_id that contains path separators or dotdot.
    if not conv_id or "/" in conv_id or "\\" in conv_id or ".." in conv_id:
        return base_dir / "_invalid.canvas.json"
    path = (base_dir / f"{conv_id}.canvas.json").resolve()
    if not path.is_relative_to(base_dir):
        return base_dir / "_invalid.canvas.json"
    return path


def read_canvas_state(config, conv_id: str) -> dict:
    """Read canvas state from disk; fail-open with empty state on any error."""
    path = _canvas_sidecar_path(config, conv_id)
    if not path.exists():
        return empty_canvas_state()
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to read canvas state for %s; treating as empty",
                    conv_id, exc_info=True)
        return empty_canvas_state()


def write_canvas_state(config, conv_id: str, state: dict) -> bool:
    """Write canvas state via tmp-file-then-rename for atomicity.

    Returns True on success, False on I/O failure (logged). Callers
    should propagate the False result so the agent / web UI sees a
    clear error rather than crashing the loop.
    """
    path = _canvas_sidecar_path(config, conv_id)
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, indent=2) + "\n")
        tmp.replace(path)
        return True
    except OSError:
        log.warning("Failed to write canvas state for %s", conv_id, exc_info=True)
        # Best-effort cleanup of the partial tmp file.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError as cleanup_exc:
            log.debug("canvas tmp cleanup failed: %s", cleanup_exc)
        return False


# ---------------------------------------------------------------------------
# State operation result
# ---------------------------------------------------------------------------

@dataclass
class CanvasOpResult:
    """Outcome of a canvas state operation."""
    ok: bool
    text: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _humanize(s: str) -> str:
    return s.replace("_", " ").title()


def _derive_label(widget_type: str, data: dict) -> str:
    """Derive a default tab label.

    For ``markdown_document``, use the first H1 line in ``content`` if
    present; otherwise fall back to ``"Untitled"``. For other widget
    types, humanize the widget type name.
    """
    if widget_type == "markdown_document":
        content = data.get("content", "") or ""
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip() or "Untitled"
        return "Untitled"
    return _humanize(widget_type)


def _validate_widget_for_canvas(widget_type: str, data: dict) -> str | None:
    """Return an error message string, or None if validation passes."""
    registry = get_widget_registry()
    if registry is None:
        return "widget registry not initialized"
    descriptor = registry.get(widget_type)
    if descriptor is None:
        return f"widget '{widget_type}' not registered"
    if "canvas" not in descriptor.modes:
        return f"widget '{widget_type}' does not support canvas mode"
    ok, msg = registry.validate(widget_type, data)
    if not ok:
        return f"schema validation failed: {msg}"
    return None


async def _emit_canvas_update(emit: EmitFn | None,
                              conv_id: str,
                              kind: str,
                              state: dict) -> None:
    """Publish a canvas_update event for subscribed clients. Fail-open."""
    if emit is None:
        return
    active_id = state.get("active_tab")
    tab = None
    for t in state.get("tabs", []):
        if t.get("id") == active_id:
            tab = t
            break
    payload = {
        "type": "canvas_update",
        "kind": kind,
        "active_tab": active_id,
        "tab": tab,
    }
    try:
        await emit(conv_id, payload)
    except Exception:
        log.warning("canvas_update emit failed for %s", conv_id, exc_info=True)


# ---------------------------------------------------------------------------
# Public read helper
# ---------------------------------------------------------------------------

def get_active_tab(config, conv_id: str) -> dict | None:
    """Return the active tab dict, or None if canvas is empty."""
    state = read_canvas_state(config, conv_id)
    active_id = state.get("active_tab")
    if not active_id:
        return None
    for tab in state.get("tabs", []):
        if tab.get("id") == active_id:
            return tab
    return None


# ---------------------------------------------------------------------------
# Mutation operations
# ---------------------------------------------------------------------------

async def set_canvas(config,
                     conv_id: str,
                     widget_type: str,
                     data: dict,
                     label: str | None = None,
                     emit: EmitFn | None = None) -> CanvasOpResult:
    """Replace the canvas with a single new tab containing ``widget_type``."""
    err = _validate_widget_for_canvas(widget_type, data)
    if err:
        return CanvasOpResult(ok=False, error=err)
    final_label = label or _derive_label(widget_type, data)
    state = {
        "schema_version": 1,
        "active_tab": "canvas_1",
        "tabs": [{
            "id": "canvas_1",
            "label": final_label,
            "widget_type": widget_type,
            "data": data,
        }],
    }
    if not write_canvas_state(config, conv_id, state):
        return CanvasOpResult(ok=False,
                              error="failed to write canvas state to disk")
    await _emit_canvas_update(emit, conv_id, "set", state)
    return CanvasOpResult(ok=True, text="canvas updated")


async def update_canvas(config,
                        conv_id: str,
                        data: dict,
                        emit: EmitFn | None = None) -> CanvasOpResult:
    """Replace the data of the existing canvas tab. Same widget_type, same label."""
    state = read_canvas_state(config, conv_id)
    tabs = state.get("tabs", [])
    if not tabs:
        return CanvasOpResult(ok=False,
                              error="no canvas widget set; call canvas_set first")
    tab = tabs[0]
    err = _validate_widget_for_canvas(tab["widget_type"], data)
    if err:
        return CanvasOpResult(ok=False, error=err)
    tab["data"] = data
    if not write_canvas_state(config, conv_id, state):
        return CanvasOpResult(ok=False,
                              error="failed to write canvas state to disk")
    await _emit_canvas_update(emit, conv_id, "update", state)
    return CanvasOpResult(ok=True, text="canvas updated")


async def clear_canvas(config,
                       conv_id: str,
                       emit: EmitFn | None = None) -> CanvasOpResult:
    """Remove the canvas widget; hides the panel."""
    state = read_canvas_state(config, conv_id)
    if not state.get("tabs"):
        return CanvasOpResult(ok=True, text="canvas already empty")
    state = empty_canvas_state()
    if not write_canvas_state(config, conv_id, state):
        return CanvasOpResult(ok=False,
                              error="failed to write canvas state to disk")
    await _emit_canvas_update(emit, conv_id, "clear", state)
    return CanvasOpResult(ok=True, text="canvas cleared")
