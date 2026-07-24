# Sticky Widget Slot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single-slot `sticky` widget surface pinned above the chat input, driven by an explicit backend primitive + agent tools, mirroring the canvas subsystem.

**Architecture:** A new `sticky` widget mode (opt-in per `widget.json`). Backend `sticky.py` owns a per-conversation `sticky.json` sidecar and `set_sticky`/`clear_sticky` that write it and emit dedicated `sticky_set`/`sticky_clear` WS events. Agent tools `widget_pin_sticky`/`widget_unpin_sticky` call the primitive (the checklist will call `set_sticky` directly in #414). Frontend `sticky-state.js` + `<sticky-slot>` render the slot between `<chat-view>` and `<chat-input>`, with a collapse-to-summary affordance (expanded desktop / collapsed mobile). This mirrors `canvas.py` / `canvas_tools.py` / `canvas-state.js` / `canvas-panel.js` — the only working non-inline surface. The `target` field on `WidgetRequest` is untouched (it is vestigial and never routes cross-surface).

**Tech Stack:** Python 3.13, Starlette (`http_server.py`), Lit (web components), the `message_types.json` codegen pipeline, pytest.

## Global Constraints

- Stdlib imports at module level; function-level imports only to break cycles.
- `ToolResult(text="[error: ...]")` for tool errors, never bare strings/raises.
- Fail-open disk I/O: corrupt/missing sidecar → empty state (mirror `read_canvas_state`).
- Atomic sidecar writes: tmp-file + `replace` (mirror `write_canvas_state`).
- WS wire types ONLY via `message_types.json` + `make gen-message-types`; never hand-edit generated files. `make check-message-types` must pass.
- WS forwarders: read event fields with `event.get(k) or default`, not `event.get(k, default)` (coerces explicit `None`).
- Tools receive `ctx` first. New tools default `priority: "normal"`.
- Single slot: a new pin replaces the previous one. Display-only (no input widgets in the slot).
- No eval cases this session (niche UI tools; revisit with #414's checklist auto-emit).

---

### Task 1: Add `sticky` mode value + enable on `markdown_document`

Adds the mode to the widget meta-schema so `widget.json` files may declare it, and opts `markdown_document` in as the test/demo occupant.

**Files:**
- Modify: `src/decafclaw/widgets.py:29-33` (meta-schema `modes` enum)
- Modify: `src/decafclaw/web/static/widgets/markdown_document/widget.json` (`modes`)
- Test: `tests/test_widgets.py` (append)

**Interfaces:**
- Produces: widget descriptors may now include `"sticky"` in `.modes`. `markdown_document` declares modes `["inline", "canvas", "sticky"]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_widgets.py`:
```python
def test_sticky_is_a_valid_mode():
    from decafclaw.widgets import load_widget_registry
    reg = load_widget_registry(config=None)
    desc = reg.get("markdown_document")
    assert desc is not None
    assert "sticky" in desc.modes
```
(If `load_widget_registry`'s signature differs, match the existing calls in `tests/test_widgets.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_widgets.py::test_sticky_is_a_valid_mode -v`
Expected: FAIL — `"sticky"` not in modes (widget.json doesn't declare it, and the enum would reject it).

- [ ] **Step 3: Implement**

In `src/decafclaw/widgets.py`, extend the enum (currently `["inline", "canvas"]`):
```python
        "modes": {"type": "array",
                  "items": {"type": "string",
                            "enum": ["inline", "canvas", "sticky"]},
                  "minItems": 1},
```
In `src/decafclaw/web/static/widgets/markdown_document/widget.json`, add `"sticky"` to the `modes` array (e.g. `"modes": ["inline", "canvas", "sticky"]`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_widgets.py::test_sticky_is_a_valid_mode -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/widgets.py src/decafclaw/web/static/widgets/markdown_document/widget.json tests/test_widgets.py
git commit -m "feat(419): add sticky widget mode; enable on markdown_document"
```

---

### Task 2: `sticky.py` — sidecar persistence

Single-slot sidecar read/write, fail-open + atomic, mirroring `canvas.py`'s helpers.

**Files:**
- Create: `src/decafclaw/sticky.py`
- Test: `tests/test_sticky.py`

**Interfaces:**
- Produces:
  - `empty_sticky_state() -> dict` → `{"schema_version": 1, "widget_type": None, "data": None}`
  - `read_sticky_state(config, conv_id: str) -> dict`
  - `write_sticky_state(config, conv_id: str, state: dict) -> bool`
  - `EmitFn = Callable[[str, dict], Awaitable[None]]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sticky.py`:
```python
from decafclaw import sticky


def test_empty_state_shape():
    assert sticky.empty_sticky_state() == {
        "schema_version": 1, "widget_type": None, "data": None,
    }


def test_read_missing_is_empty(config):
    assert sticky.read_sticky_state(config, "no-such-conv") == \
        sticky.empty_sticky_state()


def test_write_then_read_roundtrip(config):
    state = {"schema_version": 1, "widget_type": "markdown_document",
             "data": {"content": "# hi"}}
    assert sticky.write_sticky_state(config, "conv-a", state) is True
    assert sticky.read_sticky_state(config, "conv-a") == state


def test_corrupt_file_is_empty(config):
    from decafclaw.conversation_paths import sidecar_path
    p = sidecar_path(config, "conv-b", "sticky.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json")
    assert sticky.read_sticky_state(config, "conv-b") == \
        sticky.empty_sticky_state()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sticky.py -v`
Expected: FAIL — `ModuleNotFoundError: decafclaw.sticky`.

- [ ] **Step 3: Implement**

Create `src/decafclaw/sticky.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sticky.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/sticky.py tests/test_sticky.py
git commit -m "feat(419): sticky.json sidecar persistence"
```

---

### Task 3: `sticky.py` — `set_sticky` / `clear_sticky` + validation + emit

**Files:**
- Modify: `src/decafclaw/sticky.py` (append)
- Test: `tests/test_sticky.py` (append)

**Interfaces:**
- Consumes: `read_sticky_state`, `write_sticky_state`, `StickyOpResult`, `EmitFn`, `get_widget_registry`.
- Produces:
  - `set_sticky(config, conv_id, widget_type, data, emit=None) -> StickyOpResult`
  - `clear_sticky(config, conv_id, emit=None) -> StickyOpResult`
  - `_validate_widget_for_sticky(widget_type, data) -> str | None`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sticky.py`:
```python
import pytest
from decafclaw.widgets import init_widgets


@pytest.fixture
def widgets_ready(config):
    init_widgets(config)  # loads bundled widget.json registry


@pytest.mark.asyncio
async def test_set_sticky_writes_and_emits(config, widgets_ready):
    events = []

    async def emit(conv_id, payload):
        events.append((conv_id, payload))

    res = await sticky.set_sticky(
        config, "conv-s", "markdown_document", {"content": "# hi"}, emit=emit)
    assert res.ok, res.error
    state = sticky.read_sticky_state(config, "conv-s")
    assert state["widget_type"] == "markdown_document"
    assert events and events[0][1]["type"] == "sticky_set"
    assert events[0][1]["widget_type"] == "markdown_document"


@pytest.mark.asyncio
async def test_set_sticky_rejects_non_sticky_widget(config, widgets_ready):
    # text_input declares modes ["inline"] only.
    res = await sticky.set_sticky(config, "conv-s", "text_input", {})
    assert not res.ok
    assert "sticky" in res.error


@pytest.mark.asyncio
async def test_set_sticky_replaces_previous(config, widgets_ready):
    await sticky.set_sticky(config, "conv-s", "markdown_document", {"content": "# a"})
    await sticky.set_sticky(config, "conv-s", "markdown_document", {"content": "# b"})
    state = sticky.read_sticky_state(config, "conv-s")
    assert state["data"]["content"] == "# b"


@pytest.mark.asyncio
async def test_clear_sticky_emits_and_empties(config, widgets_ready):
    events = []

    async def emit(conv_id, payload):
        events.append(payload)

    await sticky.set_sticky(config, "conv-s", "markdown_document", {"content": "# a"})
    res = await sticky.clear_sticky(config, "conv-s", emit=emit)
    assert res.ok
    assert sticky.read_sticky_state(config, "conv-s") == sticky.empty_sticky_state()
    assert events and events[-1]["type"] == "sticky_clear"
```
(Confirm `pytest.mark.asyncio` is the project's async-test convention by checking an existing `tests/test_canvas.py` test; match it — the repo uses `asyncio_mode` config.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_sticky.py -v`
Expected: FAIL — `AttributeError: module 'decafclaw.sticky' has no attribute 'set_sticky'`.

- [ ] **Step 3: Implement**

Append to `src/decafclaw/sticky.py`:
```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_sticky.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/sticky.py tests/test_sticky.py
git commit -m "feat(419): set_sticky/clear_sticky with mode validation + emit"
```

---

### Task 4: `sticky_tools.py` — agent tools + registration

**Files:**
- Create: `src/decafclaw/tools/sticky_tools.py`
- Modify: `src/decafclaw/tools/__init__.py:9,38,49`
- Test: `tests/test_sticky_tools.py`

**Interfaces:**
- Consumes: `sticky.set_sticky`, `sticky.clear_sticky`.
- Produces: `STICKY_TOOLS` dict + `STICKY_TOOL_DEFINITIONS` list; tools `widget_pin_sticky(ctx, widget_type, data)` and `widget_unpin_sticky(ctx)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sticky_tools.py`:
```python
import pytest
from decafclaw import sticky
from decafclaw.tools.sticky_tools import tool_widget_pin_sticky, tool_widget_unpin_sticky
from decafclaw.widgets import init_widgets


@pytest.fixture
def widgets_ready(config):
    init_widgets(config)


@pytest.mark.asyncio
async def test_pin_sticky_pins(ctx, widgets_ready):
    res = await tool_widget_pin_sticky(ctx, "markdown_document", {"content": "# hi"})
    assert "[error" not in res.text
    state = sticky.read_sticky_state(ctx.config, ctx.conv_id)
    assert state["widget_type"] == "markdown_document"


@pytest.mark.asyncio
async def test_pin_sticky_rejects_unknown_mode(ctx, widgets_ready):
    res = await tool_widget_pin_sticky(ctx, "text_input", {})
    assert res.text.startswith("[error")


@pytest.mark.asyncio
async def test_unpin_sticky_clears(ctx, widgets_ready):
    await tool_widget_pin_sticky(ctx, "markdown_document", {"content": "# hi"})
    res = await tool_widget_unpin_sticky(ctx)
    assert "[error" not in res.text
    assert sticky.read_sticky_state(ctx.config, ctx.conv_id) == sticky.empty_sticky_state()
```
(Match the `ctx` fixture used in `tests/test_canvas.py` / existing tool tests — it must carry `config` and `conv_id`.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_sticky_tools.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the tools**

Create `src/decafclaw/tools/sticky_tools.py`:
```python
"""Agent-facing sticky-slot tools.

Pin a single display-only widget above the chat input, or clear it. The slot
is single-occupancy — pinning replaces any previous widget. Workflow-driven
producers (e.g. the checklist in #414) call decafclaw.sticky.set_sticky
directly; these tools are the agent's explicit surface.
"""

import logging

from .. import sticky as sticky_mod
from ..media import ToolResult

log = logging.getLogger(__name__)


def _emit_for_ctx(ctx):
    manager = getattr(ctx, "manager", None)
    if manager is None:
        return None
    return manager.emit


async def tool_widget_pin_sticky(ctx, widget_type: str, data: dict) -> ToolResult:
    """Pin a widget into the sticky slot above the chat input."""
    log.info("[tool:widget_pin_sticky] widget=%s", widget_type)
    result = await sticky_mod.set_sticky(
        ctx.config, ctx.conv_id, widget_type, data, emit=_emit_for_ctx(ctx))
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_widget_unpin_sticky(ctx) -> ToolResult:
    """Clear the sticky slot."""
    log.info("[tool:widget_unpin_sticky]")
    result = await sticky_mod.clear_sticky(
        ctx.config, ctx.conv_id, emit=_emit_for_ctx(ctx))
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


STICKY_TOOLS = {
    "widget_pin_sticky": tool_widget_pin_sticky,
    "widget_unpin_sticky": tool_widget_unpin_sticky,
}

STICKY_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "widget_pin_sticky",
            "description": (
                "Pin a single display-only widget into the sticky slot directly "
                "above the chat input, where it stays visible while a workflow is "
                "in progress (unlike inline widgets, which scroll away). The slot "
                "holds ONE widget — pinning replaces any previous one. Use for "
                "at-a-glance status the user should keep seeing. Clear it with "
                "widget_unpin_sticky when the work is done. The widget_type must "
                "declare sticky mode (e.g. 'markdown_document')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "widget_type": {
                        "type": "string",
                        "description": "Registered sticky-mode widget name.",
                    },
                    "data": {
                        "type": "object",
                        "description": "Widget payload; must conform to the widget's data_schema.",
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
            "name": "widget_unpin_sticky",
            "description": (
                "Clear the sticky slot above the chat input, hiding whatever "
                "widget was pinned there. Use when the pinned status is no longer "
                "relevant (e.g. the workflow finished)."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
```

- [ ] **Step 4: Register the tools**

In `src/decafclaw/tools/__init__.py`:
- Line ~9 (imports): add `from .sticky_tools import STICKY_TOOL_DEFINITIONS, STICKY_TOOLS`
- Line ~38 (TOOLS dict merge): add `**STICKY_TOOLS` to the merged dict.
- Line ~49 (DEFINITIONS concat): add `+ STICKY_TOOL_DEFINITIONS`.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_sticky_tools.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/tools/sticky_tools.py src/decafclaw/tools/__init__.py tests/test_sticky_tools.py
git commit -m "feat(419): widget_pin_sticky/widget_unpin_sticky tools"
```

---

### Task 5: WS message types `sticky_set` / `sticky_clear`

**Files:**
- Modify: `src/decafclaw/web/message_types.json`
- Regenerate: `message_types.py`, `web/static/lib/message-types.js`, `docs/websocket-messages.md`, `tui/src/types.generated.ts` (via `make gen-message-types`)

**Interfaces:**
- Produces: `WSMessageType.STICKY_SET` / `.STICKY_CLEAR` (Python), `MESSAGE_TYPES.STICKY_SET` / `.STICKY_CLEAR` (JS); TypedDicts `SrvStickySet` / `SrvStickyClear`.

- [ ] **Step 1: Add the message definitions**

In `src/decafclaw/web/message_types.json`, add two entries under `messages` (mirror the `canvas_update` entry shape):
```json
    "sticky_set": {
      "direction": "server_to_client",
      "description": "A widget was pinned into the conversation's sticky slot.",
      "fields": {
        "conv_id": "string",
        "widget_type": "string",
        "data": "object"
      }
    },
    "sticky_clear": {
      "direction": "server_to_client",
      "description": "The conversation's sticky slot was cleared.",
      "fields": {
        "conv_id": "string"
      }
    }
```

- [ ] **Step 2: Regenerate**

Run: `make gen-message-types`
Expected: updates the 4 generated files; no errors.

- [ ] **Step 3: Verify drift check passes**

Run: `make check-message-types`
Expected: PASS (generated files match the manifest).

- [ ] **Step 4: Commit**

```bash
git add src/decafclaw/web/message_types.json src/decafclaw/web/message_types.py src/decafclaw/web/static/lib/message-types.js docs/websocket-messages.md tui/src/types.generated.ts
git commit -m "feat(419): sticky_set/sticky_clear WS message types"
```

---

### Task 6: WS forwarders in `websocket.py`

Forward `sticky_set` / `sticky_clear` events to the client, mirroring the `canvas_update` inline branch and the `_make_*_forwarder` test helper.

**Files:**
- Modify: `src/decafclaw/web/websocket.py` (inline branch near the `canvas_update` branch ~:672; add a `_make_sticky_forwarder` near :70 for tests)
- Test: `tests/test_websocket_sticky.py` (mirror the existing canvas forwarder test if present — grep `_make_canvas_update_forwarder` in `tests/`)

**Interfaces:**
- Consumes: `WSMessageType.STICKY_SET/STICKY_CLEAR`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_websocket_sticky.py` (model on the canvas forwarder test; if none exists, this minimal one):
```python
import pytest
from decafclaw.web.websocket import _make_sticky_forwarder
from decafclaw.web.message_types import WSMessageType


@pytest.mark.asyncio
async def test_sticky_set_forwarded():
    sent = []
    state = {"ws_send": lambda m: sent.append(m) or _noop()}
    fwd = _make_sticky_forwarder(state, "conv-x")
    await fwd({"type": "sticky_set", "conv_id": "conv-x",
               "widget_type": "markdown_document", "data": {"content": "# hi"}})
    assert sent and sent[0]["type"] == WSMessageType.STICKY_SET
    await fwd({"type": "sticky_set", "conv_id": "other", "widget_type": "x", "data": {}})
    assert len(sent) == 1  # filtered by conv_id


async def _noop():
    return None
```
(If `ws_send` must be an async callable, define it as `async def _send(m): sent.append(m)` and pass that — match the canvas forwarder test's shape exactly.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_websocket_sticky.py -v`
Expected: FAIL — `_make_sticky_forwarder` undefined.

- [ ] **Step 3: Implement the forwarder + inline branch**

Add near `_make_canvas_update_forwarder` (~`websocket.py:70`):
```python
def _make_sticky_forwarder(state, conv_id):
    """Forward sticky_set / sticky_clear events to ws_send (tests)."""
    ws_send = state["ws_send"]

    async def _forward(event):
        etype = event.get("type")
        if etype not in ("sticky_set", "sticky_clear"):
            return
        if event.get("conv_id") != conv_id:
            return
        if etype == "sticky_set":
            await ws_send({
                "type": WSMessageType.STICKY_SET,
                "conv_id": conv_id,
                "widget_type": event.get("widget_type") or "",
                "data": event.get("data") or {},
            })
        else:
            await ws_send({
                "type": WSMessageType.STICKY_CLEAR,
                "conv_id": conv_id,
            })

    return _forward
```
Add inline branches in `on_conv_event` after the `canvas_update` branch (~:682):
```python
        elif event_type == "sticky_set":
            if event_conv_id == conv_id:
                await ws_send({
                    "type": WSMessageType.STICKY_SET,
                    "conv_id": event_conv_id,
                    "widget_type": event.get("widget_type") or "",
                    "data": event.get("data") or {},
                })

        elif event_type == "sticky_clear":
            if event_conv_id == conv_id:
                await ws_send({
                    "type": WSMessageType.STICKY_CLEAR,
                    "conv_id": event_conv_id,
                })
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_websocket_sticky.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/web/websocket.py tests/test_websocket_sticky.py
git commit -m "feat(419): forward sticky_set/sticky_clear over WS"
```

---

### Task 7: REST `GET /api/sticky/{conv_id}`

Reload recovery, mirroring `get_canvas_state`.

**Files:**
- Modify: `src/decafclaw/http_server.py` (add `get_sticky_state` near `get_canvas_state` :1714; register route near :2084)
- Test: `tests/test_http_sticky.py`

**Interfaces:**
- Produces: `GET /api/sticky/{conv_id}` → JSON `{"widget_type", "data"}` (empty state when unset).

- [ ] **Step 1: Write the failing test**

Create `tests/test_http_sticky.py` modeled on the existing canvas endpoint test (grep `get_canvas_state` / `/api/canvas/` in `tests/`). It should:
- pin a widget via `sticky.set_sticky`, then GET `/api/sticky/{conv}` and assert the JSON carries `widget_type == "markdown_document"`;
- GET an unknown conv and assert empty state (`widget_type` is `null`);
- assert the auth gate matches the canvas endpoint's.

Copy the canvas endpoint test's client/auth fixtures verbatim, swapping the path and assertions.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_http_sticky.py -v`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Implement**

Add near `get_canvas_state` (`http_server.py:1714`), matching its auth decorator/signature exactly:
```python
async def get_sticky_state(request: Request, username: str) -> JSONResponse:
    conv_id = request.path_params["conv_id"]
    from .sticky import read_sticky_state
    state = read_sticky_state(_config_for(request), conv_id)  # match get_canvas_state's config access
    return JSONResponse({
        "widget_type": state.get("widget_type"),
        "data": state.get("data"),
    })
```
(Use the exact auth wrapper + config accessor that `get_canvas_state` uses — copy its decorator line and its way of reaching `config`.)

Register the route near `http_server.py:2084`:
```python
        Route("/api/sticky/{conv_id}", get_sticky_state, methods=["GET"]),
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_http_sticky.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/http_server.py tests/test_http_sticky.py
git commit -m "feat(419): GET /api/sticky/{conv_id} for reload recovery"
```

---

### Task 8: Frontend `sticky-state.js`

Single-slot state module + collapse flag, mirroring `canvas-state.js` (simpler: no tabs, `collapsed` replaces `dismissed`).

**Files:**
- Create: `src/decafclaw/web/static/lib/sticky-state.js`

**Interfaces:**
- Produces (ES module exports): `subscribe(cb) -> unsub`, `currentSnapshot() -> {widgetType, data, collapsed, visible}`, `setActiveConv(convId) -> Promise`, `applyEvent(evt)`, `toggleCollapsed()`.

- [ ] **Step 1: Implement (no JS unit harness — verified via check-js + Task 9 wiring + manual QA)**

Create `src/decafclaw/web/static/lib/sticky-state.js`:
```javascript
/**
 * Sticky-slot state — per-conversation single-widget cache + collapse flag.
 *
 * State per conv: { widgetType, data, collapsed }
 * Snapshot: { widgetType, data, collapsed, visible }
 *
 * `collapsed` persists per-conv in localStorage (sticky-collapsed.{convId});
 * its first value is derived from the viewport (collapsed on mobile ≤639px,
 * expanded on desktop). Survives reload and conv-switch.
 */

const COLLAPSE_KEY_PREFIX = 'sticky-collapsed.';
const MOBILE_MAX = 639;

const _state = { byConv: new Map(), active: null, subscribers: new Set() };

function _collapseKey(convId) { return COLLAPSE_KEY_PREFIX + convId; }

function _loadCollapsed(convId) {
  try {
    const v = localStorage.getItem(_collapseKey(convId));
    if (v === 'true') return true;
    if (v === 'false') return false;
  } catch { /* unavailable */ }
  // First visit: default from viewport.
  return window.matchMedia(`(max-width: ${MOBILE_MAX}px)`).matches;
}

function _saveCollapsed(convId, value) {
  try { localStorage.setItem(_collapseKey(convId), value ? 'true' : 'false'); }
  catch { /* unavailable */ }
}

function _ensure(convId) {
  if (!_state.byConv.has(convId)) {
    _state.byConv.set(convId, {
      widgetType: null, data: null, collapsed: _loadCollapsed(convId),
    });
  }
  return _state.byConv.get(convId);
}

function _publish() {
  const snap = currentSnapshot();
  for (const cb of _state.subscribers) {
    try { cb(snap); } catch (err) { console.error('sticky subscriber failed', err); }
  }
}

export function currentSnapshot() {
  if (!_state.active) {
    return { widgetType: null, data: null, collapsed: false, visible: false };
  }
  const s = _ensure(_state.active);
  return {
    widgetType: s.widgetType,
    data: s.data,
    collapsed: s.collapsed,
    visible: !!s.widgetType,
  };
}

export function subscribe(callback) {
  _state.subscribers.add(callback);
  return () => _state.subscribers.delete(callback);
}

export async function setActiveConv(convId) {
  _state.active = convId;
  if (!convId) { _publish(); return; }
  const s = _ensure(convId);
  try {
    const resp = await fetch(`/api/sticky/${encodeURIComponent(convId)}`,
                             { credentials: 'same-origin' });
    if (resp.ok) {
      const data = await resp.json();
      s.widgetType = data.widget_type || null;
      s.data = data.data || null;
    }
  } catch (err) {
    console.warn('sticky state load failed', err);
  }
  _publish();
}

/** Apply an incoming sticky_set / sticky_clear WS event. */
export function applyEvent(evt) {
  const convId = evt.conv_id;
  if (!convId) return;
  const s = _ensure(convId);
  if (evt.type === 'sticky_set') {
    s.widgetType = evt.widget_type || null;
    s.data = evt.data || null;
  } else if (evt.type === 'sticky_clear') {
    s.widgetType = null;
    s.data = null;
  }
  if (convId === _state.active) _publish();
}

export function toggleCollapsed() {
  if (!_state.active) return;
  const s = _ensure(_state.active);
  s.collapsed = !s.collapsed;
  _saveCollapsed(_state.active, s.collapsed);
  _publish();
}
```

- [ ] **Step 2: Commit**

```bash
git add src/decafclaw/web/static/lib/sticky-state.js
git commit -m "feat(419): sticky-state.js single-slot frontend state"
```

---

### Task 9: `<sticky-slot>` component + wiring + CSS

**Files:**
- Create: `src/decafclaw/web/static/components/sticky-slot.js`
- Create: `src/decafclaw/web/static/styles/sticky.css`
- Modify: `src/decafclaw/web/static/index.html` (insert element between `<chat-view>` :50 and `<chat-input>` :51; add module `<script>` near the canvas-panel one at bottom; ensure `sticky.css` is loaded — add `@import` to whichever aggregate `style.css` imports `styles/canvas.css`, OR a `<link>` in `index.html` head matching how canvas.css is included — grep `canvas.css` to see which)
- Modify: `src/decafclaw/web/static/app.js` (import `applyEvent`/`setActiveConv` from sticky-state; dispatch `STICKY_SET`/`STICKY_CLEAR` near the `CANVAS_UPDATE` branch :513; call `setActiveConv` wherever canvas's `setActiveConv` is called on conversation switch — grep `canvas-state` usage in app.js)

**Interfaces:**
- Consumes: `sticky-state.js` exports; `<dc-widget-host>` (from `components/widgets/widget-host.js`, mounts `dc-widget-<type>` with a `mode` property).

- [ ] **Step 1: Implement the component**

Create `src/decafclaw/web/static/components/sticky-slot.js`:
```javascript
import { LitElement, html, nothing } from 'lit';
import { subscribe, currentSnapshot, toggleCollapsed } from '../lib/sticky-state.js';
import './widgets/widget-host.js';

/**
 * Single-slot pinned widget above the chat input. Expanded shows the widget;
 * collapsed shows a one-line summary. Hidden when nothing is pinned.
 */
class StickySlot extends LitElement {
  // Render into light DOM so global styles/styles/sticky.css apply (match canvas-panel).
  createRenderRoot() { return this; }

  static properties = { _snap: { state: true } };

  constructor() {
    super();
    this._snap = currentSnapshot();
    this._unsub = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._unsub = subscribe((snap) => { this._snap = snap; });
  }

  disconnectedCallback() {
    if (this._unsub) this._unsub();
    super.disconnectedCallback();
  }

  _summaryText() {
    const d = this._snap.data || {};
    if (typeof d.summary === 'string' && d.summary) return d.summary;
    if (typeof d.title === 'string' && d.title) return d.title;
    return (this._snap.widgetType || '').replace(/_/g, ' ');
  }

  render() {
    if (!this._snap.visible) return nothing;
    const collapsed = this._snap.collapsed;
    return html`
      <div class="sticky-slot ${collapsed ? 'sticky-collapsed' : ''}">
        <div class="sticky-header" @click=${() => toggleCollapsed()}>
          <span class="sticky-summary">${this._summaryText()}</span>
          <button class="sticky-toggle dc-icon-btn" aria-label="Toggle sticky panel"
                  @click=${(e) => { e.stopPropagation(); toggleCollapsed(); }}>
            ${collapsed ? '▸' : '▾'}
          </button>
        </div>
        ${collapsed ? nothing : html`
          <div class="sticky-body">
            <dc-widget-host
              .widgetType=${this._snap.widgetType}
              .data=${this._snap.data}
              .mode=${'sticky'}
            ></dc-widget-host>
          </div>`}
      </div>`;
  }
}

customElements.define('sticky-slot', StickySlot);
```
(Verify `<dc-widget-host>`'s property names — `widgetType`, `data`, `mode` — against `components/widgets/widget-host.js`; match them exactly. If the host requires `convId`, pass it from the snapshot's active conv.)

- [ ] **Step 2: CSS**

Create `src/decafclaw/web/static/styles/sticky.css` (quiet, full-width, scroll at max-height; mobile tighter):
```css
.sticky-slot {
  border-top: 1px solid var(--pico-muted-border-color);
  background: var(--pico-card-background-color);
  max-height: 30vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.sticky-slot.sticky-collapsed { max-height: none; }
.sticky-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.25rem 0.75rem;
  cursor: pointer;
  font-size: 0.85rem;
  color: var(--pico-muted-color);
}
.sticky-summary { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sticky-toggle { flex: 0 0 auto; }
.sticky-body { overflow-y: auto; padding: 0 0.75rem 0.5rem; }
@media (max-width: 639px) {
  .sticky-slot { max-height: 25vh; }
}
```
Wire it in the same way `styles/canvas.css` is wired (grep `canvas.css`): if `style.css` does `@import 'styles/canvas.css';`, add `@import 'styles/sticky.css';` there; if `index.html` `<link>`s it, add a matching `<link>`.

- [ ] **Step 3: index.html + app.js wiring**

- `index.html`: insert `<sticky-slot></sticky-slot>` between `<chat-view></chat-view>` and `<chat-input></chat-input>` (lines 50–51); add `<script type="module" src="/static/components/sticky-slot.js"></script>` next to the canvas-panel script at the bottom.
- `app.js`: import `{ applyEvent as stickyApplyEvent, setActiveConv as stickySetActiveConv } from './lib/sticky-state.js';`. Add after the `CANVAS_UPDATE` branch (~:513):
```javascript
  if (msg?.type === MESSAGE_TYPES.STICKY_SET || msg?.type === MESSAGE_TYPES.STICKY_CLEAR) {
    stickyApplyEvent(msg);
  }
```
  And wherever canvas's `setActiveConv(convId)` is called on conversation switch, call `stickySetActiveConv(convId)` alongside it (grep the canvas-state import usage in app.js).

- [ ] **Step 4: Verify JS + build**

Run: `make check-js`
Expected: PASS (tsc --checkJs clean).
Run: `make vendor` only if a new vendored import was added (none expected).

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/web/static/components/sticky-slot.js src/decafclaw/web/static/styles/sticky.css src/decafclaw/web/static/index.html src/decafclaw/web/static/app.js src/decafclaw/web/static/style.css
git commit -m "feat(419): sticky-slot component + wiring + styles"
```

---

### Task 10: Manual QA, docs, CLAUDE.md, full check

**Files:**
- Modify: `docs/web-ui-design.md` (or the canvas doc — pick the one documenting widget surfaces) — add a "Sticky slot" section.
- Modify: `CLAUDE.md` key-files list (add `sticky.py`, `tools/sticky_tools.py`, `web/static/components/sticky-slot.js`, `web/static/lib/sticky-state.js`).
- Modify: session `notes.md` (final summary).

- [ ] **Step 1: Manual QA via a local web-only server**

Per the `reference_ws_smoke_local_run` memory, start a web-only server on the worktree's `HTTP_PORT` (`MATTERMOST_ENABLED=false`), open the UI, and in a conversation:
- Call `widget_pin_sticky(widget_type="markdown_document", data={content:"# Working…\n- step 1\n- step 2"})` → slot appears above input.
- Toggle collapse ▾/▸ → body hides/shows; summary line shows the H1/title.
- Reload the page → slot persists (REST recovery).
- Pin a second widget → replaces the first.
- `widget_unpin_sticky()` → slot disappears.
- Narrow the window ≤639px → starts collapsed.
Record results in `notes.md`.

- [ ] **Step 2: Docs**

Add a "Sticky slot" subsection to the widget-surface doc: what it is, single-slot, display-only, `sticky` mode opt-in, driven by `widget_pin_sticky`/`widget_unpin_sticky` (and, forthcoming in #414, the checklist). Note the collapse/mobile behavior. Regenerated `docs/websocket-messages.md` already covers the new WS types (Task 5).

- [ ] **Step 3: CLAUDE.md key-files**

Add the four new modules to the "Key files" list under the appropriate sections.

- [ ] **Step 4: Full check + test**

Run: `make check` (lint + typecheck + check-js + check-message-types)
Run: `make test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/ CLAUDE.md
git commit -m "docs(419): document sticky slot; update key-files"
```

---

## Self-Review

**Spec coverage** — every spec item maps to a task:
- `sticky` mode value → Task 1. `sticky.json` sidecar → Task 2. `set_sticky`/`clear_sticky` + validation → Task 3. `widget_pin_sticky`/`widget_unpin_sticky` → Task 4. WS types → Task 5. Forwarders → Task 6. REST recovery → Task 7. `sticky-state.js` → Task 8. `<sticky-slot>` + collapse + mobile + wiring + CSS → Task 9. `markdown_document` occupant → Task 1. Docs + key-files → Task 10. Acceptance criteria (pin/unpin/replace/reject/reload/collapse) → covered by tests in Tasks 3, 4, 7 + manual QA in Task 10.
- Out-of-scope (progress_tracker, checklist auto-emit) correctly absent.

**Placeholder scan** — the two "match the existing X" notes (async-test convention in Task 3; auth wrapper/config accessor in Task 7; `dc-widget-host` prop names in Task 9; how `canvas.css` is wired in Task 9) are deliberate: they point the implementer at the exact template to copy rather than guessing an API this plan can't see verbatim. No `TODO`/`TBD`/"add error handling"-style gaps.

**Type consistency** — `set_sticky(config, conv_id, widget_type, data, emit=None)` and `clear_sticky(config, conv_id, emit=None)` are used identically in Tasks 3, 4, 7. WS event dict `{"type":"sticky_set","widget_type","data"}` / `{"type":"sticky_clear"}` (Task 3 emit) matches the forwarder reads (Task 6) and the JS `applyEvent` (Task 8). Snapshot fields `{widgetType,data,collapsed,visible}` consistent between Tasks 8 and 9.
