"""Per-conversation canvas state — sidecar persistence + state operations.

The canvas is a web-only display surface backed by a JSON sidecar at
``workspace/conversations/{conv_id}/canvas.json``. State shape:

    {
      "schema_version": 1,
      "active_tab": "canvas_2" | null,
      "next_tab_id": 3,
      "tabs": [{"id", "label", "widget_type", "data"}, ...],
    }

Phase 4 multi-tab. ``next_tab_id`` is a monotonic counter so closed-then-
recreated tab IDs never rebind. Phase 3 sidecars (no ``next_tab_id``)
get one synthesized on first read.

Durable artifact identity (#1253, design: PR #1247): a tab is a *view*. For
artifact-backed widget types (``ARTIFACT_WIDGET_TYPES``) the content is stored
once in the sibling ``artifacts.json`` sidecar under an ``artifact_id``, and the
tab carries that id instead of ``data``:

    artifacts.json:
    {
      "schema_version": 1,
      "next_artifact_id": 2,
      "artifacts": {"artifact_1": {"widget_type", "label", "data"}},
    }

Closing a tab hides the view and leaves the artifact; ``reopen_artifact``
creates a fresh tab handle for the same content, and ``update_tab`` on any view
writes the shared artifact (live binding). ``read_canvas_state`` hydrates
``data`` back onto each tab so clients and tools keep their existing shape, and
``write_canvas_state`` strips it again so the two files stay separate.

Mutation functions emit ``canvas_update`` events with one of these
``kind`` values: ``new_tab``, ``update``, ``close_tab``, ``set_active``,
``clear``. Disk I/O is fail-open: corrupt or missing files are treated
as empty canvas state.
"""

import json
import logging
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .conversation_paths import sidecar_path
from .widgets import get_widget_registry

log = logging.getLogger(__name__)

EmitFn = Callable[[str, dict], Awaitable[None]]

# Widget types whose canvas content lives in artifacts.json rather than in the
# tab. Terminals and other widgets keep their inline ``data`` (see #1253 scope).
ARTIFACT_WIDGET_TYPES = frozenset({"markdown_document"})


def empty_canvas_state() -> dict:
    """Return a fresh empty canvas-state dict."""
    return {"schema_version": 1, "active_tab": None, "next_tab_id": 1, "tabs": []}


def empty_artifact_state() -> dict:
    """Return a fresh empty artifact-store dict (``artifacts.json``)."""
    return {"schema_version": 1, "next_artifact_id": 1, "artifacts": {}}


def _canvas_sidecar_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "canvas.json")


def _artifact_sidecar_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "artifacts.json")


def _write_sidecar(path: Path, payload: dict, label: str) -> bool:
    """Write a sidecar via tmp-file-then-rename for atomicity; log on failure."""
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        tmp.replace(path)
        return True
    except OSError:
        log.warning("Failed to write %s", label, exc_info=True)
        # Best-effort cleanup of the partial tmp file.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError as cleanup_exc:
            log.debug("%s tmp cleanup failed: %s", label, cleanup_exc)
        return False


def read_artifact_state(config, conv_id: str) -> dict:
    """Read the artifact store; fail-open with an empty store on any error."""
    path = _artifact_sidecar_path(config, conv_id)
    if not path.exists():
        return empty_artifact_state()
    try:
        state = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to read artifacts for %s; treating as empty", conv_id, exc_info=True)
        return empty_artifact_state()
    if not isinstance(state, dict) or not isinstance(state.get("artifacts"), dict):
        log.warning("Malformed artifacts sidecar for %s; treating as empty", conv_id)
        return empty_artifact_state()
    if "next_artifact_id" not in state:
        state["next_artifact_id"] = _derive_next_artifact_id(state["artifacts"])
    return state


def write_artifact_state(config, conv_id: str, state: dict) -> bool:
    """Persist the artifact store atomically. False on I/O failure (logged)."""
    return _write_sidecar(_artifact_sidecar_path(config, conv_id), state, f"artifacts for {conv_id}")


def _derive_next_artifact_id(artifacts: dict) -> int:
    """Compute next_artifact_id from existing ids (artifact_N format)."""
    max_n = 0
    for artifact_id in artifacts:
        if isinstance(artifact_id, str) and artifact_id.startswith("artifact_"):
            try:
                max_n = max(max_n, int(artifact_id.split("_", 1)[1]))
            except (ValueError, IndexError):
                continue
    return max_n + 1


