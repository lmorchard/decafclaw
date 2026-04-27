# Widgets Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent canvas panel to the web UI plus a `markdown_document` widget that the agent can build and revise across multiple turns.

**Architecture:** Per-conversation `canvas.json` sidecar holds the active widget; new always-loaded canvas tools mutate it; new WebSocket `canvas_update` event drives the in-app panel and a standalone `/canvas/{conv_id}` view. Existing widget infrastructure (registry, validation, host) is reused as-is.

**Tech Stack:** Python (Starlette/Uvicorn), Lit (web components), JSON Schema validation via `jsonschema`, pytest, `marked` for markdown rendering, DOMPurify for sanitization.

**Spec:** [`spec.md`](./spec.md)

---

## File Structure

### Server-side (Python)

| File | Responsibility |
|---|---|
| `src/decafclaw/canvas.py` (new) | Sidecar I/O, internal state operations (`set_canvas` / `update_canvas` / `clear_canvas` / `read_canvas`), schema validation against widget registry, event emission via manager. |
| `src/decafclaw/tools/canvas_tools.py` (new) | Four agent-facing tools: `canvas_set`, `canvas_update`, `canvas_clear`, `canvas_read`. Thin wrappers that call into `canvas.py`. |
| `src/decafclaw/tools/__init__.py` (modify) | Register `CANVAS_TOOLS` and `CANVAS_TOOL_DEFINITIONS`. |
| `src/decafclaw/http_server.py` (modify) | Add `GET /api/canvas/{conv_id}`, `POST /api/canvas/{conv_id}/set`, `GET /canvas/{conv_id}`. |
| `src/decafclaw/web/websocket.py` (modify) | Forward `canvas_update` events from manager to subscribed clients. |

### Frontend (JS / HTML / CSS)

| File | Responsibility |
|---|---|
| `src/decafclaw/web/static/widgets/markdown_document/widget.json` (new) | Widget descriptor (modes: inline, canvas). |
| `src/decafclaw/web/static/widgets/markdown_document/widget.js` (new) | Lit component supporting both inline (collapsed + buttons) and canvas (full + scroll preservation) modes. |
| `src/decafclaw/web/static/lib/canvas-state.js` (new) | Per-conv canvas state management (load via REST, subscribe to WS, dismiss flag). |
| `src/decafclaw/web/static/components/canvas-panel.js` (new) | Lit component: header (label, "open in new tab", close), body (mounts `dc-widget-host`), drag-to-resize handle. |
| `src/decafclaw/web/static/styles/canvas.css` (new) | Panel styles, mobile breakpoints, resize handle. |
| `src/decafclaw/web/static/index.html` (modify) | Add `#chat-main-header` strip, `#canvas-resize-handle`, `#canvas-main`; load canvas component + state module. |
| `src/decafclaw/web/static/app.js` (modify) | Wire canvas-state into select-conv flow; resize handle drag handler; mobile mutual-exclusion with wiki. |
| `src/decafclaw/web/static/canvas-page.html` (new) | Standalone canvas view HTML. |
| `src/decafclaw/web/static/canvas-page.js` (new) | Page controller for standalone view (REST + WS). |

### Tests

| File | Responsibility |
|---|---|
| `tests/test_canvas.py` (new) | Sidecar I/O, validation, event emission. |
| `tests/test_canvas_tools.py` (new) | Tool happy paths and all error branches. |
| `tests/test_web_canvas.py` (new) | REST endpoints (auth, response shape) + WS event projection. |
| `tests/test_widgets.py` (modify) | Add `markdown_document` to expected widgets. |

### Docs

| File | Responsibility |
|---|---|
| `docs/widgets.md` (modify) | Drop "canvas out-of-scope" note; document canvas mode + markdown_document. |
| `docs/web-ui.md` (modify) | Add canvas panel section. |
| `docs/web-ui-mobile.md` (modify) | Add canvas overlay + mutual exclusion row. |
| `docs/conversations.md` (modify) | Note `{conv_id}.canvas.json` sidecar. |
| `docs/context-composer.md` (modify) | Add canvas tools to always-loaded list. |
| `CLAUDE.md` (modify) | Add `canvas.py` and `tools/canvas_tools.py` to key files. |
| `README.md` (modify) | Reflect canvas in feature list (concise). |

---

## Task 1: Canvas persistence + internal state module

**Files:**
- Create: `src/decafclaw/canvas.py`
- Test: `tests/test_canvas.py`

This task introduces the sidecar I/O and the internal state-mutation functions used by both the agent tools and REST endpoints. Event emission goes through `ConversationManager.emit` (passed in by callers) — the canvas module itself stays manager-agnostic by accepting an optional async `emit` callable.

- [ ] **Step 1: Write the failing tests for sidecar path + read/write round-trip**

Create `tests/test_canvas.py`:

```python
"""Tests for canvas.py — per-conversation canvas state sidecar."""

import json
import pytest

from decafclaw import canvas
from decafclaw.config import Config


@pytest.fixture
def config(tmp_path):
    cfg = Config()
    cfg.workspace_path = tmp_path / "workspace"
    cfg.workspace_path.mkdir()
    return cfg


def test_canvas_sidecar_path_basic(config):
    path = canvas._canvas_sidecar_path(config, "abc123")
    expected = config.workspace_path / "conversations" / "abc123.canvas.json"
    assert path == expected.resolve()


def test_canvas_sidecar_path_traversal_guard(config):
    bad = canvas._canvas_sidecar_path(config, "../etc/passwd")
    assert bad.name == "_invalid.canvas.json"


def test_canvas_sidecar_path_empty(config):
    bad = canvas._canvas_sidecar_path(config, "")
    assert bad.name == "_invalid.canvas.json"


def test_read_canvas_state_missing_file(config):
    state = canvas.read_canvas_state(config, "nope")
    assert state == canvas.empty_canvas_state()


def test_read_canvas_state_corrupt_file(config):
    path = canvas._canvas_sidecar_path(config, "corrupt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json")
    state = canvas.read_canvas_state(config, "corrupt")
    assert state == canvas.empty_canvas_state()


def test_write_then_read_round_trip(config):
    state = {
        "schema_version": 1,
        "active_tab": "canvas_1",
        "tabs": [{
            "id": "canvas_1",
            "label": "Hello",
            "widget_type": "markdown_document",
            "data": {"content": "# Hi"},
        }],
    }
    canvas.write_canvas_state(config, "conv1", state)
    assert canvas.read_canvas_state(config, "conv1") == state


def test_write_is_atomic(config):
    """Write goes through tmp file + rename — no leftover .tmp file."""
    state = {"schema_version": 1, "active_tab": None, "tabs": []}
    canvas.write_canvas_state(config, "conv2", state)
    path = canvas._canvas_sidecar_path(config, "conv2")
    assert not path.with_suffix(".json.tmp").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_canvas.py -v`
Expected: All fail with `ModuleNotFoundError: No module named 'decafclaw.canvas'`.

- [ ] **Step 3: Write minimal persistence implementation**

Create `src/decafclaw/canvas.py`:

```python
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
    safe_name = conv_id.replace("/", "").replace("\\", "").replace("..", "")
    if not safe_name:
        return base_dir / "_invalid.canvas.json"
    path = (base_dir / f"{safe_name}.canvas.json").resolve()
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


def write_canvas_state(config, conv_id: str, state: dict) -> None:
    """Write canvas state via tmp-file-then-rename for atomicity."""
    path = _canvas_sidecar_path(config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_canvas.py -v`
Expected: All 7 tests pass.

- [ ] **Step 5: Add tests for state operations (set / update / clear / read / get_active_tab)**

Append to `tests/test_canvas.py`:

