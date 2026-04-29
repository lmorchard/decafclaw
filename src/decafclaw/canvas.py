"""Per-conversation canvas state — sidecar persistence + state operations.

The canvas is a web-only display surface backed by a JSON sidecar at
``workspace/conversations/{conv_id}.canvas.json``. State shape:

    {
      "schema_version": 1,
      "active_tab": "canvas_2" | null,
      "next_tab_id": 3,
      "tabs": [{"id", "label", "widget_type", "data"}, ...],
    }

Phase 4 multi-tab. ``next_tab_id`` is a monotonic counter so closed-then-
recreated tab IDs never rebind. Phase 3 sidecars (no ``next_tab_id``)
get one synthesized on first read.

Mutation functions emit ``canvas_update`` events with one of these
``kind`` values: ``new_tab``, ``update``, ``close_tab``, ``set_active``,
``clear``. Disk I/O is fail-open: corrupt or missing files are treated
as empty canvas state.
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
    return {"schema_version": 1, "active_tab": None, "next_tab_id": 1, "tabs": []}


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


def _derive_next_tab_id(tabs: list) -> int:
    """Compute next_tab_id from existing tab ids (canvas_N format)."""
    max_n = 0
    for t in tabs:
        tab_id = t.get("id", "")
        if tab_id.startswith("canvas_"):
            try:
                n = int(tab_id.split("_", 1)[1])
                max_n = max(max_n, n)
            except (ValueError, IndexError):
                continue
    return max_n + 1


def read_canvas_state(config, conv_id: str) -> dict:
    """Read canvas state from disk; fail-open with empty state on any error."""
    path = _canvas_sidecar_path(config, conv_id)
    if not path.exists():
        return empty_canvas_state()
    try:
        state = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to read canvas state for %s; treating as empty",
                    conv_id, exc_info=True)
        return empty_canvas_state()
    # Phase 3 migration: synthesize next_tab_id from existing tabs if missing.
    if "next_tab_id" not in state:
        state["next_tab_id"] = _derive_next_tab_id(state.get("tabs", []))
    return state


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
    tab_id: str | None = None


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
                              *,
                              active_tab: str | None,
                              tab: dict | None = None,
                              closed_tab_id: str | None = None) -> None:
    """Publish a canvas_update event for subscribed clients. Fail-open."""
    if emit is None:
        return
    payload = {
        "type": "canvas_update",
        "kind": kind,
        "active_tab": active_tab,
        "tab": tab,
    }
    if closed_tab_id is not None:
        payload["closed_tab_id"] = closed_tab_id
    try:
        await emit(conv_id, payload)
    except Exception:
        log.warning("canvas_update emit failed for %s", conv_id, exc_info=True)


# ---------------------------------------------------------------------------
# Mutation operations
# ---------------------------------------------------------------------------

async def clear_canvas(config,
                       conv_id: str,
                       emit: EmitFn | None = None) -> CanvasOpResult:
    """Remove all canvas tabs; hides the panel.

    ``next_tab_id`` is preserved across clears so a closed tab id is never
    rebound — protects standalone tab-locked URLs (`/canvas/{conv}/canvas_2`)
    from silently switching to a different widget after a clear+new_tab cycle.
    """
    state = read_canvas_state(config, conv_id)
    if not state.get("tabs"):
        return CanvasOpResult(ok=True, text="canvas already empty")
    next_id = state.get("next_tab_id", 1)
    state = empty_canvas_state()
    state["next_tab_id"] = next_id
    if not write_canvas_state(config, conv_id, state):
        return CanvasOpResult(ok=False,
                              error="failed to write canvas state to disk")
    await _emit_canvas_update(emit, conv_id, "clear", active_tab=None, tab=None)
    return CanvasOpResult(ok=True, text="canvas cleared")


# ---------------------------------------------------------------------------
# Tab-aware state operations
# ---------------------------------------------------------------------------

def get_tab(config, conv_id: str, tab_id: str) -> dict | None:
    """Return a specific tab dict by id, or None if not found."""
    state = read_canvas_state(config, conv_id)
    for tab in state.get("tabs", []):
        if tab.get("id") == tab_id:
            return tab
    return None


async def new_tab(config,
                  conv_id: str,
                  widget_type: str,
                  data: dict,
                  label: str | None = None,
                  emit: EmitFn | None = None) -> CanvasOpResult:
    """Append a new tab and make it active. Returns the new tab_id."""
    err = _validate_widget_for_canvas(widget_type, data)
    if err:
        return CanvasOpResult(ok=False, error=err)
    registry = get_widget_registry()
    if registry is not None:
        data = registry.normalize(widget_type, data)
    state = read_canvas_state(config, conv_id)
    next_n = state.get("next_tab_id", 1)
    tab_id = f"canvas_{next_n}"
    final_label = label or _derive_label(widget_type, data)
    new_tab_dict = {
        "id": tab_id,
        "label": final_label,
        "widget_type": widget_type,
        "data": data,
    }
    state["tabs"].append(new_tab_dict)
    state["active_tab"] = tab_id
    state["next_tab_id"] = next_n + 1
    if not write_canvas_state(config, conv_id, state):
        return CanvasOpResult(ok=False,
                              error="failed to write canvas state to disk")
    await _emit_canvas_update(emit, conv_id, "new_tab",
                              active_tab=tab_id, tab=new_tab_dict)
    return CanvasOpResult(ok=True, text="tab created", tab_id=tab_id)


async def update_tab(config,
                     conv_id: str,
                     tab_id: str,
                     data: dict,
                     emit: EmitFn | None = None) -> CanvasOpResult:
    """Replace data of an existing tab; preserves widget_type + label."""
    state = read_canvas_state(config, conv_id)
    for tab in state.get("tabs", []):
        if tab.get("id") == tab_id:
            err = _validate_widget_for_canvas(tab["widget_type"], data)
            if err:
                return CanvasOpResult(ok=False, error=err)
            registry = get_widget_registry()
            if registry is not None:
                data = registry.normalize(tab["widget_type"], data)
            tab["data"] = data
            if not write_canvas_state(config, conv_id, state):
                return CanvasOpResult(ok=False,
                                      error="failed to write canvas state to disk")
            await _emit_canvas_update(emit, conv_id, "update",
                                      active_tab=state.get("active_tab"),
                                      tab=tab)
            return CanvasOpResult(ok=True, text=f"tab {tab_id} updated")
    return CanvasOpResult(ok=False, error=f"tab '{tab_id}' not found")


async def close_tab(config,
                    conv_id: str,
                    tab_id: str,
                    emit: EmitFn | None = None) -> CanvasOpResult:
    """Remove a tab. If active, switch to left neighbor (else right; else None)."""
    state = read_canvas_state(config, conv_id)
    tabs = state.get("tabs", [])
    idx = next((i for i, t in enumerate(tabs) if t.get("id") == tab_id), -1)
    if idx < 0:
        return CanvasOpResult(ok=False, error=f"tab '{tab_id}' not found")
    was_active = state.get("active_tab") == tab_id
    tabs.pop(idx)
    if was_active:
        if tabs:
            # Prefer left (idx-1), else right (idx now points at right neighbor).
            new_idx = idx - 1 if idx - 1 >= 0 else 0
            state["active_tab"] = tabs[new_idx]["id"]
        else:
            state["active_tab"] = None
    if not write_canvas_state(config, conv_id, state):
        return CanvasOpResult(ok=False,
                              error="failed to write canvas state to disk")
    new_active = state.get("active_tab")
    await _emit_canvas_update(emit, conv_id, "close_tab",
                              active_tab=new_active,
                              tab=None,
                              closed_tab_id=tab_id)
    text = (f"tab {tab_id} closed"
            + (f" (active={new_active})" if new_active
               else " (canvas hidden — no tabs left)"))
    return CanvasOpResult(ok=True, text=text)


async def set_active_tab(config,
                         conv_id: str,
                         tab_id: str,
                         emit: EmitFn | None = None) -> CanvasOpResult:
    """Set the active tab; broadcasts kind='set_active'."""
    state = read_canvas_state(config, conv_id)
    if not any(t.get("id") == tab_id for t in state.get("tabs", [])):
        return CanvasOpResult(ok=False, error=f"tab '{tab_id}' not found")
    state["active_tab"] = tab_id
    if not write_canvas_state(config, conv_id, state):
        return CanvasOpResult(ok=False,
                              error="failed to write canvas state to disk")
    await _emit_canvas_update(emit, conv_id, "set_active",
                              active_tab=tab_id, tab=None)
    return CanvasOpResult(ok=True, text=f"active tab set to {tab_id}")