def _create_artifact(artifacts_state: dict, widget_type: str, label: str, data: dict) -> str:
    """Allocate an artifact id and store its content. Ids are never rebound."""
    next_n = artifacts_state.get("next_artifact_id", 1)
    artifact_id = f"artifact_{next_n}"
    artifacts_state.setdefault("artifacts", {})[artifact_id] = {
        "widget_type": widget_type,
        "label": label,
        "data": data,
    }
    artifacts_state["next_artifact_id"] = next_n + 1
    return artifact_id


def get_artifact(config, conv_id: str, artifact_id: str) -> dict | None:
    """Return an artifact's stored record, or None when this conversation has no such artifact."""
    return read_artifact_state(config, conv_id).get("artifacts", {}).get(artifact_id)


def list_artifacts(config, conv_id: str) -> list[dict]:
    """List this conversation's artifacts as view metadata (no content)."""
    artifacts = read_artifact_state(config, conv_id).get("artifacts", {})
    return [
        {
            "artifact_id": artifact_id,
            "widget_type": record.get("widget_type", ""),
            "label": record.get("label", ""),
        }
        for artifact_id, record in artifacts.items()
    ]


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


def _read_canvas_raw(config, conv_id: str) -> dict:
    """Read canvas.json as stored (artifact-backed tabs carry no ``data``)."""
    path = _canvas_sidecar_path(config, conv_id)
    if not path.exists():
        return empty_canvas_state()
    try:
        state = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to read canvas state for %s; treating as empty", conv_id, exc_info=True)
        return empty_canvas_state()
    # Phase 3 migration: synthesize next_tab_id from existing tabs if missing.
    if "next_tab_id" not in state:
        state["next_tab_id"] = _derive_next_tab_id(state.get("tabs", []))
    return state


def read_canvas_state(config, conv_id: str) -> dict:
    """Read canvas state from disk; fail-open with empty state on any error.

    Artifact-backed tabs are hydrated with their stored ``data`` so callers
    (web client, tools) keep their existing ``{data: {...}}`` view. A missing
    artifact yields empty data rather than another artifact's content.
    """
    state = _read_canvas_raw(config, conv_id)
    tabs = state.get("tabs", [])
    if any(tab.get("artifact_id") for tab in tabs):
        artifacts = read_artifact_state(config, conv_id).get("artifacts", {})
        for tab in tabs:
            artifact_id = tab.get("artifact_id")
            if not artifact_id:
                continue
            record = artifacts.get(artifact_id) or {}
            tab["data"] = dict(record.get("data") or {})
    return state


def _strip_tab_for_disk(tab: dict) -> dict:
    """Drop hydrated ``data`` from artifact-backed tabs; content lives in artifacts.json."""
    if tab.get("artifact_id"):
        return {key: value for key, value in tab.items() if key != "data"}
    return tab


def write_canvas_state(config, conv_id: str, state: dict) -> bool:
    """Write canvas state via tmp-file-then-rename for atomicity.

    This is the single serialization point: artifact-backed tabs are written
    without ``data`` so content cannot fork between canvas.json and
    artifacts.json. Returns True on success, False on I/O failure (logged).
    Callers should propagate the False result so the agent / web UI sees a
    clear error rather than crashing the loop.
    """
    payload = {**state, "tabs": [_strip_tab_for_disk(tab) for tab in state.get("tabs", [])]}
    return _write_sidecar(_canvas_sidecar_path(config, conv_id), payload, f"canvas state for {conv_id}")


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
    if widget_type == "web_frame":
        title = data.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
        url = data.get("url", "")
        if isinstance(url, str) and url.strip():
            try:
                parsed = urllib.parse.urlsplit(url.strip())
                return parsed.netloc or "Web Frame"
            except Exception:
                return "Web Frame"
        return "Web Frame"
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


async def _emit_canvas_update(
    emit: EmitFn | None,
    conv_id: str,
    kind: str,
    *,
    active_tab: str | None,
    tab: dict | None = None,
    closed_tab_id: str | None = None,
) -> None:
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