```python
class _FakeRegistry:
    """Stand-in for WidgetRegistry used by canvas validation in tests."""

    def __init__(self, descriptors):
        self._descriptors = descriptors

    def get(self, name):
        return self._descriptors.get(name)

    def validate(self, name, data):
        desc = self._descriptors.get(name)
        if desc is None:
            return False, f"unknown widget '{name}'"
        for r in desc.get("required", []):
            if r not in data:
                return False, f"missing required field '{r}'"
        return True, None


@pytest.fixture
def md_doc_registry(monkeypatch):
    reg = _FakeRegistry({
        "markdown_document": {"modes": ["inline", "canvas"], "required": ["content"]},
        "data_table": {"modes": ["inline"], "required": []},
    })
    monkeypatch.setattr(canvas, "get_widget_registry", lambda: reg)
    return reg


@pytest.fixture
def emit_recorder():
    events = []

    async def emit(conv_id, event):
        events.append((conv_id, event))

    emit.events = events
    return emit


@pytest.mark.asyncio
async def test_set_canvas_creates_tab_and_emits(config, md_doc_registry, emit_recorder):
    result = await canvas.set_canvas(
        config, "conv1", "markdown_document",
        {"content": "# Hello"}, label="Doc",
        emit=emit_recorder,
    )
    assert result.ok
    state = canvas.read_canvas_state(config, "conv1")
    assert state["active_tab"] == "canvas_1"
    assert state["tabs"][0]["widget_type"] == "markdown_document"
    assert state["tabs"][0]["label"] == "Doc"
    assert len(emit_recorder.events) == 1
    conv_id, event = emit_recorder.events[0]
    assert conv_id == "conv1"
    assert event["type"] == "canvas_update"
    assert event["kind"] == "set"
    assert event["tab"]["data"] == {"content": "# Hello"}


@pytest.mark.asyncio
async def test_set_canvas_replaces_existing_tab(config, md_doc_registry, emit_recorder):
    await canvas.set_canvas(config, "c", "markdown_document",
                            {"content": "first"}, emit=emit_recorder)
    await canvas.set_canvas(config, "c", "markdown_document",
                            {"content": "second"}, emit=emit_recorder)
    state = canvas.read_canvas_state(config, "c")
    assert len(state["tabs"]) == 1
    assert state["tabs"][0]["data"]["content"] == "second"


@pytest.mark.asyncio
async def test_set_canvas_unknown_widget(config, md_doc_registry, emit_recorder):
    result = await canvas.set_canvas(
        config, "c", "no_such_widget", {"x": 1}, emit=emit_recorder,
    )
    assert not result.ok
    assert "not registered" in result.error
    assert canvas.read_canvas_state(config, "c") == canvas.empty_canvas_state()
    assert emit_recorder.events == []


@pytest.mark.asyncio
async def test_set_canvas_widget_without_canvas_mode(config, md_doc_registry, emit_recorder):
    result = await canvas.set_canvas(
        config, "c", "data_table", {}, emit=emit_recorder,
    )
    assert not result.ok
    assert "does not support canvas mode" in result.error


@pytest.mark.asyncio
async def test_set_canvas_invalid_data(config, md_doc_registry, emit_recorder):
    result = await canvas.set_canvas(
        config, "c", "markdown_document", {"wrong_field": "x"},
        emit=emit_recorder,
    )
    assert not result.ok
    assert "schema validation failed" in result.error


@pytest.mark.asyncio
async def test_set_canvas_default_label_from_h1(config, md_doc_registry, emit_recorder):
    await canvas.set_canvas(
        config, "c", "markdown_document",
        {"content": "# Project Summary\n\nSome text"},
        emit=emit_recorder,
    )
    state = canvas.read_canvas_state(config, "c")
    assert state["tabs"][0]["label"] == "Project Summary"


@pytest.mark.asyncio
async def test_set_canvas_default_label_fallback(config, md_doc_registry, emit_recorder):
    await canvas.set_canvas(
        config, "c", "markdown_document",
        {"content": "no heading here"},
        emit=emit_recorder,
    )
    state = canvas.read_canvas_state(config, "c")
    assert state["tabs"][0]["label"] == "Untitled"


@pytest.mark.asyncio
async def test_update_canvas_with_no_tab_fails(config, md_doc_registry, emit_recorder):
    result = await canvas.update_canvas(
        config, "c", {"content": "x"}, emit=emit_recorder,
    )
    assert not result.ok
    assert "no canvas widget set" in result.error
    assert emit_recorder.events == []


@pytest.mark.asyncio
async def test_update_canvas_preserves_label_and_widget_type(
    config, md_doc_registry, emit_recorder
):
    await canvas.set_canvas(config, "c", "markdown_document",
                            {"content": "v1"}, label="Doc",
                            emit=emit_recorder)
    emit_recorder.events.clear()
    result = await canvas.update_canvas(
        config, "c", {"content": "v2"}, emit=emit_recorder,
    )
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    assert state["tabs"][0]["label"] == "Doc"
    assert state["tabs"][0]["widget_type"] == "markdown_document"
    assert state["tabs"][0]["data"]["content"] == "v2"
    _, event = emit_recorder.events[0]
    assert event["kind"] == "update"


@pytest.mark.asyncio
async def test_update_canvas_invalid_data(config, md_doc_registry, emit_recorder):
    await canvas.set_canvas(config, "c", "markdown_document",
                            {"content": "v1"}, emit=emit_recorder)
    result = await canvas.update_canvas(
        config, "c", {"oops": True}, emit=emit_recorder,
    )
    assert not result.ok
    assert "schema validation failed" in result.error


@pytest.mark.asyncio
async def test_clear_canvas_when_empty(config, md_doc_registry, emit_recorder):
    result = await canvas.clear_canvas(config, "c", emit=emit_recorder)
    assert result.ok
    assert result.text == "canvas already empty"
    assert emit_recorder.events == []


@pytest.mark.asyncio
async def test_clear_canvas_with_tab(config, md_doc_registry, emit_recorder):
    await canvas.set_canvas(config, "c", "markdown_document",
                            {"content": "v1"}, emit=emit_recorder)
    emit_recorder.events.clear()
    result = await canvas.clear_canvas(config, "c", emit=emit_recorder)
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    assert state == canvas.empty_canvas_state()
    _, event = emit_recorder.events[0]
    assert event["kind"] == "clear"
    assert event["tab"] is None


def test_get_active_tab_empty(config):
    assert canvas.get_active_tab(config, "c") is None


@pytest.mark.asyncio
async def test_get_active_tab_present(config, md_doc_registry, emit_recorder):
    await canvas.set_canvas(
        config, "c", "markdown_document",
        {"content": "x"}, emit=emit_recorder,
    )
    tab = canvas.get_active_tab(config, "c")
    assert tab is not None
    assert tab["widget_type"] == "markdown_document"
```

- [ ] **Step 6: Run new tests, verify they fail**

Run: `uv run pytest tests/test_canvas.py -v`
Expected: New tests fail with attribute / function-not-defined errors.

- [ ] **Step 7: Implement state operations**

Append to `src/decafclaw/canvas.py`:

```python
@dataclass
class CanvasOpResult:
    """Outcome of a canvas state operation."""
    ok: bool
    text: str = ""
    error: str = ""


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


def _validate_widget_for_canvas(widget_type: str, data: dict) -> str | None:
    """Return an error message string, or None if validation passes."""
    registry = get_widget_registry()
    if registry is None:
        return "widget registry not initialized"
    descriptor = registry.get(widget_type)
    if descriptor is None:
        return f"widget '{widget_type}' not registered"
    modes = getattr(descriptor, "modes", None)
    if modes is None and isinstance(descriptor, dict):
        modes = descriptor.get("modes", [])
    if "canvas" not in (modes or []):
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
    write_canvas_state(config, conv_id, state)
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
    write_canvas_state(config, conv_id, state)
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
    write_canvas_state(config, conv_id, state)
    await _emit_canvas_update(emit, conv_id, "clear", state)
    return CanvasOpResult(ok=True, text="canvas cleared")
```

- [ ] **Step 8: Run all canvas tests, verify pass**

Run: `uv run pytest tests/test_canvas.py -v`
Expected: All ~22 tests pass.

- [ ] **Step 9: Lint**

Run: `uv run ruff check src/decafclaw/canvas.py tests/test_canvas.py`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add src/decafclaw/canvas.py tests/test_canvas.py
git commit -m "$(cat <<'EOF'
feat(canvas): persistence + state operations module

Sidecar I/O at workspace/conversations/{conv_id}.canvas.json with
fail-open reads and atomic tmp+rename writes. set/update/clear
operations validate against the widget registry and emit canvas_update
events via an injected emit callable (manager.emit at call sites).

Phase 3 of #256 (#388).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Wire `canvas_update` event into WebSocket transport

**Files:**
- Modify: `src/decafclaw/web/websocket.py` (add `canvas_update` branch in `on_conv_event`, plus a small extracted helper for testing)
- Test: `tests/test_web_canvas.py`

The manager-emitted event already routes to per-conv subscribers (existing infra). We add a small forwarding branch in the WebSocket handler so the event reaches the client.

- [ ] **Step 1: Write failing test for the WS forwarding behavior**

Create `tests/test_web_canvas.py`:

```python
"""Tests for canvas REST endpoints and WebSocket event projection."""

import pytest

from decafclaw.web import websocket as ws_mod


@pytest.mark.asyncio
async def test_canvas_update_event_projected_to_client():
    """The on_conv_event callback forwards canvas_update events to the WS."""
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_canvas_update_forwarder(state, conv_id="conv-x")

    await callback({
        "type": "canvas_update",
        "conv_id": "conv-x",
        "kind": "set",
        "active_tab": "canvas_1",
        "tab": {"id": "canvas_1", "label": "L",
                "widget_type": "markdown_document",
                "data": {"content": "x"}},
    })

    assert sent == [{
        "type": "canvas_update",
        "conv_id": "conv-x",
        "kind": "set",
        "active_tab": "canvas_1",
        "tab": {"id": "canvas_1", "label": "L",
                "widget_type": "markdown_document",
                "data": {"content": "x"}},
    }]


@pytest.mark.asyncio
async def test_canvas_update_event_skipped_for_other_conv():
    """A canvas_update for a different conv_id is ignored by this socket."""
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_canvas_update_forwarder(state, conv_id="conv-x")
    await callback({"type": "canvas_update", "conv_id": "OTHER",
                    "kind": "set", "active_tab": None, "tab": None})
    assert sent == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web_canvas.py -v`
Expected: `AttributeError: module 'decafclaw.web.websocket' has no attribute '_make_canvas_update_forwarder'`.

- [ ] **Step 3: Add the forwarder helper and the canvas_update branch**

In `src/decafclaw/web/websocket.py`, add this helper near the other module-level helpers (after `_project_tool_end`, around line 50):

```python
def _make_canvas_update_forwarder(state, conv_id):
    """Build a coroutine that forwards canvas_update events to ws_send.

    Used in unit tests; production code uses the inline branch in
    on_conv_event for performance.
    """
    ws_send = state["ws_send"]

    async def _forward(event):
        if event.get("type") != "canvas_update":
            return
        if event.get("conv_id") != conv_id:
            return
        await ws_send({
            "type": "canvas_update",
            "conv_id": conv_id,
            "kind": event.get("kind", "set"),
            "active_tab": event.get("active_tab"),
            "tab": event.get("tab"),
        })

    return _forward
```

Then, inside `on_conv_event` in `_subscribe_to_conv` (after the existing `tool_end` branch, around line 521), add a new branch:

```python
        elif event_type == "canvas_update":
            if event_conv_id == conv_id:
                await ws_send({
                    "type": "canvas_update",
                    "conv_id": event_conv_id,
                    "kind": event.get("kind", "set"),
                    "active_tab": event.get("active_tab"),
                    "tab": event.get("tab"),
                })
```

- [ ] **Step 4: Run test, verify pass**

Run: `uv run pytest tests/test_web_canvas.py -v`
Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/web/websocket.py tests/test_web_canvas.py
git commit -m "$(cat <<'EOF'
feat(web): forward canvas_update events to WebSocket clients

Adds a branch in on_conv_event for canvas_update plus a tiny extracted
helper for testability. Subscribers receive {type, conv_id, kind,
active_tab, tab}.

Phase 3 of #256 (#388).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Canvas tools — `canvas_set`, `canvas_update`, `canvas_clear`, `canvas_read`

**Files:**
- Create: `src/decafclaw/tools/canvas_tools.py`
- Modify: `src/decafclaw/tools/__init__.py`
- Test: `tests/test_canvas_tools.py`

Each tool is a thin wrapper that builds a manager-bound `emit` from `ctx`, calls into `canvas.py`, and projects `CanvasOpResult` into a `ToolResult`.

- [ ] **Step 1: Write failing tests for the four tools**

Create `tests/test_canvas_tools.py`:

```python
"""Tests for canvas_* agent tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from decafclaw.tools import canvas_tools
from decafclaw.media import ToolResult


def _make_ctx(config, manager=None, conv_id="conv1"):
    ctx = MagicMock()
    ctx.config = config
    ctx.conv_id = conv_id
    ctx.manager = manager
    return ctx


@pytest.fixture
def config(tmp_path):
    from decafclaw.config import Config
    cfg = Config()
    cfg.workspace_path = tmp_path / "workspace"
    cfg.workspace_path.mkdir()
    return cfg


@pytest.fixture
def md_doc_registry(monkeypatch):
    from decafclaw import canvas as canvas_mod

    class _Reg:
        _d = {"markdown_document": {"modes": ["inline", "canvas"], "required": ["content"]}}

        def get(self, name): return self._d.get(name)

        def validate(self, name, data):
            d = self._d.get(name)
            if not d:
                return False, "unknown"
            for r in d.get("required", []):
                if r not in data:
                    return False, f"missing {r}"
            return True, None

    monkeypatch.setattr(canvas_mod, "get_widget_registry", lambda: _Reg())


@pytest.mark.asyncio
async def test_canvas_set_happy_path(config, md_doc_registry):
    manager = MagicMock()
    manager.emit = AsyncMock()
    ctx = _make_ctx(config, manager)
    result = await canvas_tools.tool_canvas_set(
        ctx, "markdown_document", {"content": "# Hi"},
    )
    assert isinstance(result, ToolResult)
    assert "canvas updated" in result.text
    assert "/canvas/conv1" in result.text
    manager.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_canvas_set_unknown_widget(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_set(
        ctx, "no_such", {"content": "x"},
    )
    assert result.text.startswith("[error: ")
    assert "not registered" in result.text


@pytest.mark.asyncio
async def test_canvas_update_no_prior_set(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_update(ctx, {"content": "x"})
    assert result.text.startswith("[error: ")
    assert "no canvas widget set" in result.text


@pytest.mark.asyncio
async def test_canvas_update_after_set(config, md_doc_registry):
    manager = MagicMock(emit=AsyncMock())
    ctx = _make_ctx(config, manager)
    await canvas_tools.tool_canvas_set(ctx, "markdown_document", {"content": "v1"})
    result = await canvas_tools.tool_canvas_update(ctx, {"content": "v2"})
    assert result.text == "canvas updated"


@pytest.mark.asyncio
async def test_canvas_clear_when_empty(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_clear(ctx)
    assert result.text == "canvas already empty"


@pytest.mark.asyncio
async def test_canvas_clear_with_tab(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    await canvas_tools.tool_canvas_set(ctx, "markdown_document", {"content": "x"})
    result = await canvas_tools.tool_canvas_clear(ctx)
    assert result.text == "canvas cleared"


@pytest.mark.asyncio
async def test_canvas_read_empty(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_read(ctx)
    assert result.data is None
    assert "empty" in result.text.lower()


@pytest.mark.asyncio
async def test_canvas_read_populated(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    await canvas_tools.tool_canvas_set(ctx, "markdown_document",
                                       {"content": "x"}, label="Lbl")
    result = await canvas_tools.tool_canvas_read(ctx)
    assert result.data is not None
    assert result.data["widget_type"] == "markdown_document"
    assert result.data["label"] == "Lbl"
    assert result.data["data"] == {"content": "x"}


def test_tools_registered_as_always_loaded():
    """Canvas tools appear in the always-loaded registry."""
    from decafclaw.tools import TOOLS, TOOL_DEFINITIONS
    for name in ("canvas_set", "canvas_update", "canvas_clear", "canvas_read"):
        assert name in TOOLS, f"{name} missing from TOOLS"
    names = {d["function"]["name"] for d in TOOL_DEFINITIONS
             if d.get("type") == "function"}
    for name in ("canvas_set", "canvas_update", "canvas_clear", "canvas_read"):
        assert name in names, f"{name} missing from TOOL_DEFINITIONS"
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `uv run pytest tests/test_canvas_tools.py -v`
Expected: All fail with import error.

- [ ] **Step 3: Implement `canvas_tools.py`**

Create `src/decafclaw/tools/canvas_tools.py`:

```python
"""Agent-facing canvas tools — push, replace, clear, and read the canvas surface.

The canvas is a per-conversation, web-only display area where the agent
maintains a living widget across multiple turns. These four tools wrap
the internal state operations in :mod:`decafclaw.canvas` and project
results into ``ToolResult`` objects suitable for the agent loop.

All four tools are always-loaded (small definitions, low cost) and run
under the standard 180s tool timeout.
"""

import logging

from .. import canvas as canvas_mod
from ..media import ToolResult

log = logging.getLogger(__name__)


def _emit_for_ctx(ctx):
    """Build an emit callable from the conversation manager on ctx.

    Returns ``None`` when there's no manager (unit tests, terminal).
    canvas.py treats ``None`` as fail-open.
    """
    manager = getattr(ctx, "manager", None)
    if manager is None:
        return None
    return manager.emit  # async (conv_id, event)


def _canvas_url(conv_id: str) -> str:
    return f"/canvas/{conv_id}"


async def tool_canvas_set(ctx,
                          widget_type: str,
                          data: dict,
                          label: str | None = None) -> ToolResult:
    """Push a widget onto the canvas, replacing any existing tab."""
    log.info("[tool:canvas_set] widget=%s label=%r", widget_type, label)
    result = await canvas_mod.set_canvas(
        ctx.config, ctx.conv_id, widget_type, data,
        label=label, emit=_emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=f"{result.text} — view at {_canvas_url(ctx.conv_id)}")