async def clear_canvas(config, conv_id: str, emit: EmitFn | None = None, registry=None) -> CanvasOpResult:
    """Remove all canvas tabs; hides the panel.

    ``next_tab_id`` is preserved across clears so a closed tab id is never
    rebound — protects standalone tab-locked URLs (`/canvas/{conv}/canvas_2`)
    from silently switching to a different widget after a clear+new_tab cycle.

    If ``registry`` is provided, kills terminal PTYs for any terminal tabs
    before clearing (fail-open per D3).
    """
    state = read_canvas_state(config, conv_id)
    if not state.get("tabs"):
        return CanvasOpResult(ok=True, text="canvas already empty")

    # Kill terminal PTYs before clearing (fail-open per D3)
    if registry is not None:
        for tab in state["tabs"]:
            if tab.get("widget_type") == "terminal":
                session = registry.get(conv_id, tab["id"])
                if session:
                    try:
                        await registry.kill(session)
                        log.debug("Killed terminal PTY for tab %s", tab["id"])
                    except Exception as exc:
                        # D3: fail-open — log but don't block canvas clear
                        log.warning("Failed to kill terminal PTY for %s: %s", tab["id"], exc, exc_info=True)

    next_id = state.get("next_tab_id", 1)
    state = empty_canvas_state()
    state["next_tab_id"] = next_id
    if not write_canvas_state(config, conv_id, state):
        return CanvasOpResult(ok=False, error="failed to write canvas state to disk")
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


async def new_tab(
    config,
    conv_id: str,
    widget_type: str,
    data: dict,
    label: str | None = None,
    emit: EmitFn | None = None,
    enforce_agent_creatable: bool = True,
    enforce_agent_createable: bool | None = None,
) -> CanvasOpResult:
    """Append a new tab and make it active. Returns the new tab_id.

    `enforce_agent_creatable` gates the `agent_creatable` widget-descriptor
    check and defaults to on. Trusted, human-only callers (the `/terminal`
    command handler, the authenticated "Open in Canvas" HTTP endpoint) pass
    `False` — only the agent-facing `canvas_new_tab` tool leaves it enforced.
    """
    err = _validate_widget_for_canvas(widget_type, data)
    if err:
        return CanvasOpResult(ok=False, error=err)
    registry = get_widget_registry()
    if registry is not None:
        enforce = enforce_agent_creatable
        if enforce is True and enforce_agent_createable is not None:
            enforce = enforce_agent_createable
        if enforce:
            descriptor = registry.get(widget_type)
            if descriptor and not getattr(descriptor, "agent_creatable", getattr(descriptor, "agent_createable", True)):
                return CanvasOpResult(ok=False, error=f"widget type '{widget_type}' is not agent_creatable")
        try:
            data = registry.normalize(widget_type, data)
        except ValueError as e:
            return CanvasOpResult(ok=False, error=str(e))
    state = read_canvas_state(config, conv_id)
    next_n = state.get("next_tab_id", 1)
    tab_id = f"canvas_{next_n}"
    final_label = label or _derive_label(widget_type, data)
    artifact_id: str | None = None
    if widget_type in ARTIFACT_WIDGET_TYPES:
        artifacts_state = read_artifact_state(config, conv_id)
        artifact_id = _create_artifact(artifacts_state, widget_type, final_label, data)
        if not write_artifact_state(config, conv_id, artifacts_state):
            return CanvasOpResult(ok=False, error="failed to write artifacts to disk")
    new_tab_dict: dict[str, object] = {
        "id": tab_id,
        "label": final_label,
        "widget_type": widget_type,
    }
    if artifact_id is not None:
        new_tab_dict["artifact_id"] = artifact_id
    else:
        new_tab_dict["data"] = data
    state["tabs"].append(new_tab_dict)
    state["active_tab"] = tab_id
    state["next_tab_id"] = next_n + 1
    if not write_canvas_state(config, conv_id, state):
        return CanvasOpResult(ok=False, error="failed to write canvas state to disk")
    await _emit_canvas_update(emit, conv_id, "new_tab", active_tab=tab_id, tab={**new_tab_dict, "data": data})
    return CanvasOpResult(ok=True, text="tab created", tab_id=tab_id)


async def reopen_artifact(
    config,
    conv_id: str,
    artifact_id: str,
    label: str | None = None,
    emit: EmitFn | None = None,
) -> CanvasOpResult:
    """Open a new tab viewing an existing artifact. Content is not copied.

    The new tab gets a fresh handle, so a tab-locked URL for the closed tab
    still selects nothing. Only this conversation's artifact store is
    searched, so an id from another conversation cannot resolve.
    """
    artifact = get_artifact(config, conv_id, artifact_id)
    if artifact is None:
        return CanvasOpResult(ok=False, error=f"artifact '{artifact_id}' not found")
    state = read_canvas_state(config, conv_id)
    next_n = state.get("next_tab_id", 1)
    tab_id = f"canvas_{next_n}"
    widget_type = artifact.get("widget_type", "")
    final_label = label or artifact.get("label") or _derive_label(widget_type, artifact.get("data") or {})
    new_tab_dict = {
        "id": tab_id,
        "label": final_label,
        "widget_type": widget_type,
        "artifact_id": artifact_id,
    }
    state["tabs"].append(new_tab_dict)
    state["active_tab"] = tab_id
    state["next_tab_id"] = next_n + 1
    if not write_canvas_state(config, conv_id, state):
        return CanvasOpResult(ok=False, error="failed to write canvas state to disk")
    await _emit_canvas_update(
        emit,
        conv_id,
        "new_tab",
        active_tab=tab_id,
        tab={**new_tab_dict, "data": dict(artifact.get("data") or {})},
    )
    return CanvasOpResult(ok=True, text=f"artifact {artifact_id} opened as tab {tab_id}", tab_id=tab_id)