async def tool_canvas_update(ctx, data: dict) -> ToolResult:
    """Replace the data of the current canvas widget. Errors if none set."""
    log.info("[tool:canvas_update]")
    result = await canvas_mod.update_canvas(
        ctx.config, ctx.conv_id, data, emit=_emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_canvas_clear(ctx) -> ToolResult:
    """Remove the canvas widget; hides the panel for all watchers."""
    log.info("[tool:canvas_clear]")
    result = await canvas_mod.clear_canvas(
        ctx.config, ctx.conv_id, emit=_emit_for_ctx(ctx),
    )
    return ToolResult(text=result.text)


async def tool_canvas_read(ctx) -> ToolResult:
    """Return the current canvas tab as structured data, or null if empty."""
    log.info("[tool:canvas_read]")
    tab = canvas_mod.get_active_tab(ctx.config, ctx.conv_id)
    if tab is None:
        return ToolResult(text="canvas is empty (no widget set)", data=None)
    payload = {
        "widget_type": tab["widget_type"],
        "label": tab.get("label", ""),
        "data": tab.get("data", {}),
    }
    return ToolResult(
        text=f"current canvas: {payload['widget_type']} ({payload['label']})",
        data=payload,
    )


CANVAS_TOOLS = {
    "canvas_set": tool_canvas_set,
    "canvas_update": tool_canvas_update,
    "canvas_clear": tool_canvas_clear,
    "canvas_read": tool_canvas_read,
}


CANVAS_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_set",
            "description": (
                "Push a widget onto the conversation's canvas, replacing any "
                "existing widget. The canvas is a persistent display surface "
                "in the user's web UI — use it for documents, plans, or "
                "visualizations you intend to revise across multiple turns. "
                "Always reveals the panel to the user. Currently supports "
                "widget_type='markdown_document' with data={content: <markdown>}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "widget_type": {
                        "type": "string",
                        "description": "Registered canvas-mode widget name.",
                    },
                    "data": {
                        "type": "object",
                        "description": "Widget payload; must conform to the widget's data_schema.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Optional tab label. Defaults to first H1 of content for markdown_document.",
                    },
                },
                "required": ["widget_type", "data"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_update",
            "description": (
                "Replace the data of the existing canvas widget. Same "
                "widget_type, same label. Use for revising the current "
                "document — preserves scroll position and does NOT pop the "
                "panel back open if the user has dismissed it. Errors if no "
                "canvas_set has happened yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "object",
                        "description": "New data payload; must match the current widget's data_schema.",
                    },
                },
                "required": ["data"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_clear",
            "description": (
                "Remove the canvas widget and hide the panel for all "
                "watchers. No-op if the canvas is already empty."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_read",
            "description": (
                "Return the current canvas widget as {widget_type, label, "
                "data}, or null if empty. Use to ground revisions in the "
                "current canvas state — especially after compaction or after "
                "the user clicks 'Open in Canvas' on an inline widget."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
```

- [ ] **Step 4: Register the tools**

Edit `src/decafclaw/tools/__init__.py`:

Add the import alongside the other tool imports (alphabetical):

```python
from .canvas_tools import CANVAS_TOOL_DEFINITIONS, CANVAS_TOOLS
```

Add `**CANVAS_TOOLS` to the `TOOLS = {...}` dict and `+ CANVAS_TOOL_DEFINITIONS` to `TOOL_DEFINITIONS`:

```python
TOOLS = {**CORE_TOOLS, **CHECKLIST_TOOLS,
         **CONVERSATION_TOOLS, **WORKSPACE_TOOLS, **SHELL_TOOLS,
         **HTTP_TOOLS,
         **SKILL_TOOLS,
         **HEARTBEAT_TOOLS, **HEALTH_TOOLS,
         **DELEGATE_TOOLS, **ATTACHMENT_TOOLS, **EMAIL_TOOLS,
         **NOTIFICATION_TOOLS, **CANVAS_TOOLS}
TOOL_DEFINITIONS = (CORE_TOOL_DEFINITIONS
                    + CHECKLIST_TOOL_DEFINITIONS
                    + CONVERSATION_TOOL_DEFINITIONS + WORKSPACE_TOOL_DEFINITIONS
                    + SHELL_TOOL_DEFINITIONS
                    + HTTP_TOOL_DEFINITIONS + SKILL_TOOL_DEFINITIONS
                    + HEARTBEAT_TOOL_DEFINITIONS
                    + HEALTH_TOOL_DEFINITIONS
                    + DELEGATE_TOOL_DEFINITIONS + ATTACHMENT_TOOL_DEFINITIONS
                    + EMAIL_TOOL_DEFINITIONS
                    + NOTIFICATION_TOOL_DEFINITIONS
                    + CANVAS_TOOL_DEFINITIONS)
```

- [ ] **Step 5: Run all tool tests, verify pass**

Run: `uv run pytest tests/test_canvas_tools.py -v`
Expected: 9 tests pass.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/ -x -q`
Expected: all pass.

- [ ] **Step 7: Lint**

Run: `uv run ruff check src/decafclaw/tools/canvas_tools.py tests/test_canvas_tools.py`

- [ ] **Step 8: Commit**

```bash
git add src/decafclaw/tools/canvas_tools.py src/decafclaw/tools/__init__.py tests/test_canvas_tools.py
git commit -m "$(cat <<'EOF'
feat(canvas): always-loaded canvas tools (set/update/clear/read)

Four agent-facing tools wrapping canvas.py state operations. Tool
descriptions emphasize lifecycle: set creates/replaces+reveals, update
mutates silently if dismissed, clear hides, read grounds.

Phase 3 of #256 (#388).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: REST endpoints — load state, "Open in Canvas" button, standalone view stub

**Files:**
- Modify: `src/decafclaw/http_server.py` (add three routes + helper)
- Create: `src/decafclaw/web/static/canvas-page.html` (stub)
- Test: `tests/test_web_canvas.py` (extend)

The frontend needs three HTTP-level affordances: load current state on conv-select, push to canvas from an inline widget button, and serve the standalone canvas page.

- [ ] **Step 1: Append failing tests for REST endpoints**

Append to `tests/test_web_canvas.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from starlette.testclient import TestClient


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    """Build a Starlette app with a known auth token + stubbed widget registry."""
    from decafclaw.config import Config
    from decafclaw.http_server import create_app

    cfg = Config()
    cfg.workspace_path = tmp_path / "workspace"
    cfg.workspace_path.mkdir()
    cfg.web_tokens = {"test-token": "tester"}

    from decafclaw import canvas as canvas_mod

    class _Reg:
        _d = {"markdown_document": {"modes": ["inline", "canvas"], "required": ["content"]}}

        def get(self, name): return self._d.get(name)

        def validate(self, name, data):
            d = self._d.get(name)
            if not d:
                return False, "unknown"
            for r in d.get("required", []):
                if r not in data:
                    return False, f"missing {r}"
            return True, None

    monkeypatch.setattr(canvas_mod, "get_widget_registry", lambda: _Reg())

    bus = MagicMock()
    manager = MagicMock()
    manager.emit = AsyncMock()
    app = create_app(cfg, bus, app_ctx=None, manager=manager)
    return app, cfg, manager


def _client(app):
    return TestClient(app)


def _auth_cookie():
    return {"dfc_session": "test-token"}


def test_get_canvas_state_empty(app_factory):
    app, cfg, manager = app_factory
    client = _client(app)
    resp = client.get("/api/canvas/conv1", cookies=_auth_cookie())
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == 1
    assert body["active_tab"] is None
    assert body["tabs"] == []


def test_get_canvas_state_requires_auth(app_factory):
    app, _, _ = app_factory
    client = _client(app)
    resp = client.get("/api/canvas/conv1")
    assert resp.status_code in (401, 302, 403)


def test_post_canvas_set_writes_state_and_emits(app_factory):
    app, cfg, manager = app_factory
    client = _client(app)
    resp = client.post(
        "/api/canvas/conv1/set",
        json={"widget_type": "markdown_document",
              "data": {"content": "# Doc\n\nbody"}},
        cookies=_auth_cookie(),
    )
    assert resp.status_code == 200
    follow = client.get("/api/canvas/conv1", cookies=_auth_cookie())
    assert follow.status_code == 200
    state = follow.json()
    assert state["active_tab"] == "canvas_1"
    assert state["tabs"][0]["data"] == {"content": "# Doc\n\nbody"}
    assert manager.emit.await_count == 1
    args = manager.emit.await_args
    assert args.args[0] == "conv1"
    assert args.args[1]["type"] == "canvas_update"


def test_post_canvas_set_rejects_unknown_widget(app_factory):
    app, _, _ = app_factory
    client = _client(app)
    resp = client.post(
        "/api/canvas/conv1/set",
        json={"widget_type": "no_such", "data": {}},
        cookies=_auth_cookie(),
    )
    assert resp.status_code == 400
    assert "not registered" in resp.json().get("error", "")


def test_get_standalone_canvas_html(app_factory):
    app, _, _ = app_factory
    client = _client(app)
    resp = client.get("/canvas/conv1", cookies=_auth_cookie())
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "<dc-widget-host>" in resp.text
```

- [ ] **Step 2: Run, confirm 404s on the endpoints**

Run: `uv run pytest tests/test_web_canvas.py -v`
Expected: 5 new tests fail (404).

- [ ] **Step 3: Implement endpoints in `http_server.py`**

In `src/decafclaw/http_server.py`, near the top of the module, add the regex helper:

```python
import re

_SAFE_CONV_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _is_safe_conv_id(conv_id: str) -> bool:
    return bool(conv_id and _SAFE_CONV_ID_RE.match(conv_id))
```

Then inside `create_app`, alongside the existing `@_authenticated` handlers (e.g., near `get_context_diagnostics` around line 435), add:

```python
    @_authenticated
    async def get_canvas_state(request: Request, username: str) -> JSONResponse:
        """Load current canvas state for a conversation."""
        from . import canvas as canvas_mod
        conv_id = request.path_params.get("conv_id", "")
        if not _is_safe_conv_id(conv_id):
            return JSONResponse({"error": "invalid conv_id"}, status_code=400)
        state = canvas_mod.read_canvas_state(config, conv_id)
        return JSONResponse(state)

    @_authenticated
    async def post_canvas_set(request: Request, username: str) -> JSONResponse:
        """Push a widget to the canvas (used by 'Open in Canvas' button)."""
        from . import canvas as canvas_mod
        conv_id = request.path_params.get("conv_id", "")
        if not _is_safe_conv_id(conv_id):
            return JSONResponse({"error": "invalid conv_id"}, status_code=400)
        body = await request.json()
        widget_type = body.get("widget_type", "")
        data = body.get("data") or {}
        label = body.get("label")
        emit = manager.emit if manager else None
        result = await canvas_mod.set_canvas(
            config, conv_id, widget_type, data, label=label, emit=emit,
        )
        if not result.ok:
            return JSONResponse({"error": result.error}, status_code=400)
        return JSONResponse({"ok": True, "text": result.text})

    @_authenticated
    async def get_canvas_page(request: Request, username: str):
        """Serve the standalone canvas HTML page."""
        from pathlib import Path
        from starlette.responses import Response
        conv_id = request.path_params.get("conv_id", "")
        if not _is_safe_conv_id(conv_id):
            return Response("Invalid conversation id", status_code=400)
        html_path = Path(__file__).parent / "web" / "static" / "canvas-page.html"
        return Response(html_path.read_text(), media_type="text/html")
```

Find the `Route(...)` list inside `create_app` (search for `Route("/api/conversations"`) and add alongside the other routes:

```python
        Route("/api/canvas/{conv_id}", get_canvas_state, methods=["GET"]),
        Route("/api/canvas/{conv_id}/set", post_canvas_set, methods=["POST"]),
        Route("/canvas/{conv_id}", get_canvas_page, methods=["GET"]),
```

- [ ] **Step 4: Create canvas-page.html stub**

Create `src/decafclaw/web/static/canvas-page.html`:

```html
<!DOCTYPE html>
<html>
<head><title>Canvas</title></head>
<body>
  <dc-widget-host></dc-widget-host>
  <script>/* placeholder — replaced in Task 10 */</script>
</body>
</html>
```

- [ ] **Step 5: Run all canvas REST tests**

Run: `uv run pytest tests/test_web_canvas.py -v`
Expected: all pass.

- [ ] **Step 6: Run full suite**

Run: `uv run pytest tests/ -x -q`

- [ ] **Step 7: Commit**

```bash
git add src/decafclaw/http_server.py src/decafclaw/web/static/canvas-page.html tests/test_web_canvas.py
git commit -m "$(cat <<'EOF'
feat(web): canvas REST endpoints + standalone HTML stub

GET /api/canvas/{conv_id} loads state; POST /api/canvas/{conv_id}/set
backs the 'Open in Canvas' inline-widget button and emits the same
canvas_update event as the agent tools; GET /canvas/{conv_id} serves
a placeholder standalone HTML page (filled out in Task 10).

Phase 3 of #256 (#388).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `markdown_document` widget

**Files:**
- Create: `src/decafclaw/web/static/widgets/markdown_document/widget.json`
- Create: `src/decafclaw/web/static/widgets/markdown_document/widget.js`
- Modify: `tests/test_widgets.py`

Two modes (`inline`, `canvas`). Inline is collapsed-by-default with two action buttons; canvas is the full document with scroll preservation across data updates.

- [ ] **Step 1: Add the descriptor**

Create `src/decafclaw/web/static/widgets/markdown_document/widget.json`:

```json
{
  "name": "markdown_document",
  "description": "A persistent markdown document the agent can build and revise across turns. Inline mode shows a collapsed preview; canvas mode is the primary surface.",
  "modes": ["inline", "canvas"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "required": ["content"],
    "properties": {
      "content": { "type": "string" }
    },
    "additionalProperties": false
  }
}
```

- [ ] **Step 2: Add a registry test**

Append to `tests/test_widgets.py`:

```python
def test_markdown_document_registered():
    from decafclaw.widgets import load_widget_registry
    from decafclaw.config import Config
    cfg = Config()
    reg = load_widget_registry(cfg)
    desc = reg.get("markdown_document")
    assert desc is not None
    assert "inline" in desc.modes
    assert "canvas" in desc.modes
    ok, _ = reg.validate("markdown_document", {"content": "# hi"})
    assert ok
    bad_ok, _ = reg.validate("markdown_document", {"wrong": 1})
    assert not bad_ok
```

Run: `uv run pytest tests/test_widgets.py -k markdown_document -v`
Expected: pass.

- [ ] **Step 3: Implement the Lit component**

Create `src/decafclaw/web/static/widgets/markdown_document/widget.js`:

```js
import { LitElement, html } from 'lit';
import { renderMarkdown } from '/static/lib/markdown.js';

const INLINE_MAX_HEIGHT = '8rem';

export class MarkdownDocumentWidget extends LitElement {
  static properties = {
    data: { type: Object },
    mode: { type: String },
    expanded: { type: Boolean, state: true },
  };

  constructor() {
    super();
    this.data = {};
    this.mode = 'inline';
    this.expanded = false;
  }

  createRenderRoot() { return this; }  // light DOM

  willUpdate(changed) {
    if (this.mode !== 'canvas') return;
    if (!changed.has('data')) return;
    const scroller = this.querySelector('.md-doc-scroll');
    if (scroller) {
      this._savedScroll = {
        top: scroller.scrollTop,
        left: scroller.scrollLeft,
      };
    }
  }

  updated() {
    if (this.mode !== 'canvas') return;
    if (!this._savedScroll) return;
    const scroller = this.querySelector('.md-doc-scroll');
    if (!scroller) return;
    const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
    const maxLeft = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
    scroller.scrollTop = Math.min(this._savedScroll.top, maxTop);
    scroller.scrollLeft = Math.min(this._savedScroll.left, maxLeft);
    this._savedScroll = null;
  }

  _firstH1(content) {
    if (!content) return 'Untitled';
    for (const line of content.split('\n')) {
      const stripped = line.trim();
      if (stripped.startsWith('# ')) return stripped.slice(2).trim() || 'Untitled';
    }
    return 'Untitled';
  }

  _toggleExpand() {
    this.expanded = !this.expanded;
  }

  async _openInCanvas() {
    const convId = (window.dc && window.dc.activeConvId) || '';
    if (!convId) return;
    const label = this._firstH1(this.data?.content);
    try {
      const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}/set`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          widget_type: 'markdown_document',
          data: { content: this.data?.content ?? '' },
          label,
        }),
      });
      if (!resp.ok) {
        console.error('canvas set failed', resp.status, await resp.text());
      }
    } catch (err) {
      console.error('canvas set error', err);
    }
  }

  render() {
    const content = this.data?.content ?? '';
    const rendered = renderMarkdown(content);

    if (this.mode === 'canvas') {
      return html`
        <div class="md-doc md-doc-canvas">
          <div class="md-doc-scroll" .innerHTML=${rendered}></div>
        </div>
      `;
    }

    const collapsedStyle = this.expanded
      ? ''
      : `max-height: ${INLINE_MAX_HEIGHT}; overflow: hidden;`;
    return html`
      <div class="md-doc md-doc-inline ${this.expanded ? 'expanded' : 'collapsed'}">
        <div class="md-doc-body" style=${collapsedStyle} .innerHTML=${rendered}></div>
        <div class="md-doc-actions">
          <button @click=${this._toggleExpand}>${this.expanded ? 'Collapse' : 'Expand'}</button>
          <button @click=${this._openInCanvas}>Open in Canvas</button>
        </div>
      </div>
    `;
  }
}

customElements.define('dc-widget-markdown-document', MarkdownDocumentWidget);
```

- [ ] **Step 4: Add styles for the widget**

Find where bundled-widget styles live (search: `grep -rn "data-table\|data_table" src/decafclaw/web/static/styles/` and `src/decafclaw/web/static/widgets/`). If `data_table` ships its own stylesheet, follow that pattern; otherwise add to the existing global CSS bundle.

Add this CSS block (location: a `widgets.css` or wherever data_table styles live — the implementer should match the existing convention):

```css
.md-doc-inline.collapsed .md-doc-body {
  position: relative;
}
.md-doc-inline.collapsed .md-doc-body::after {
  content: "";
  position: absolute;
  inset: auto 0 0 0;
  height: 2rem;
  background: linear-gradient(transparent, var(--bg, #fff));
  pointer-events: none;
}
.md-doc-actions {
  display: flex;
  gap: 0.5rem;
  padding: 0.5rem 0;
}
.md-doc-canvas {
  height: 100%;
  display: flex;
}
.md-doc-canvas .md-doc-scroll {
  flex: 1;
  overflow: auto;
  padding: 1rem 1.25rem;
}
```

- [ ] **Step 5: Run python tests**

Run: `uv run pytest tests/test_widgets.py -v`

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/web/static/widgets/markdown_document/ tests/test_widgets.py
# Also add wherever the CSS landed:
git add src/decafclaw/web/static/styles/  # adjust path
git commit -m "$(cat <<'EOF'
feat(widgets): markdown_document widget — inline + canvas modes

Inline mode: collapsed via max-height with fade gradient, Expand and
Open in Canvas buttons. Canvas mode: full content, scroll position
preserved across data updates (clamped to current scrollable extent).
Render via existing renderMarkdown() — wiki-link and workspace://
support inherited.

Phase 3 of #256 (#388).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Frontend canvas state client module

**Files:**
- Create: `src/decafclaw/web/static/lib/canvas-state.js`

A tiny module that owns the per-conv canvas state cache, the dismiss flag, and the unread-update flag. Exposed for the panel and resummon UI via subscribe/snapshot semantics.

- [ ] **Step 1: Implement the module**

Create `src/decafclaw/web/static/lib/canvas-state.js`:

```js
/**
 * Canvas state — per-conversation canvas tab cache + dismiss flag + unread flag.
 *
 * Memory-only state; reload returns the user to the default visible state
 * (any non-empty canvas is shown by default).
 *
 * Subscribers receive `(state)` snapshots after every mutation so the
 * panel and resummon UI re-render. Snapshot shape:
 *   { tab: {id,label,widget_type,data}|null,
 *     visible: boolean,
 *     unreadDot: boolean }
 */

const _state = {
  byConv: new Map(),  // convId -> { tab, dismissed, unreadDot }
  active: null,
  subscribers: new Set(),
};

function _ensure(convId) {
  if (!_state.byConv.has(convId)) {
    _state.byConv.set(convId, { tab: null, dismissed: false, unreadDot: false });
  }
  return _state.byConv.get(convId);
}

function _publish() {
  const snap = currentSnapshot();
  for (const cb of _state.subscribers) {
    try { cb(snap); } catch (err) { console.error('canvas subscriber failed', err); }
  }
}

export function currentSnapshot() {
  if (!_state.active) {
    return { tab: null, visible: false, unreadDot: false };
  }
  const s = _ensure(_state.active);
  return {
    tab: s.tab,
    visible: !!s.tab && !s.dismissed,
    unreadDot: s.unreadDot,
  };
}

export function subscribe(callback) {
  _state.subscribers.add(callback);
  return () => _state.subscribers.delete(callback);
}

/** Switch to a different conversation. Loads state from the server. */
export async function setActiveConv(convId) {
  _state.active = convId;
  if (!convId) { _publish(); return; }
  _ensure(convId);
  try {
    const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}`,
                             { credentials: 'same-origin' });
    if (resp.ok) {
      const data = await resp.json();
      const tabs = data.tabs || [];
      const activeId = data.active_tab;
      const tab = tabs.find(t => t.id === activeId) || null;
      const s = _ensure(convId);
      s.tab = tab;
      s.dismissed = false;
      s.unreadDot = false;
    }
  } catch (err) {
    console.warn('canvas state load failed', err);
  }
  _publish();
}

/** Apply an incoming canvas_update WS event. */
export function applyEvent(evt) {
  const convId = evt.conv_id;
  if (!convId) return;
  const s = _ensure(convId);
  const kind = evt.kind || 'set';

  if (kind === 'clear') {
    s.tab = null;
    s.unreadDot = false;
    s.dismissed = false;
  } else if (kind === 'set') {
    s.tab = evt.tab || null;
    s.dismissed = false;
    s.unreadDot = false;
  } else if (kind === 'update') {
    s.tab = evt.tab || s.tab;
    if (s.dismissed) {
      s.unreadDot = true;
    } else {
      s.unreadDot = false;
    }
  }
  if (convId === _state.active) _publish();
}

export function dismiss() {
  if (!_state.active) return;
  const s = _ensure(_state.active);
  s.dismissed = true;
  _publish();
}

export function resummon() {
  if (!_state.active) return;
  const s = _ensure(_state.active);
  s.dismissed = false;
  s.unreadDot = false;
  _publish();
}
```

- [ ] **Step 2: Syntax check**

Run: `uv run python -c "import pathlib; assert pathlib.Path('src/decafclaw/web/static/lib/canvas-state.js').stat().st_size > 1500"`

(There's no JS test harness in the project; later tasks integrate this into the panel and verify via Playwright MCP smoke.)

- [ ] **Step 3: Commit**

```bash
git add src/decafclaw/web/static/lib/canvas-state.js
git commit -m "$(cat <<'EOF'
feat(web): canvas-state.js — per-conv canvas state client module

In-memory state cache with pub/sub. Owns the dismiss and unread-dot
flags per conv; clears them on set events and on conv-switch. Used by
the canvas panel and the resummon button.

Phase 3 of #256 (#388).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `<canvas-panel>` Lit component + integration into `index.html`

**Files:**
- Create: `src/decafclaw/web/static/components/canvas-panel.js`
- Create: `src/decafclaw/web/static/styles/canvas.css`
- Modify: `src/decafclaw/web/static/index.html`
- Modify: `src/decafclaw/web/static/app.js`
- Modify: `src/decafclaw/web/static/components/widgets/widget-host.js` (forward `mode`)

In-app panel: markup, component, CSS, conv-select integration, WS event handling, resize handle.

- [ ] **Step 1: Forward `mode` through `<dc-widget-host>`**

Read `src/decafclaw/web/static/components/widgets/widget-host.js`. If `mode` isn't already a property, add it to `static properties`:

```js
static properties = {
  widgetType: { type: String },
  descriptor: { type: Object },
  data: { type: Object },
  submitted: { type: Boolean },
  response: { type: Object },
  fallbackText: { type: String },
  mode: { type: String },  // 'inline' | 'canvas'
};
```

In the constructor, add `this.mode = 'inline';` to default it.

In the section that creates and configures the mounted widget element, after setting `el.data = this.data;`, add:

```js
if (this.mode) el.mode = this.mode;
```

- [ ] **Step 2: Implement `<canvas-panel>`**

Create `src/decafclaw/web/static/components/canvas-panel.js`:

```js
import { LitElement, html } from 'lit';
import { subscribe, currentSnapshot, dismiss } from '/static/lib/canvas-state.js';
import { getDescriptor } from '/static/lib/widget-catalog.js';
import '/static/components/widgets/widget-host.js';

export class CanvasPanel extends LitElement {
  static properties = {
    _snapshot: { state: true },
  };

  constructor() {
    super();
    this._snapshot = currentSnapshot();
    this._unsubscribe = null;
  }

  createRenderRoot() { return this; }

  connectedCallback() {
    super.connectedCallback();
    this._unsubscribe = subscribe(snap => {
      this._snapshot = snap;
      this._reflectVisibility();
    });
    this._reflectVisibility();
  }

  disconnectedCallback() {
    if (this._unsubscribe) this._unsubscribe();
    super.disconnectedCallback();
  }

  _reflectVisibility() {
    const wrap = document.getElementById('canvas-main');
    const handle = document.getElementById('canvas-resize-handle');
    if (!wrap || !handle) return;
    const visible = this._snapshot.visible;
    wrap.classList.toggle('hidden', !visible);
    handle.classList.toggle('hidden', !visible);
    if (visible) {
      // Mobile: opening canvas closes wiki.
      if (window.matchMedia('(max-width: 639px)').matches) {
        document.getElementById('wiki-main')?.classList.add('hidden');
      }
    }
  }

  _onClose() { dismiss(); }

  _onOpenInTab() {
    const convId = window.dc?.activeConvId;
    if (!convId) return;
    window.open(`/canvas/${encodeURIComponent(convId)}`, '_blank', 'noopener');
  }

  render() {
    const tab = this._snapshot.tab;
    if (!tab) {
      return html`<div class="canvas-empty">No canvas content yet.</div>`;
    }
    const descriptor = getDescriptor(tab.widget_type);
    return html`
      <header class="canvas-header">
        <span class="canvas-label">${tab.label || 'Canvas'}</span>
        <span class="canvas-spacer"></span>
        <button class="canvas-btn" title="Open in new tab"
                @click=${this._onOpenInTab}>↗</button>
        <button class="canvas-btn canvas-close" title="Close"
                @click=${this._onClose}>×</button>
      </header>
      <main class="canvas-body">
        <dc-widget-host
          .widgetType=${tab.widget_type}
          .descriptor=${descriptor}
          .data=${tab.data}
          .mode=${'canvas'}
          fallbackText="Canvas widget unavailable">
        </dc-widget-host>
      </main>
    `;
  }
}

customElements.define('canvas-panel', CanvasPanel);
```

- [ ] **Step 3: Add canvas styles**

Create `src/decafclaw/web/static/styles/canvas.css`:

```css
/* Canvas panel — desktop + mobile. */

#canvas-main {
  width: var(--canvas-width, 45%);
  min-width: 280px;
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--border, #ddd);
  background: var(--bg, #fff);
  overflow: hidden;
}

#canvas-resize-handle {
  width: 4px;
  cursor: col-resize;
  background: transparent;
}

#canvas-resize-handle:hover { background: var(--accent, #3b82f6); }

canvas-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.canvas-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border, #ddd);
  font-weight: 600;
}
.canvas-label { flex: 0 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.canvas-spacer { flex: 1; }
.canvas-btn {
  background: none; border: 0; cursor: pointer;
  font-size: 1.1rem; padding: 0.25rem 0.5rem;
  min-width: 2.5rem; min-height: 2.5rem;
}
.canvas-body { flex: 1; overflow: hidden; position: relative; }
.canvas-empty { padding: 1rem; color: var(--muted, #666); }

#chat-main { flex: 1; min-width: 320px; }

#chat-main-header {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 0.25rem 0.75rem;
  border-bottom: 1px solid var(--border, #eee);
  min-height: 2rem;
}
.canvas-resummon-pill {
  display: inline-flex;
  gap: 0.4rem;
  padding: 0.35rem 0.7rem;
  border-radius: 999px;
  border: 1px solid var(--border, #ccc);
  background: var(--bg, #fff);
  cursor: pointer;
  font-size: 0.85rem;
}
.canvas-resummon-pill[data-unread="true"]::after {
  content: "•";
  color: var(--accent, #3b82f6);
  font-weight: bold;
}

@media (max-width: 639px) {
  #canvas-main {
    position: fixed;
    inset: 0;
    width: 100%;
    z-index: 100;
    border-left: 0;
  }
  #canvas-resize-handle { display: none !important; }
  .canvas-btn { min-width: 44px; min-height: 44px; }
}
```

- [ ] **Step 4: Update `index.html`**

In `src/decafclaw/web/static/index.html`, find the `#chat-layout` block. Add a `#chat-main-header` strip inside `#chat-main` before `#mobile-header`, and add `#canvas-resize-handle` + `#canvas-main` after `#chat-main`:

```html
  <div id="chat-main">
    <div id="chat-main-header"></div>
    <div id="mobile-header">...</div>
    <chat-view></chat-view>
    <chat-input></chat-input>
  </div>
  <div id="canvas-resize-handle" class="hidden"></div>
  <div id="canvas-main" class="hidden">
    <canvas-panel></canvas-panel>
  </div>
```

In `<head>`, add:

```html
<link rel="stylesheet" href="/static/styles/canvas.css">
<script type="module" src="/static/components/canvas-panel.js"></script>
```

- [ ] **Step 5: Wire conv-select + WS event into `app.js`**

In `src/decafclaw/web/static/app.js`, near the top (alongside other imports / module-level code), add:

```js
import { setActiveConv, applyEvent } from '/static/lib/canvas-state.js';
```

In the conv-select handler — wherever the existing code calls into `select_conv` over the WS or sets up the new conv view — add:

```js
setActiveConv(convId);
window.dc = window.dc || {};
window.dc.activeConvId = convId;
```

In the WS message switch (the `onmessage` handler that switches on `msg.type`), add a case:

```js
} else if (msg.type === 'canvas_update') {
  applyEvent(msg);
}
```

- [ ] **Step 6: Add resize-handle drag handler**

Mirror the existing wiki resize handler in `app.js`. Find: `grep -n "wiki-resize-handle\|setupResizeHandle" src/decafclaw/web/static/app.js`. Add a parallel function:

```js
function setupCanvasResize() {
  const handle = document.getElementById('canvas-resize-handle');
  if (!handle) return;
  const layout = document.getElementById('chat-layout');
  const canvasMain = document.getElementById('canvas-main');
  const saved = parseInt(localStorage.getItem('dc.canvasWidthPx') || '0', 10);
  if (saved > 0) {
    canvasMain.style.setProperty('--canvas-width', `${saved}px`);
  }
  handle.addEventListener('mousedown', (e) => {
    const startX = e.clientX;
    const startWidth = canvasMain.getBoundingClientRect().width;
    const onMove = (ev) => {
      const dx = startX - ev.clientX;
      const newWidth = Math.max(280,
        Math.min(layout.getBoundingClientRect().width * 0.7,
                 startWidth + dx));
      canvasMain.style.setProperty('--canvas-width', `${newWidth}px`);
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      const w = canvasMain.getBoundingClientRect().width;
      localStorage.setItem('dc.canvasWidthPx', String(Math.round(w)));
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    e.preventDefault();
  });
}

setupCanvasResize();
```

- [ ] **Step 7: Smoke check via Playwright MCP**

Run dev server in worktree on a non-conflicting port. The default `HTTP_PORT` is 18880 — use 18881 to avoid clashing with the user's running instance:

```bash
HTTP_PORT=18881 uv run decafclaw &
```

Wait for the "uvicorn running" log. Then drive the browser:
- `mcp__playwright__browser_navigate` to `http://localhost:18881`.
- Log in with the token from `data/decafclaw/web_tokens.json` (read fresh; do not commit).
- `mcp__playwright__browser_console_messages` — expect no errors.
- `mcp__playwright__browser_evaluate` to confirm `document.querySelector('canvas-panel')` returns an element.
- The panel `#canvas-main` should be hidden (no canvas state).

Stop the dev server when done: `kill %1`.

Defer end-to-end behavior verification (set + reveal) to Task 12.

- [ ] **Step 8: Commit**

```bash
git add src/decafclaw/web/static/components/canvas-panel.js \
        src/decafclaw/web/static/styles/canvas.css \
        src/decafclaw/web/static/index.html \
        src/decafclaw/web/static/app.js \
        src/decafclaw/web/static/components/widgets/widget-host.js
git commit -m "$(cat <<'EOF'
feat(web): canvas-panel component + layout integration

Adds <canvas-panel> Lit component with header (label, open-in-new-tab,
close), body mounting <dc-widget-host> in canvas mode, plus a resize
handle whose width persists to localStorage. Wires conv-select and
canvas_update WS events into canvas-state.js. Forwards mode property
through dc-widget-host.

Phase 3 of #256 (#388).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Resummon UI (chat-main-header + mobile-header pill)

**Files:**
- Modify: `src/decafclaw/web/static/app.js`

The pill appears when canvas state exists *and* the panel is hidden. Click re-shows. CSS for the pill was added in Task 7.

- [ ] **Step 1: Mount the resummon pill**

Add to `app.js` near the `setupCanvasResize` call:

```js
import { subscribe as subscribeCanvas, resummon } from '/static/lib/canvas-state.js';

function setupResummonPill() {
  const desktopHost = document.getElementById('chat-main-header');
  const mobileHost = document.getElementById('mobile-header');
  if (!desktopHost) return;

  const renderTo = (host, snapshot) => {
    host.querySelector('.canvas-resummon-pill')?.remove();
    if (!snapshot.tab) return;
    if (snapshot.visible) return;
    const btn = document.createElement('button');
    btn.className = 'canvas-resummon-pill';
    btn.textContent = '📄 Canvas';
    if (snapshot.unreadDot) btn.dataset.unread = 'true';
    btn.addEventListener('click', () => resummon());
    host.appendChild(btn);
  };

  subscribeCanvas(snap => {
    renderTo(desktopHost, snap);
    if (mobileHost) renderTo(mobileHost, snap);
  });
}

setupResummonPill();
```

- [ ] **Step 2: Smoke check via Playwright MCP**

Defer to Task 12 (full flow). At this point just confirm no console errors when the page loads.

- [ ] **Step 3: Commit**

```bash
git add src/decafclaw/web/static/app.js
git commit -m "$(cat <<'EOF'
feat(web): canvas resummon pill in chat-main-header

Pill button appears when canvas state exists and the user has
dismissed the panel. Click resummons; an unread dot lights up when
canvas_update fires while hidden. Mobile pill mirrors in
#mobile-header.

Phase 3 of #256 (#388).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Mobile mutual exclusion with wiki

**Files:**
- Modify: `src/decafclaw/web/static/app.js`

Most of the mobile CSS landed in Task 7. This task hooks the sidebar tab change so opening wiki on mobile auto-closes canvas (the reverse direction is in `<canvas-panel>._reflectVisibility` from Task 7).

- [ ] **Step 1: Handle the wiki→canvas mutual exclusion on mobile**

In `app.js`, near where `sidebar-tab-change` events are handled (or where wiki visibility is toggled in the sidebar), add:

```js
import { dismiss as canvasDismiss, currentSnapshot as canvasSnapshot } from '/static/lib/canvas-state.js';

document.addEventListener('sidebar-tab-change', (e) => {
  const isMobile = window.matchMedia('(max-width: 639px)').matches;
  if (!isMobile) return;
  if (e.detail?.tab === 'wiki' && canvasSnapshot().visible) {
    canvasDismiss();
  }
});
```

If the imports for `dismiss` and `currentSnapshot` were already added in earlier tasks under different aliases, dedupe — otherwise use distinct local names as shown above.

- [ ] **Step 2: Smoke check via Playwright MCP at mobile width**

Defer to Task 12.

- [ ] **Step 3: Commit**

```bash
git add src/decafclaw/web/static/app.js
git commit -m "$(cat <<'EOF'
feat(web): canvas mobile overlay + wiki mutual exclusion

≤639px: opening wiki auto-closes canvas (and vice versa via
canvas-panel). 44px tap-target sizing on all panel controls (CSS in
canvas.css from Task 7).

Phase 3 of #256 (#388).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Standalone `/canvas/{conv_id}` view

**Files:**
- Modify: `src/decafclaw/web/static/canvas-page.html` (full content; placeholder added in Task 4)
- Create: `src/decafclaw/web/static/canvas-page.js`
- Modify: `src/decafclaw/web/static/styles/canvas.css` (append standalone-page block)

Live-updating standalone canvas page.

- [ ] **Step 1: Replace `canvas-page.html` with the full markup**

Open `src/decafclaw/web/static/canvas-page.html`. Inspect the existing `index.html` for the `<script type="importmap">` block — copy it verbatim into the canvas page so module imports resolve identically.

Replace contents with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Canvas</title>
  <link rel="stylesheet" href="/static/styles/main.css">
  <link rel="stylesheet" href="/static/styles/canvas.css">
  <!-- IMPORT MAP: copy verbatim from index.html -->
  <script type="importmap">
  {
    "imports": {}
  }
  </script>
  <script type="module" src="/static/components/widgets/widget-host.js"></script>
  <script type="module" src="/static/canvas-page.js"></script>
</head>
<body class="canvas-standalone">
  <header id="canvas-standalone-header">
    <h1 id="canvas-label">Canvas</h1>
    <span class="canvas-spacer"></span>
    <a id="canvas-back-link" class="back-link" href="/">← Back to chat</a>
  </header>
  <main id="canvas-standalone-body">
    <div id="canvas-empty-state" class="canvas-empty">No canvas content yet.</div>
    <dc-widget-host id="canvas-standalone-host" hidden></dc-widget-host>
  </main>
</body>
</html>
```

> **CRITICAL:** Replace the empty `"imports": {}` block with the actual importmap from `index.html` so `lit` and other bare specifiers resolve.

- [ ] **Step 2: Implement the page controller**

Create `src/decafclaw/web/static/canvas-page.js`:

```js
/**
 * Standalone canvas page controller.
 *
 * Reads conv_id from the URL path, fetches initial state via REST,
 * mounts the active widget into <dc-widget-host>, and subscribes to
 * canvas_update events over WebSocket for live updates.
 */

import { getDescriptor } from '/static/lib/widget-catalog.js';

const PATH_RE = /^\/canvas\/([^/?#]+)/;
const m = location.pathname.match(PATH_RE);
const convId = m ? decodeURIComponent(m[1]) : '';
if (!convId) {
  document.body.innerHTML = '<p>Invalid canvas URL.</p>';
  throw new Error('no conv_id');
}

const host = document.getElementById('canvas-standalone-host');
const empty = document.getElementById('canvas-empty-state');
const labelEl = document.getElementById('canvas-label');
const backLink = document.getElementById('canvas-back-link');
backLink.href = `/?conv=${encodeURIComponent(convId)}`;

function applyTab(tab) {
  if (!tab) {
    host.hidden = true;
    empty.hidden = false;
    labelEl.textContent = 'Canvas (empty)';
    document.title = 'Canvas';
    return;
  }
  empty.hidden = true;
  host.hidden = false;
  labelEl.textContent = tab.label || 'Canvas';
  document.title = `Canvas — ${tab.label || 'Canvas'}`;

  const descriptor = getDescriptor(tab.widget_type);
  host.widgetType = tab.widget_type;
  host.descriptor = descriptor;
  host.mode = 'canvas';
  host.data = tab.data;
}

async function loadInitial() {
  try {
    const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}`,
                             { credentials: 'same-origin' });
    if (!resp.ok) {
      console.warn('canvas load failed', resp.status);
      applyTab(null);
      return;
    }
    const data = await resp.json();
    const tabs = data.tabs || [];
    const tab = tabs.find(t => t.id === data.active_tab) || null;
    applyTab(tab);
  } catch (err) {
    console.error('canvas load error', err);
    applyTab(null);
  }
}

function openWebSocket() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.addEventListener('open', () => {
    ws.send(JSON.stringify({ type: 'select_conv', conv_id: convId }));
  });
  ws.addEventListener('message', (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type !== 'canvas_update') return;
    if (msg.conv_id && msg.conv_id !== convId) return;
    applyTab(msg.tab);
  });
  ws.addEventListener('close', () => {
    // Reconnect/backoff is out-of-scope (per spec).
    console.info('canvas WS closed');
  });
}

await loadInitial();
openWebSocket();
```

- [ ] **Step 3: Add standalone-page CSS**

Append to `src/decafclaw/web/static/styles/canvas.css`:

```css
body.canvas-standalone {
  margin: 0;
  display: flex;
  flex-direction: column;
  height: 100vh;
}
#canvas-standalone-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--border, #ddd);
}
#canvas-standalone-header h1 { font-size: 1rem; margin: 0; }
#canvas-standalone-body {
  flex: 1;
  overflow: hidden;
  display: flex;
}
#canvas-standalone-body > * { flex: 1; }
```

- [ ] **Step 4: Smoke check via Playwright MCP**

After dev-server restart (worktree on port 18881), navigate to `/canvas/<known_conv_id>`. Verify:
- Page loads, no console errors.
- If state empty, "No canvas content yet." shown.
- If agent has set canvas, the markdown renders.
- Triggering `canvas_update` from the main UI updates this page live.

Defer the full smoke checklist to Task 12.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/web/static/canvas-page.html \
        src/decafclaw/web/static/canvas-page.js \
        src/decafclaw/web/static/styles/canvas.css
git commit -m "$(cat <<'EOF'
feat(web): standalone /canvas/{conv_id} view with live updates

Standalone HTML page that fetches initial canvas state and subscribes
to canvas_update events over WebSocket. Reuses <dc-widget-host> in
canvas mode. Same web-auth as the main UI. Reconnect/backoff deferred
to a follow-up issue.

Phase 3 of #256 (#388).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Documentation updates

**Files:**
- Modify: `docs/widgets.md`, `docs/web-ui.md`, `docs/web-ui-mobile.md`, `docs/conversations.md`, `docs/context-composer.md`, `CLAUDE.md`, `README.md`

Per CLAUDE.md "When changing a feature: update its `docs/` page as part of the same PR".

- [ ] **Step 1: `docs/widgets.md`**

- Remove the "Canvas panel out-of-scope" line.
- Add a new "Phase 3 — Canvas panel and `markdown_document`" section that summarizes:
  - `WidgetRequest.target` and the `widget.json` `modes` field as the canvas-mode contract.
  - Inline vs canvas: the host sets `el.mode = 'inline' | 'canvas'`; widgets render accordingly.
  - The `markdown_document` widget — descriptor, modes, inline collapse + buttons, canvas scroll preservation.
  - Cross-links to `docs/web-ui.md` (UI surface) and `docs/context-composer.md` (always-loaded canvas tools).

- [ ] **Step 2: `docs/web-ui.md`**

Add a "Canvas panel" section covering:
- Layout (`conversation-sidebar | wiki-main? | chat-main | canvas-main?`).
- Resummon UI (chat-main-header pill, unread dot semantics).
- Dismiss behavior (in-memory ephemeral; cleared on `set` events / conv-switch / reload).
- `/canvas/{conv_id}` standalone view route.
- Mobile mutual exclusion with wiki.

- [ ] **Step 3: `docs/web-ui-mobile.md`**

Add a row to the breakpoint behaviors:
- Canvas panel: full-screen overlay at ≤639px.
- Mutually exclusive with wiki overlay (most-recent-open wins).
- Resize handle hidden.
- 44px tap-target controls.

- [ ] **Step 4: `docs/conversations.md`**

Add a bullet to the per-conversation sidecar list:
- `{conv_id}.canvas.json` — canvas widget state. Sample shape with the `tabs[]` array.

- [ ] **Step 5: `docs/context-composer.md`**

Update the always-loaded tool list (or wherever core tools are enumerated for system-prompt assembly) to include `canvas_set`, `canvas_update`, `canvas_clear`, `canvas_read` with one-line descriptions each.

- [ ] **Step 6: `CLAUDE.md`**

In the "Key files → Data and persistence" list, add `canvas.py`.
In the "Key files → Tools" list, add `canvas_tools.py`.
In the Web UI subsection of the conventions, mention canvas panel + standalone view briefly.

- [ ] **Step 7: `README.md`**

Update the feature list (if any) so canvas panel is mentioned alongside chat / vault / files. Keep one line.

- [ ] **Step 8: Commit**

```bash
git add docs/ CLAUDE.md README.md
git commit -m "$(cat <<'EOF'
docs: canvas panel + markdown_document widget (Phase 3)