async def update_tab(config, conv_id: str, tab_id: str, data: dict, emit: EmitFn | None = None) -> CanvasOpResult:
    """Replace data of an existing tab; preserves widget_type + label.

    For an artifact-backed tab the content is written to the shared artifact,
    so every tab viewing it shows the new content (live binding), and the tab
    list on disk is left untouched.
    """
    state = read_canvas_state(config, conv_id)
    for tab in state.get("tabs", []):
        if tab.get("id") == tab_id:
            err = _validate_widget_for_canvas(tab["widget_type"], data)
            if err:
                return CanvasOpResult(ok=False, error=err)
            registry = get_widget_registry()
            if registry is not None:
                try:
                    data = registry.normalize(tab["widget_type"], data)
                except ValueError as e:
                    return CanvasOpResult(ok=False, error=str(e))
            artifact_id = tab.get("artifact_id")
            if artifact_id:
                artifacts_state = read_artifact_state(config, conv_id)
                artifact = artifacts_state.get("artifacts", {}).get(artifact_id)
                if artifact is None:
                    return CanvasOpResult(ok=False, error=f"artifact '{artifact_id}' not found")
                artifact["data"] = data
                if not write_artifact_state(config, conv_id, artifacts_state):
                    return CanvasOpResult(ok=False, error="failed to write artifacts to disk")
                await _emit_canvas_update(
                    emit,
                    conv_id,
                    "update",
                    active_tab=state.get("active_tab"),
                    tab={**tab, "data": data},
                )
                return CanvasOpResult(ok=True, text=f"tab {tab_id} updated")
            tab["data"] = data
            if not write_canvas_state(config, conv_id, state):
                return CanvasOpResult(ok=False, error="failed to write canvas state to disk")
            await _emit_canvas_update(emit, conv_id, "update", active_tab=state.get("active_tab"), tab=tab)
            return CanvasOpResult(ok=True, text=f"tab {tab_id} updated")
    return CanvasOpResult(ok=False, error=f"tab '{tab_id}' not found")


async def close_tab(config, conv_id: str, tab_id: str, emit: EmitFn | None = None, registry=None) -> CanvasOpResult:
    """Remove a tab. If active, switch to left neighbor (else right; else None).

    If the tab is a terminal widget and a ``TerminalRegistry`` is supplied,
    kill the underlying PTY session before removing the tab. Kill failures
    are logged, not raised — they must never block tab removal.
    """
    tab = get_tab(config, conv_id, tab_id)
    if tab is not None and tab.get("widget_type") == "terminal" and registry is not None:
        session = registry.get(conv_id, tab_id)
        if session is not None:
            try:
                await registry.kill(session)
            except Exception as exc:
                log.warning("close_tab: failed to kill terminal session conv=%s tab=%s: %s", conv_id, tab_id, exc)
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
        return CanvasOpResult(ok=False, error="failed to write canvas state to disk")
    new_active = state.get("active_tab")
    await _emit_canvas_update(emit, conv_id, "close_tab", active_tab=new_active, tab=None, closed_tab_id=tab_id)
    text = f"tab {tab_id} closed" + (f" (active={new_active})" if new_active else " (canvas hidden — no tabs left)")
    return CanvasOpResult(ok=True, text=text)


async def set_active_tab(config, conv_id: str, tab_id: str, emit: EmitFn | None = None) -> CanvasOpResult:
    """Set the active tab; broadcasts kind='set_active'."""
    state = read_canvas_state(config, conv_id)
    if not any(t.get("id") == tab_id for t in state.get("tabs", [])):
        return CanvasOpResult(ok=False, error=f"tab '{tab_id}' not found")
    state["active_tab"] = tab_id
    if not write_canvas_state(config, conv_id, state):
        return CanvasOpResult(ok=False, error="failed to write canvas state to disk")
    await _emit_canvas_update(emit, conv_id, "set_active", active_tab=tab_id, tab=None)
    return CanvasOpResult(ok=True, text=f"active tab set to {tab_id}")