Documents canvas architecture, widget mode contract,
markdown_document descriptor, REST + WS surfaces, mobile behavior, and
sidecar persistence. Updates key-files lists and feature summaries.

Phase 3 of #256 (#388).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: End-to-end manual smoke test (Playwright MCP)

**Files:** None (verification + any necessary fixup commits).

Walk through the full smoke list using the Playwright MCP. Document outcomes in `notes.md`. Fix anything found before declaring complete.

- [ ] **Step 1: Start dev server in the worktree on a non-conflicting port**

```bash
HTTP_PORT=18881 uv run decafclaw &
```

Wait for the "uvicorn running" log line. (Le has an instance on 18880 — do NOT clash.)

- [ ] **Step 2: Drive the browser via Playwright MCP**

- `mcp__playwright__browser_navigate` to `http://localhost:18881`.
- Read the auth token fresh from `data/decafclaw/web_tokens.json` (do NOT commit). Submit it via the login form using `mcp__playwright__browser_fill_form` + `mcp__playwright__browser_click`.

- [ ] **Step 3: Run each spec smoke item**

Create or open a conv. Trigger agent calls (either by chatting and asking the agent to call `canvas_set`, or by invoking the tool through a test skill; whichever is easier for the implementer).

For each item, use `mcp__playwright__browser_*` tools and `console_messages` / `network_requests` to verify behavior. Append pass/fail to `notes.md`.

1. `canvas_set("markdown_document", {"content": "# Hi\n\nbody"})` → panel appears, label "Hi", body renders.
2. `canvas_update({"content": "# Hi\n\nlonger body"})` → content swaps. Pre-update: scroll the canvas body. Post-update: scroll position preserved.
3. Click close (×) on canvas → panel hides; resummon pill appears in chat-main-header. Trigger `canvas_update` → pill shows unread dot. Click pill → panel reappears, dot gone.
4. With panel hidden, trigger another `canvas_set` → panel auto-reveals.
5. Inline `markdown_document` widget (in a tool result message): collapsed + fade visible. Click "Expand" → grows; "Collapse" reverses. Click "Open in Canvas" → canvas updates with that content; verify a `POST /api/canvas/{conv_id}/set` and a `canvas_update` WS event in network panel.
6. Drag `#canvas-resize-handle` → panel resizes. Reload page → width persists.
7. Open `/canvas/<conv_id>` in a second browser tab → loads current state. Trigger `canvas_update` from first tab → second tab updates live.
8. Resize browser to 600px width → canvas full-screen overlay. Open wiki → canvas auto-closes; opening canvas again → wiki auto-closes. Close button measures ≥44×44px.
9. Switch to a different conv → its canvas state loads (or empty). Switch back → first conv's canvas restored. Dismiss flag is per-conv.
10. `canvas_clear` → panel hides for both watching tabs (in-app + standalone).

- [ ] **Step 4: Stop dev server**

```bash
kill %1
```

- [ ] **Step 5: Document results in `notes.md`**

Append a `## Smoke test results` section listing each item with PASS / FAIL and any notes.

- [ ] **Step 6: Fix any issues found**

Common gotchas to anticipate:
- `<dc-widget-host>` not forwarding `mode` to the mounted widget (re-check Task 7 step 1).
- Resummon pill layout glitch on conv-switch — guard `_publish` calls or rerender after a microtask.
- WS reconnect not handled in standalone view — note as known limitation per spec, do not fix here.
- `chat-main-header` pushing chat-view layout unexpectedly on narrow widths — adjust CSS if needed.
- `canvas-page.html` importmap missing — copy from `index.html` (Task 10 step 1 warning).

Each fix is its own small commit:

```bash
git add <files>
git commit -m "fix(canvas): smoke-test correction — <brief summary>"
```

- [ ] **Step 7: Final test suite + lint pass**

```bash
uv run pytest tests/ -q
uv run make check  # lint + typecheck (Python + JS)
```

Both should pass cleanly.

---

## After completion

When all 12 tasks are done:

1. Push branch: `git push -u origin widgets-phase-3-388`.
2. Open PR: `gh pr create` with body including `Closes #388`. Note the spec and plan paths.
3. After merge, file follow-on issues for the spec's out-of-scope items.
4. Move to dev-session retro phase via `/dev-session retro`.

---

## Self-review

**Spec coverage:**
- Persistence: Task 1 ✓
- Tools: Task 3 ✓
- WebSocket event: Tasks 1 (emit) + 2 (forward) ✓
- REST endpoints: Task 4 ✓
- markdown_document widget (inline + canvas): Task 5 ✓
- Frontend state module: Task 6 ✓
- Canvas panel: Task 7 ✓
- Resummon UI: Task 8 ✓
- Mobile responsive: Task 9 (CSS in 7) ✓
- Standalone view: Task 10 ✓
- Documentation: Task 11 ✓
- Acceptance criteria & smoke: Task 12 ✓
- Validation/error cases: Task 1 + 3 tests ✓

**Type consistency:**
- `set_canvas` / `update_canvas` / `clear_canvas` / `get_active_tab` / `read_canvas_state` (canvas.py) consistent with `tool_canvas_set` / `_update` / `_clear` / `_read` (canvas_tools.py).
- `CanvasOpResult { ok, text, error }` shape used uniformly.
- WS event payload shape `{type, conv_id, kind, active_tab, tab}` consistent in canvas.py emit, websocket.py forward, frontend canvas-state.js applyEvent, canvas-page.js applyTab.
- Frontend `mode` property `'inline' | 'canvas'` consistent in markdown_document widget, widget-host (forwarder), canvas-panel, canvas-page.

**Placeholder scan:**
- No "TBD" / "fill in" markers.
- One area of pragmatic vagueness: Task 5 step 4 says "find where bundled-widget styles live." This is a real ambiguity in the existing codebase that the implementer must resolve at edit time. Includes a concrete grep pattern to locate the right file.
- Task 5 step 5 references `app.js` setting `window.dc.activeConvId`. Task 7 step 5 includes that exact line. If Task 7 lands first, Task 5 has nothing to add; otherwise Task 5 adds it. Either way the `if (!convId) return;` guard in `_openInCanvas` makes it safe before `activeConvId` is defined.
- Task 10 step 1 contains an `"imports": {}` placeholder for the importmap with a CRITICAL note to copy from `index.html`. This is unavoidable: the implementer must read the live importmap rather than have it duplicated in the plan (which would rot).
