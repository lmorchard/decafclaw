# Widgets Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 3's single-tab canvas API with explicit-tab-id ops, add a `code_block` widget with highlight.js syntax highlighting (also lit up across existing chat code), surface canvas tabs as a strip on desktop / vertical list on mobile, and add tab-locked standalone URLs.

**Architecture:** Five always-loaded tools (`canvas_new_tab`, `canvas_update`, `canvas_close_tab`, `canvas_clear`, `canvas_read`) operate on tab IDs that the agent receives back from `canvas_new_tab`. Tab strip + ARIA tab pattern in `<canvas-panel>`. Standalone view picks up a `/{tab_id}` URL segment for tab-locked focus mode.

**Tech Stack:** Python (Starlette), Lit (web components), JSON Schema validation, pytest, highlight.js (new vendor dep), Pico CSS.

**Spec:** [`spec.md`](./spec.md)

---

## File Structure

### Server-side (Python)

| File | Responsibility |
|---|---|
| `src/decafclaw/canvas.py` (rewrite) | Add `new_tab` / `update_tab` / `close_tab` / `set_active_tab` / `get_tab`. Drop `set_canvas` / `update_canvas` / `get_active_tab`. Add `next_tab_id` counter with Phase-3 sidecar migration. Extend `_emit_canvas_update` for new `kind` values. |
| `src/decafclaw/tools/canvas_tools.py` (rewrite) | Five tools: `tool_canvas_new_tab` / `tool_canvas_update` / `tool_canvas_close_tab` / `tool_canvas_clear` / `tool_canvas_read`. Drop `tool_canvas_set`. |
| `src/decafclaw/http_server.py` (modify) | Rename `POST .../set` → `POST .../new_tab`. Add `POST .../active_tab`, `POST .../close_tab`. Add `GET /canvas/{conv_id}/{tab_id}` route. |
| `src/decafclaw/web/websocket.py` (modify) | Extend `_make_canvas_update_forwarder` and the inline branch to pass through new `kind` values (`new_tab`, `close_tab`, `set_active`). |

### Frontend

| File | Responsibility |
|---|---|
| `src/decafclaw/web/static/widgets/code_block/widget.json` (new) | Widget descriptor; modes `["inline", "canvas"]`; data: `{code, language?, filename?}`. |
| `src/decafclaw/web/static/widgets/code_block/widget.js` (new) | Lit component; inline (collapse + Expand + Open in Canvas) and canvas (full + scroll preserved) modes. `hljs.highlightElement` in `updated()`. |
| `src/decafclaw/web/static/widgets/markdown_document/widget.js` (modify) | Open-in-Canvas POST URL rename `/set` → `/new_tab`. |
| `src/decafclaw/web/static/lib/canvas-state.js` (modify) | Multi-tab snapshot shape; handle new event kinds; expose tab list + active tab. |
| `src/decafclaw/web/static/components/canvas-panel.js` (modify) | Tab strip render, ARIA tab pattern, keyboard nav, mobile vertical-list disclosure. |
| `src/decafclaw/web/static/canvas-page.js` (modify) | Path-parse `/canvas/{conv}/{tab_id?}`; route WS events per bare-URL vs tab-locked. |
| `src/decafclaw/web/static/styles/canvas.css` (modify) | Tab strip styles, mobile disclosure, hljs theme imports. |
| `src/decafclaw/web/static/components/messages/assistant-message.js` (modify) | Hook `hljs.highlightElement` into existing fenced-code path. |
| `src/decafclaw/web/static/vendor/bundle/highlight.js` (new) | hljs vendor bundle (built via `make vendor`). |
| `src/decafclaw/web/static/styles/hljs-themes.css` (new) | Both atom-one-dark + atom-one-light scoped under `[data-theme]`. |
| `src/decafclaw/web/static/index.html` (modify) | Importmap entry for hljs; link new theme stylesheet. |
| `Makefile` / `scripts/build_vendor.sh` (modify, locate during impl) | Add hljs build step. |

### Tests

| File | Responsibility |
|---|---|
| `tests/test_canvas.py` (rewrite) | Tab-aware persistence + state ops. Phase 3 tests for `set_canvas` / `update_canvas` rewritten to new API. |
| `tests/test_canvas_tools.py` (rewrite) | Five new tool tests. |
| `tests/test_web_canvas.py` (modify) | Renamed `/new_tab`, new `/active_tab`, new `/close_tab`, tab-locked `GET /canvas/{conv}/{tab_id}` routes. WS forwarder for new kinds. |
| `tests/test_widgets.py` (modify) | Add `code_block` to expected widgets. |
| `tests/test_workspace_tools.py` (no changes needed) | `workspace_preview_markdown` from Phase 3 already works. |

### Docs

`docs/widgets.md`, `docs/web-ui.md`, `docs/web-ui-mobile.md`, `docs/conversations.md`, `docs/context-composer.md` — see Task 13.

---
## Task 1: `canvas.py` — `next_tab_id` counter + Phase 3 sidecar migration

**Files:**
- Modify: `src/decafclaw/canvas.py`
- Modify: `tests/test_canvas.py`

Phase 3's `canvas.json` had no counter; tab IDs were always `canvas_1`. To prevent ID reuse across closes (`canvas_3` closed → reopen as `canvas_4`, not `canvas_3`), add a `next_tab_id` counter to the persisted state with backwards-compat for Phase 3 sidecars.

- [ ] **Step 1: Write the failing test for next_tab_id migration**

Append to `tests/test_canvas.py`:

```python
def test_read_phase3_sidecar_synthesizes_next_tab_id(config):
    """A Phase 3 sidecar (no next_tab_id field) gets one synthesized on read."""
    path = canvas._canvas_sidecar_path(config, "phase3conv")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "active_tab": "canvas_2",
        "tabs": [
            {"id": "canvas_1", "label": "L1", "widget_type": "markdown_document", "data": {"content": "a"}},
            {"id": "canvas_2", "label": "L2", "widget_type": "markdown_document", "data": {"content": "b"}},
        ],
    }))
    state = canvas.read_canvas_state(config, "phase3conv")
    assert state["next_tab_id"] == 3


def test_read_empty_state_has_next_tab_id_one(config):
    state = canvas.read_canvas_state(config, "fresh")
    assert state["next_tab_id"] == 1


def test_write_then_read_preserves_next_tab_id(config):
    state = {
        "schema_version": 1,
        "active_tab": "canvas_5",
        "next_tab_id": 7,
        "tabs": [
            {"id": "canvas_5", "label": "L", "widget_type": "markdown_document", "data": {"content": "x"}},
        ],
    }
    canvas.write_canvas_state(config, "c", state)
    got = canvas.read_canvas_state(config, "c")
    assert got["next_tab_id"] == 7


def test_read_phase3_sidecar_with_no_tabs(config):
    """A Phase 3 sidecar with empty tabs gets next_tab_id=1."""
    path = canvas._canvas_sidecar_path(config, "emptyphase3")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "active_tab": None, "tabs": []}))
    state = canvas.read_canvas_state(config, "emptyphase3")
    assert state["next_tab_id"] == 1
```

- [ ] **Step 2: Run, verify failure**

```bash
uv run pytest tests/test_canvas.py -k "next_tab_id or phase3" -v
```
Expected: FAIL — `next_tab_id` not in returned state.

- [ ] **Step 3: Implement migration in `read_canvas_state` and update `empty_canvas_state`**

In `src/decafclaw/canvas.py`, modify `empty_canvas_state`:

```python
def empty_canvas_state() -> dict:
    """Return a fresh empty canvas-state dict."""
    return {"schema_version": 1, "active_tab": None, "next_tab_id": 1, "tabs": []}
```

And modify `read_canvas_state` so a state without `next_tab_id` synthesizes one from the highest existing tab id + 1:

```python
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
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run pytest tests/test_canvas.py -k "next_tab_id or phase3" -v
```
Expected: 4 passing.

- [ ] **Step 5: Run full canvas test suite — no regressions**

```bash
uv run pytest tests/test_canvas.py -v
```
Expected: previously-passing tests still pass; new tests pass too.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check src/decafclaw/canvas.py tests/test_canvas.py
git add src/decafclaw/canvas.py tests/test_canvas.py
git commit -m "feat(canvas): add next_tab_id counter + Phase 3 sidecar migration

Closed-then-reopened tab ids must not rebind (e.g. canvas_3 closed
should yield canvas_4 next, not canvas_3 again). Persist a monotonic
next_tab_id counter; read_canvas_state synthesizes one from existing
tabs when reading a Phase 3 sidecar.

Phase 4 of #256 (#389)."
```

---

## Task 2: `canvas.py` — Add `new_tab` / `update_tab` / `close_tab` / `set_active_tab` / `get_tab`

**Files:**
- Modify: `src/decafclaw/canvas.py` (add new functions alongside existing)
- Modify: `tests/test_canvas.py` (add tests for new ops)

Add the new tab-aware state operations. Old functions (`set_canvas`, `update_canvas`, `get_active_tab`) stay for now — they get deleted in Task 5 once callers migrate. This keeps the test suite green throughout.

Extend `_emit_canvas_update` to support new `kind` values: `"new_tab"`, `"close_tab"`, `"set_active"`.

- [ ] **Step 1: Write failing tests for `new_tab`**

Append to `tests/test_canvas.py`:

```python
@pytest.mark.asyncio
async def test_new_tab_creates_and_activates(config, md_doc_registry, emit_recorder):
    result = await canvas.new_tab(
        config, "c", "markdown_document",
        {"content": "# Doc"}, label="Doc", emit=emit_recorder,
    )
    assert result.ok
    assert result.tab_id == "canvas_1"
    state = canvas.read_canvas_state(config, "c")
    assert state["active_tab"] == "canvas_1"
    assert state["next_tab_id"] == 2
    assert len(state["tabs"]) == 1
    assert state["tabs"][0]["id"] == "canvas_1"
    assert state["tabs"][0]["label"] == "Doc"
    # Event
    assert len(emit_recorder.events) == 1
    _, event = emit_recorder.events[0]
    assert event["kind"] == "new_tab"
    assert event["active_tab"] == "canvas_1"
    assert event["tab"]["id"] == "canvas_1"


@pytest.mark.asyncio
async def test_new_tab_increments_counter_across_close(config, md_doc_registry, emit_recorder):
    """Closing a tab does NOT decrement next_tab_id; ids never reused."""
    r1 = await canvas.new_tab(config, "c", "markdown_document",
                              {"content": "a"}, emit=emit_recorder)
    r2 = await canvas.new_tab(config, "c", "markdown_document",
                              {"content": "b"}, emit=emit_recorder)
    assert r1.tab_id == "canvas_1"
    assert r2.tab_id == "canvas_2"
    await canvas.close_tab(config, "c", "canvas_2", emit=emit_recorder)
    r3 = await canvas.new_tab(config, "c", "markdown_document",
                              {"content": "c"}, emit=emit_recorder)
    assert r3.tab_id == "canvas_3"  # NOT canvas_2 again


@pytest.mark.asyncio
async def test_new_tab_unknown_widget(config, md_doc_registry, emit_recorder):
    result = await canvas.new_tab(
        config, "c", "no_such", {"content": "x"}, emit=emit_recorder,
    )
    assert not result.ok
    assert "not registered" in result.error
    assert canvas.read_canvas_state(config, "c") == canvas.empty_canvas_state()


@pytest.mark.asyncio
async def test_new_tab_invalid_data(config, md_doc_registry, emit_recorder):
    result = await canvas.new_tab(
        config, "c", "markdown_document", {"wrong": 1}, emit=emit_recorder,
    )
    assert not result.ok
    assert "schema validation failed" in result.error
```

- [ ] **Step 2: Write failing tests for `update_tab`**

```python
@pytest.mark.asyncio
async def test_update_tab_by_id(config, md_doc_registry, emit_recorder):
    r1 = await canvas.new_tab(config, "c", "markdown_document",
                              {"content": "v1"}, label="L", emit=emit_recorder)
    emit_recorder.events.clear()
    result = await canvas.update_tab(
        config, "c", r1.tab_id, {"content": "v2"}, emit=emit_recorder,
    )
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    assert state["tabs"][0]["data"]["content"] == "v2"
    assert state["tabs"][0]["label"] == "L"  # preserved
    assert state["tabs"][0]["widget_type"] == "markdown_document"  # preserved
    _, event = emit_recorder.events[0]
    assert event["kind"] == "update"
    assert event["tab"]["id"] == "canvas_1"


@pytest.mark.asyncio
async def test_update_tab_unknown_id(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document",
                         {"content": "x"}, emit=emit_recorder)
    result = await canvas.update_tab(
        config, "c", "canvas_99", {"content": "y"}, emit=emit_recorder,
    )
    assert not result.ok
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_update_tab_invalid_data(config, md_doc_registry, emit_recorder):
    r1 = await canvas.new_tab(config, "c", "markdown_document",
                              {"content": "x"}, emit=emit_recorder)
    result = await canvas.update_tab(
        config, "c", r1.tab_id, {"wrong": 1}, emit=emit_recorder,
    )
    assert not result.ok
    assert "schema validation failed" in result.error
```

- [ ] **Step 3: Write failing tests for `close_tab`**

```python
@pytest.mark.asyncio
async def test_close_tab_active_switches_to_left_neighbor(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document", {"content": "1"}, emit=emit_recorder)
    await canvas.new_tab(config, "c", "markdown_document", {"content": "2"}, emit=emit_recorder)
    await canvas.new_tab(config, "c", "markdown_document", {"content": "3"}, emit=emit_recorder)
    # active is canvas_3 (most recent new_tab)
    emit_recorder.events.clear()
    result = await canvas.close_tab(config, "c", "canvas_3", emit=emit_recorder)
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    assert state["active_tab"] == "canvas_2"  # left neighbor
    assert len(state["tabs"]) == 2
    _, event = emit_recorder.events[0]
    assert event["kind"] == "close_tab"
    assert event["closed_tab_id"] == "canvas_3"
    assert event["active_tab"] == "canvas_2"


@pytest.mark.asyncio
async def test_close_tab_first_switches_to_right(config, md_doc_registry, emit_recorder):
    """Closing the leftmost active tab → right neighbor (no left exists)."""
    r1 = await canvas.new_tab(config, "c", "markdown_document", {"content": "1"}, emit=emit_recorder)
    r2 = await canvas.new_tab(config, "c", "markdown_document", {"content": "2"}, emit=emit_recorder)
    # Manually activate canvas_1
    await canvas.set_active_tab(config, "c", r1.tab_id, emit=emit_recorder)
    emit_recorder.events.clear()
    await canvas.close_tab(config, "c", r1.tab_id, emit=emit_recorder)
    state = canvas.read_canvas_state(config, "c")
    assert state["active_tab"] == "canvas_2"


@pytest.mark.asyncio
async def test_close_tab_non_active(config, md_doc_registry, emit_recorder):
    """Closing a non-active tab leaves the active tab unchanged."""
    await canvas.new_tab(config, "c", "markdown_document", {"content": "1"}, emit=emit_recorder)
    await canvas.new_tab(config, "c", "markdown_document", {"content": "2"}, emit=emit_recorder)
    # active is canvas_2
    await canvas.close_tab(config, "c", "canvas_1", emit=emit_recorder)
    state = canvas.read_canvas_state(config, "c")
    assert state["active_tab"] == "canvas_2"
    assert len(state["tabs"]) == 1
    assert state["tabs"][0]["id"] == "canvas_2"


@pytest.mark.asyncio
async def test_close_last_tab_clears_active(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document", {"content": "1"}, emit=emit_recorder)
    emit_recorder.events.clear()
    await canvas.close_tab(config, "c", "canvas_1", emit=emit_recorder)
    state = canvas.read_canvas_state(config, "c")
    assert state["active_tab"] is None
    assert state["tabs"] == []
    _, event = emit_recorder.events[0]
    assert event["kind"] == "close_tab"
    assert event["active_tab"] is None
    assert event["tab"] is None


@pytest.mark.asyncio
async def test_close_tab_unknown_id(config, md_doc_registry, emit_recorder):
    result = await canvas.close_tab(config, "c", "canvas_99", emit=emit_recorder)
    assert not result.ok
    assert "not found" in result.error
```

- [ ] **Step 4: Write failing tests for `set_active_tab` and `get_tab`**

```python
@pytest.mark.asyncio
async def test_set_active_tab(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document", {"content": "1"}, emit=emit_recorder)
    await canvas.new_tab(config, "c", "markdown_document", {"content": "2"}, emit=emit_recorder)
    emit_recorder.events.clear()
    result = await canvas.set_active_tab(config, "c", "canvas_1", emit=emit_recorder)
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    assert state["active_tab"] == "canvas_1"
    _, event = emit_recorder.events[0]
    assert event["kind"] == "set_active"
    assert event["active_tab"] == "canvas_1"


@pytest.mark.asyncio
async def test_set_active_tab_unknown_id(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document", {"content": "1"}, emit=emit_recorder)
    result = await canvas.set_active_tab(config, "c", "canvas_99", emit=emit_recorder)
    assert not result.ok
    assert "not found" in result.error


def test_get_tab_by_id(config, md_doc_registry, emit_recorder):
    import asyncio
    asyncio.run(canvas.new_tab(config, "c", "markdown_document",
                               {"content": "x"}, label="L", emit=emit_recorder))
    tab = canvas.get_tab(config, "c", "canvas_1")
    assert tab is not None
    assert tab["label"] == "L"
    assert canvas.get_tab(config, "c", "canvas_99") is None
```

- [ ] **Step 5: Run, verify all new tests fail**

```bash
uv run pytest tests/test_canvas.py -k "new_tab or update_tab or close_tab or set_active_tab or get_tab" -v
```
Expected: All fail (functions don't exist).

- [ ] **Step 6: Implement the new state ops**

In `src/decafclaw/canvas.py`, replace `CanvasOpResult` with an extended version (adds `tab_id`):

```python
@dataclass
class CanvasOpResult:
    """Outcome of a canvas state operation."""
    ok: bool
    text: str = ""
    error: str = ""
    tab_id: str | None = None
```

Add the new state ops (before the existing `set_canvas`):

```python
def get_tab(config, conv_id: str, tab_id: str) -> dict | None:
    """Return a specific tab dict by id, or None if not found."""
    state = read_canvas_state(config, conv_id)
    for tab in state.get("tabs", []):
        if tab.get("id") == tab_id:
            return tab
    return None


async def _emit_canvas_update_kind(emit: EmitFn | None,
                                   conv_id: str,
                                   kind: str,
                                   *,
                                   active_tab: str | None,
                                   tab: dict | None = None,
                                   closed_tab_id: str | None = None) -> None:
    """Publish a canvas_update event with explicit kind + payload fields.

    Phase 4 generalisation of _emit_canvas_update — old function still
    used by the Phase 3 set_canvas/update_canvas/clear_canvas wrappers
    until those are deleted in Task 5.
    """
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
    await _emit_canvas_update_kind(emit, conv_id, "new_tab",
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
            tab["data"] = data
            if not write_canvas_state(config, conv_id, state):
                return CanvasOpResult(ok=False,
                                      error="failed to write canvas state to disk")
            await _emit_canvas_update_kind(emit, conv_id, "update",
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
    await _emit_canvas_update_kind(emit, conv_id, "close_tab",
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
    await _emit_canvas_update_kind(emit, conv_id, "set_active",
                                   active_tab=tab_id, tab=None)
    return CanvasOpResult(ok=True, text=f"active tab set to {tab_id}")
```

- [ ] **Step 7: Run new tests, verify pass**

```bash
uv run pytest tests/test_canvas.py -k "new_tab or update_tab or close_tab or set_active_tab or get_tab" -v
```
Expected: All passing.

- [ ] **Step 8: Run full canvas test suite — old + new ops both work**

```bash
uv run pytest tests/test_canvas.py -v
```
Expected: All passing.

- [ ] **Step 9: Lint + commit**

```bash
uv run ruff check src/decafclaw/canvas.py tests/test_canvas.py
git add src/decafclaw/canvas.py tests/test_canvas.py
git commit -m "feat(canvas): add tab-aware state ops alongside Phase 3 functions

new_tab / update_tab / close_tab / set_active_tab / get_tab operate
on explicit tab ids. Old set_canvas / update_canvas / get_active_tab
stay for now — callers migrate in Tasks 3 + 4, dead code removed in
Task 5. Extends emit with kind values: new_tab, close_tab, set_active.

Phase 4 of #256 (#389)."
```

---
## Task 3: Rewrite `tools/canvas_tools.py` for the tab API

**Files:**
- Modify: `src/decafclaw/tools/canvas_tools.py` (rewrite)
- Modify: `tests/test_canvas_tools.py` (rewrite)

Five tools using the new state ops from Task 2. Phase 3's `tool_canvas_set` and the no-id `tool_canvas_update` are removed; tests rewritten to the new API.

- [ ] **Step 1: Rewrite `tests/test_canvas_tools.py`**

Replace the existing file with:

```python
"""Tests for canvas_* agent tools."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    cfg = SimpleNamespace(workspace_path=tmp_path / "workspace")
    cfg.workspace_path.mkdir()
    return cfg


@pytest.fixture
def md_doc_registry(monkeypatch):
    from decafclaw import canvas as canvas_mod

    class _Reg:
        _d = {
            "markdown_document": SimpleNamespace(
                modes=["inline", "canvas"], required=["content"]
            ),
        }

        def get(self, name): return self._d.get(name)

        def validate(self, name, data):
            d = self._d.get(name)
            if not d: return False, "unknown"
            for r in getattr(d, "required", []):
                if r not in data: return False, f"missing {r}"
            return True, None

    monkeypatch.setattr(canvas_mod, "get_widget_registry", lambda: _Reg())


@pytest.fixture
def manager_mock():
    m = MagicMock()
    m.emit = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_canvas_new_tab_returns_tab_id(config, md_doc_registry, manager_mock):
    ctx = _make_ctx(config, manager_mock)
    result = await canvas_tools.tool_canvas_new_tab(
        ctx, "markdown_document", {"content": "# Hi"},
    )
    assert isinstance(result, ToolResult)
    assert result.data["tab_id"] == "canvas_1"
    assert "/canvas/conv1/canvas_1" in result.text
    manager_mock.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_canvas_new_tab_unknown_widget(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_new_tab(
        ctx, "no_such", {"content": "x"},
    )
    assert result.text.startswith("[error: ")
    assert "not registered" in result.text


@pytest.mark.asyncio
async def test_canvas_update_targets_explicit_id(config, md_doc_registry, manager_mock):
    ctx = _make_ctx(config, manager_mock)
    r1 = await canvas_tools.tool_canvas_new_tab(
        ctx, "markdown_document", {"content": "v1"},
    )
    tab_id = r1.data["tab_id"]
    result = await canvas_tools.tool_canvas_update(ctx, tab_id, {"content": "v2"})
    assert "updated" in result.text.lower()
    assert "[error" not in result.text


@pytest.mark.asyncio
async def test_canvas_update_unknown_id(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_update(ctx, "canvas_99", {"content": "x"})
    assert result.text.startswith("[error: ")
    assert "not found" in result.text


@pytest.mark.asyncio
async def test_canvas_close_tab_returns_new_active(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    await canvas_tools.tool_canvas_new_tab(ctx, "markdown_document", {"content": "1"})
    await canvas_tools.tool_canvas_new_tab(ctx, "markdown_document", {"content": "2"})
    # active is canvas_2
    result = await canvas_tools.tool_canvas_close_tab(ctx, "canvas_2")
    assert "active=canvas_1" in result.text


@pytest.mark.asyncio
async def test_canvas_close_last_tab_hides_panel(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    await canvas_tools.tool_canvas_new_tab(ctx, "markdown_document", {"content": "1"})
    result = await canvas_tools.tool_canvas_close_tab(ctx, "canvas_1")
    assert "no tabs left" in result.text


@pytest.mark.asyncio
async def test_canvas_close_tab_unknown_id(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_close_tab(ctx, "canvas_99")
    assert result.text.startswith("[error: ")


@pytest.mark.asyncio
async def test_canvas_clear_when_empty(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_clear(ctx)
    assert result.text == "canvas already empty"


@pytest.mark.asyncio
async def test_canvas_clear_with_tabs(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    await canvas_tools.tool_canvas_new_tab(ctx, "markdown_document", {"content": "1"})
    await canvas_tools.tool_canvas_new_tab(ctx, "markdown_document", {"content": "2"})
    result = await canvas_tools.tool_canvas_clear(ctx)
    assert result.text == "canvas cleared"


@pytest.mark.asyncio
async def test_canvas_read_empty(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_read(ctx)
    assert result.data == {"active_tab": None, "tabs": []}


@pytest.mark.asyncio
async def test_canvas_read_full_state(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    await canvas_tools.tool_canvas_new_tab(
        ctx, "markdown_document", {"content": "a"}, label="A",
    )
    await canvas_tools.tool_canvas_new_tab(
        ctx, "markdown_document", {"content": "b"}, label="B",
    )
    result = await canvas_tools.tool_canvas_read(ctx)
    assert result.data["active_tab"] == "canvas_2"
    assert len(result.data["tabs"]) == 2
    assert result.data["tabs"][0]["label"] == "A"
    assert result.data["tabs"][1]["label"] == "B"


def test_tools_registered_as_always_loaded():
    from decafclaw.tools import TOOLS, TOOL_DEFINITIONS
    expected = {"canvas_new_tab", "canvas_update", "canvas_close_tab",
                "canvas_clear", "canvas_read"}
    for name in expected:
        assert name in TOOLS, f"{name} missing from TOOLS"
    names = {d["function"]["name"] for d in TOOL_DEFINITIONS
             if d.get("type") == "function"}
    assert expected.issubset(names)
    # Old Phase 3 tool removed
    assert "canvas_set" not in TOOLS


@pytest.mark.asyncio
async def test_canvas_new_tab_url_uses_explicit_form(config, md_doc_registry, manager_mock):
    """Returned URL uses /canvas/{conv}/{tab_id} not bare /canvas/{conv}."""
    ctx = _make_ctx(config, manager_mock)
    result = await canvas_tools.tool_canvas_new_tab(
        ctx, "markdown_document", {"content": "x"},
    )
    assert f"/canvas/conv1/{result.data['tab_id']}" in result.text
```

- [ ] **Step 2: Run, verify failure**

```bash
uv run pytest tests/test_canvas_tools.py -v
```
Expected: many failures — old `tool_canvas_set` tests gone, new tools don't exist yet.

- [ ] **Step 3: Rewrite `src/decafclaw/tools/canvas_tools.py`**

Replace the file with:

```python
"""Agent-facing canvas tools — tab-aware (Phase 4).

Five tools that operate on explicit tab IDs. canvas_new_tab returns an
auto-generated tab_id; subsequent canvas_update / canvas_close_tab
target by id. canvas_clear nukes everything; canvas_read returns the
full state for grounding.
"""

import logging
from urllib.parse import quote

from .. import canvas as canvas_mod
from ..media import ToolResult

log = logging.getLogger(__name__)


def _emit_for_ctx(ctx):
    manager = getattr(ctx, "manager", None)
    if manager is None:
        return None
    return manager.emit


def _canvas_url(conv_id: str, tab_id: str | None = None) -> str:
    base = f"/canvas/{quote(conv_id, safe='')}"
    if tab_id:
        return f"{base}/{quote(tab_id, safe='')}"
    return base


async def tool_canvas_new_tab(ctx,
                              widget_type: str,
                              data: dict,
                              label: str | None = None) -> ToolResult:
    """Create a new canvas tab and make it active."""
    log.info("[tool:canvas_new_tab] widget=%s label=%r", widget_type, label)
    result = await canvas_mod.new_tab(
        ctx.config, ctx.conv_id, widget_type, data,
        label=label, emit=_emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    url = _canvas_url(ctx.conv_id, result.tab_id)
    return ToolResult(
        text=f"tab created (id={result.tab_id}) — view at {url}",
        data={"tab_id": result.tab_id},
    )


async def tool_canvas_update(ctx, tab_id: str, data: dict) -> ToolResult:
    """Replace data of an existing tab. Preserves widget_type + label."""
    log.info("[tool:canvas_update] tab=%s", tab_id)
    result = await canvas_mod.update_tab(
        ctx.config, ctx.conv_id, tab_id, data, emit=_emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_canvas_close_tab(ctx, tab_id: str) -> ToolResult:
    """Close a single tab by id. If it was active, the panel switches or hides."""
    log.info("[tool:canvas_close_tab] tab=%s", tab_id)
    result = await canvas_mod.close_tab(
        ctx.config, ctx.conv_id, tab_id, emit=_emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_canvas_clear(ctx) -> ToolResult:
    """Close all canvas tabs and hide the panel."""
    log.info("[tool:canvas_clear]")
    state = canvas_mod.read_canvas_state(ctx.config, ctx.conv_id)
    if not state.get("tabs"):
        return ToolResult(text="canvas already empty")
    # Reuse canvas_mod.clear_canvas (existing) — emits kind="clear".
    result = await canvas_mod.clear_canvas(
        ctx.config, ctx.conv_id, emit=_emit_for_ctx(ctx),
    )
    if not result.ok:
        return ToolResult(text=f"[error: {result.error}]")
    return ToolResult(text=result.text)


async def tool_canvas_read(ctx) -> ToolResult:
    """Return the full canvas state including all tabs and active_tab."""
    log.info("[tool:canvas_read]")
    state = canvas_mod.read_canvas_state(ctx.config, ctx.conv_id)
    payload = {
        "active_tab": state.get("active_tab"),
        "tabs": [
            {
                "id": t["id"],
                "label": t.get("label", ""),
                "widget_type": t["widget_type"],
                "data": t.get("data", {}),
            }
            for t in state.get("tabs", [])
        ],
    }
    if not payload["tabs"]:
        text = "canvas is empty (no tabs)"
    else:
        labels = ", ".join(f"{t['id']}({t['label']})" for t in payload["tabs"])
        text = f"canvas has {len(payload['tabs'])} tab(s): {labels}; active={payload['active_tab']}"
    return ToolResult(text=text, data=payload)


CANVAS_TOOLS = {
    "canvas_new_tab": tool_canvas_new_tab,
    "canvas_update": tool_canvas_update,
    "canvas_close_tab": tool_canvas_close_tab,
    "canvas_clear": tool_canvas_clear,
    "canvas_read": tool_canvas_read,
}


CANVAS_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_new_tab",
            "description": (
                "Create a new tab on the conversation's canvas and make it the "
                "active tab. The canvas is a persistent display surface in the "
                "user's web UI — use it for documents, plans, or visualizations "
                "you intend to revise across multiple turns. Returns a tab_id "
                "you MUST keep to target this tab in subsequent canvas_update "
                "or canvas_close_tab calls. Currently supports widget_type='markdown_document' "
                "with data={content: <markdown>} and widget_type='code_block' "
                "with data={code: <string>, language?: <string>, filename?: <string>}."
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
                        "description": "Optional tab label. Defaults to first H1 of content for markdown_document, filename for code_block, else humanized widget_type.",
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
                "Replace the data of an existing canvas tab. Pass the tab_id "
                "you got from canvas_new_tab. Preserves widget_type and label. "
                "Use for revising a document — the panel updates without "
                "re-mounting the widget; scroll position is preserved. Errors "
                "if tab_id doesn't exist (use canvas_read to list current tabs)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tab_id": {
                        "type": "string",
                        "description": "Tab id from canvas_new_tab (e.g. 'canvas_2').",
                    },
                    "data": {
                        "type": "object",
                        "description": "New data payload; must match the tab's widget data_schema.",
                    },
                },
                "required": ["tab_id", "data"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_close_tab",
            "description": (
                "Close a single canvas tab by id. If it was the active tab, "
                "the panel switches to the left neighbor (else right; else "
                "hides). To replace a tab with a different widget_type, "
                "canvas_close_tab the old one and canvas_new_tab the new one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tab_id": {
                        "type": "string",
                        "description": "Tab id to close.",
                    },
                },
                "required": ["tab_id"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "canvas_clear",
            "description": (
                "Close ALL canvas tabs and hide the panel. Use as a 'reset' "
                "when you're done with the canvas entirely. To close one tab, "
                "use canvas_close_tab instead."
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
                "Return the current canvas state — list of tabs (with id, "
                "label, widget_type, data) and the active_tab id. Use to "
                "ground revisions in current canvas state, especially after "
                "compaction or when you've lost track of tab ids."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_canvas_tools.py -v
```
Expected: All passing (~12 tests).

- [ ] **Step 5: Run full suite — http_server.py still imports `set_canvas` so test_web_canvas.py will fail; that's expected, fixed in Task 4**

```bash
uv run pytest tests/test_canvas.py tests/test_canvas_tools.py -v
```
Expected: pass. The full suite WILL fail in test_web_canvas.py until Task 4 lands; document this in the commit but don't run full suite yet.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check src/decafclaw/tools/canvas_tools.py tests/test_canvas_tools.py
git add src/decafclaw/tools/canvas_tools.py tests/test_canvas_tools.py
git commit -m "feat(canvas): rewrite canvas_tools for tab-aware API

Five tools using new state ops: canvas_new_tab returns tab_id,
canvas_update / canvas_close_tab take explicit tab_id, canvas_clear
nukes all, canvas_read returns full state. Phase 3 canvas_set tool
removed; tool descriptions emphasize tab-id workflow.

http_server.py still imports the old REST endpoint helpers — Task 4
migrates those.

Phase 4 of #256 (#389)."
```

---

## Task 4: REST endpoints + websocket forwarder for new event kinds

**Files:**
- Modify: `src/decafclaw/http_server.py`
- Modify: `src/decafclaw/web/websocket.py`
- Modify: `tests/test_web_canvas.py`

Rename Phase 3's `POST .../set` to `POST .../new_tab`. Add `POST .../active_tab` and `POST .../close_tab`. Add `GET /canvas/{conv_id}/{tab_id}`. Extend the WebSocket forwarder to handle new event kinds.

- [ ] **Step 1: Update `tests/test_web_canvas.py`** — rename and add tests

In `tests/test_web_canvas.py`, rename existing `test_post_canvas_set_*` to `test_post_canvas_new_tab_*` (replace `/set` URL paths with `/new_tab`, replace `widget_type` body assertions with `tab_id` response assertions). Add new tests:

```python
@pytest.mark.asyncio
async def test_post_active_tab_changes_active(authed_client, manager_mock, owned_conv):
    # Seed: create two tabs
    r1 = await authed_client.post(
        f"/api/canvas/{owned_conv}/new_tab",
        json={"widget_type": "markdown_document", "data": {"content": "a"}},
    )
    r2 = await authed_client.post(
        f"/api/canvas/{owned_conv}/new_tab",
        json={"widget_type": "markdown_document", "data": {"content": "b"}},
    )
    assert r2.status_code == 200
    # Switch active back to canvas_1
    resp = await authed_client.post(
        f"/api/canvas/{owned_conv}/active_tab",
        json={"tab_id": "canvas_1"},
    )
    assert resp.status_code == 200
    follow = await authed_client.get(f"/api/canvas/{owned_conv}")
    state = follow.json()
    assert state["active_tab"] == "canvas_1"


@pytest.mark.asyncio
async def test_post_active_tab_unknown_id_400(authed_client, owned_conv):
    resp = await authed_client.post(
        f"/api/canvas/{owned_conv}/active_tab",
        json={"tab_id": "canvas_99"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_close_tab(authed_client, manager_mock, owned_conv):
    await authed_client.post(
        f"/api/canvas/{owned_conv}/new_tab",
        json={"widget_type": "markdown_document", "data": {"content": "a"}},
    )
    await authed_client.post(
        f"/api/canvas/{owned_conv}/new_tab",
        json={"widget_type": "markdown_document", "data": {"content": "b"}},
    )
    resp = await authed_client.post(
        f"/api/canvas/{owned_conv}/close_tab",
        json={"tab_id": "canvas_2"},
    )
    assert resp.status_code == 200
    follow = await authed_client.get(f"/api/canvas/{owned_conv}")
    state = follow.json()
    assert state["active_tab"] == "canvas_1"
    assert len(state["tabs"]) == 1


@pytest.mark.asyncio
async def test_post_close_tab_unknown_id_400(authed_client, owned_conv):
    resp = await authed_client.post(
        f"/api/canvas/{owned_conv}/close_tab",
        json={"tab_id": "canvas_99"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_active_tab_other_user_conv_404(authed_client, other_user_conv):
    resp = await authed_client.post(
        f"/api/canvas/{other_user_conv}/active_tab",
        json={"tab_id": "canvas_1"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_close_tab_other_user_conv_404(authed_client, other_user_conv):
    resp = await authed_client.post(
        f"/api/canvas/{other_user_conv}/close_tab",
        json={"tab_id": "canvas_1"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_standalone_canvas_with_tab_id(authed_client, owned_conv):
    resp = await authed_client.get(f"/canvas/{owned_conv}/canvas_2")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "<dc-widget-host" in resp.text


@pytest.mark.asyncio
async def test_get_standalone_canvas_with_tab_id_other_user_404(
    authed_client, other_user_conv,
):
    resp = await authed_client.get(f"/canvas/{other_user_conv}/canvas_1")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_canvas_update_event_kind_new_tab():
    """The forwarder passes through kind=new_tab + closed_tab_id field."""
    sent = []

    async def ws_send(payload):
        sent.append(payload)

    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_canvas_update_forwarder(state, conv_id="conv-x")
    await callback({
        "type": "canvas_update", "conv_id": "conv-x",
        "kind": "new_tab", "active_tab": "canvas_3",
        "tab": {"id": "canvas_3", "label": "L", "widget_type": "code_block", "data": {"code": "x"}},
    })
    assert sent[0]["kind"] == "new_tab"
    assert sent[0]["tab"]["id"] == "canvas_3"


@pytest.mark.asyncio
async def test_canvas_update_event_kind_close_tab():
    sent = []
    async def ws_send(payload):
        sent.append(payload)
    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_canvas_update_forwarder(state, conv_id="conv-x")
    await callback({
        "type": "canvas_update", "conv_id": "conv-x",
        "kind": "close_tab", "active_tab": "canvas_2",
        "tab": None, "closed_tab_id": "canvas_3",
    })
    assert sent[0]["kind"] == "close_tab"
    assert sent[0]["closed_tab_id"] == "canvas_3"


@pytest.mark.asyncio
async def test_canvas_update_event_kind_set_active():
    sent = []
    async def ws_send(payload):
        sent.append(payload)
    state = {"ws_send": ws_send, "config": None}
    callback = ws_mod._make_canvas_update_forwarder(state, conv_id="conv-x")
    await callback({
        "type": "canvas_update", "conv_id": "conv-x",
        "kind": "set_active", "active_tab": "canvas_1", "tab": None,
    })
    assert sent[0]["kind"] == "set_active"
    assert sent[0]["active_tab"] == "canvas_1"
```

- [ ] **Step 2: Run, verify failures**

```bash
uv run pytest tests/test_web_canvas.py -v
```
Expected: failures for the new endpoints + new event kinds.

- [ ] **Step 3: Update `src/decafclaw/web/websocket.py`** — extend the forwarder

In `_make_canvas_update_forwarder` (and the inline branch in `_subscribe_to_conv`'s `on_conv_event`), pass through the new payload fields:

```python
def _make_canvas_update_forwarder(state, conv_id):
    """Build a coroutine that forwards canvas_update events to ws_send."""
    ws_send = state["ws_send"]

    async def _forward(event):
        if event.get("type") != "canvas_update":
            return
        if event.get("conv_id") != conv_id:
            return
        out = {
            "type": "canvas_update",
            "conv_id": conv_id,
            "kind": event.get("kind", "set"),
            "active_tab": event.get("active_tab"),
            "tab": event.get("tab"),
        }
        # New Phase 4 field for kind=close_tab
        if "closed_tab_id" in event:
            out["closed_tab_id"] = event["closed_tab_id"]
        await ws_send(out)

    return _forward
```

And the inline branch in `on_conv_event` — same shape:

```python
        elif event_type == "canvas_update":
            if event_conv_id == conv_id:
                payload = {
                    "type": "canvas_update",
                    "conv_id": event_conv_id,
                    "kind": event.get("kind", "set"),
                    "active_tab": event.get("active_tab"),
                    "tab": event.get("tab"),
                }
                if "closed_tab_id" in event:
                    payload["closed_tab_id"] = event["closed_tab_id"]
                await ws_send(payload)
```

- [ ] **Step 4: Update `src/decafclaw/http_server.py`** — rename + add endpoints

Find the canvas route block (currently has `get_canvas_state`, `post_canvas_set`, `get_canvas_page` plus `_user_owns_conv`). Update:

- Rename `post_canvas_set` to `post_canvas_new_tab`. The handler body changes to call `canvas_mod.new_tab` and return `{"ok": True, "tab_id": result.tab_id}`.
- Add `post_canvas_active_tab` and `post_canvas_close_tab` handlers.
- Add a separate `get_canvas_page_with_tab` handler for `/canvas/{conv_id}/{tab_id}`. (Or accept the path param and serve the same HTML — the page controller picks the tab_id off the URL.)

Replace the canvas-routes block with:

```python
    @_authenticated
    async def get_canvas_state(request: Request, username: str) -> JSONResponse:
        """Load current canvas state for a conversation."""
        from . import canvas as canvas_mod
        conv_id = request.path_params.get("conv_id", "")
        if not _is_safe_conv_id(conv_id):
            return JSONResponse({"error": "invalid conv_id"}, status_code=400)
        if not _user_owns_conv(conv_id, username):
            return JSONResponse({"error": "not found"}, status_code=404)
        state = canvas_mod.read_canvas_state(config, conv_id)
        return JSONResponse(state)

    @_authenticated
    async def post_canvas_new_tab(request: Request, username: str) -> JSONResponse:
        """Create a new canvas tab. Backs the inline 'Open in Canvas' button."""
        from . import canvas as canvas_mod
        conv_id = request.path_params.get("conv_id", "")
        if not _is_safe_conv_id(conv_id):
            return JSONResponse({"error": "invalid conv_id"}, status_code=400)
        if not _user_owns_conv(conv_id, username):
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        widget_type = body.get("widget_type", "")
        data = body.get("data") or {}
        label = body.get("label")
        emit = manager.emit if manager else None
        result = await canvas_mod.new_tab(
            config, conv_id, widget_type, data, label=label, emit=emit,
        )
        if not result.ok:
            return JSONResponse({"error": result.error}, status_code=400)
        return JSONResponse({"ok": True, "tab_id": result.tab_id})

    @_authenticated
    async def post_canvas_active_tab(request: Request, username: str) -> JSONResponse:
        """Set the active tab via user click in the panel."""
        from . import canvas as canvas_mod
        conv_id = request.path_params.get("conv_id", "")
        if not _is_safe_conv_id(conv_id):
            return JSONResponse({"error": "invalid conv_id"}, status_code=400)
        if not _user_owns_conv(conv_id, username):
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        tab_id = body.get("tab_id", "")
        emit = manager.emit if manager else None
        result = await canvas_mod.set_active_tab(config, conv_id, tab_id, emit=emit)
        if not result.ok:
            return JSONResponse({"error": result.error}, status_code=400)
        return JSONResponse({"ok": True})

    @_authenticated
    async def post_canvas_close_tab(request: Request, username: str) -> JSONResponse:
        """Close a tab via user [×] click."""
        from . import canvas as canvas_mod
        conv_id = request.path_params.get("conv_id", "")
        if not _is_safe_conv_id(conv_id):
            return JSONResponse({"error": "invalid conv_id"}, status_code=400)
        if not _user_owns_conv(conv_id, username):
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
        tab_id = body.get("tab_id", "")
        emit = manager.emit if manager else None
        result = await canvas_mod.close_tab(config, conv_id, tab_id, emit=emit)
        if not result.ok:
            return JSONResponse({"error": result.error}, status_code=400)
        return JSONResponse({"ok": True})

    @_authenticated
    async def get_canvas_page(request: Request, username: str):
        """Serve the standalone canvas HTML (bare or tab-locked URL)."""
        from starlette.responses import Response
        conv_id = request.path_params.get("conv_id", "")
        tab_id = request.path_params.get("tab_id", "")  # may be None for bare URL
        if not _is_safe_conv_id(conv_id):
            return Response("Invalid conversation id", status_code=400)
        if tab_id and not _is_safe_conv_id(tab_id):
            return Response("Invalid tab id", status_code=400)
        if not _user_owns_conv(conv_id, username):
            return Response("Not found", status_code=404)
        html_path = Path(__file__).parent / "web" / "static" / "canvas-page.html"
        return Response(html_path.read_text(), media_type="text/html")
```

Update the `routes` list:

```python
        Route("/api/canvas/{conv_id}", get_canvas_state, methods=["GET"]),
        Route("/api/canvas/{conv_id}/new_tab", post_canvas_new_tab, methods=["POST"]),
        Route("/api/canvas/{conv_id}/active_tab", post_canvas_active_tab, methods=["POST"]),
        Route("/api/canvas/{conv_id}/close_tab", post_canvas_close_tab, methods=["POST"]),
        Route("/canvas/{conv_id}", get_canvas_page, methods=["GET"]),
        Route("/canvas/{conv_id}/{tab_id}", get_canvas_page, methods=["GET"]),
```

(Remove the old `/set` route.)

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_web_canvas.py -v
uv run pytest tests/ -x -q
```
Expected: all passing.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check src/decafclaw/http_server.py src/decafclaw/web/websocket.py tests/test_web_canvas.py
git add src/decafclaw/http_server.py src/decafclaw/web/websocket.py tests/test_web_canvas.py
git commit -m "feat(web): tab-aware REST endpoints + WS event kinds

POST .../new_tab (renamed from /set) returns tab_id; new POST
.../active_tab and POST .../close_tab cover user-side click actions.
GET /canvas/{conv}/{tab_id} serves the standalone HTML for tab-locked
viewing. WS forwarder passes through new kind values (new_tab,
close_tab, set_active) plus the closed_tab_id field.

Phase 4 of #256 (#389)."
```

---

## Task 5: Delete unused Phase 3 functions in `canvas.py`

**Files:**
- Modify: `src/decafclaw/canvas.py` (delete `set_canvas`, `update_canvas`, `clear_canvas`'s old `set` event, `get_active_tab`, `_emit_canvas_update`)

Now that `canvas_tools.py` and `http_server.py` use the new state ops, the old Phase 3 functions are dead. Delete them.

Note: `clear_canvas` is KEPT — it's used by `tool_canvas_clear` and emits `kind="clear"` which is unchanged. Just the older internal `set_canvas` / `update_canvas` go.

- [ ] **Step 1: Verify nothing imports the old functions**

```bash
grep -rn "from decafclaw.canvas import\|canvas_mod\.set_canvas\|canvas_mod\.update_canvas\|canvas_mod\.get_active_tab\|canvas_mod\._emit_canvas_update" src/ tests/
```
Expected: only references should be in the file we're modifying (canvas.py itself) and the tests we're keeping.

If `tests/test_canvas.py` still has tests for `set_canvas`, delete them in this step (Phase 3 tests rewritten in Tasks 1-2).

- [ ] **Step 2: Delete dead functions from `canvas.py`**

Remove from `src/decafclaw/canvas.py`:
- `set_canvas(config, conv_id, widget_type, data, label?, emit?)` — superseded by `new_tab`.
- `update_canvas(config, conv_id, data, emit?)` — superseded by `update_tab`.
- `get_active_tab(config, conv_id)` — superseded by `get_tab`. (NOTE: if you find any caller still using it, migrate them to `get_tab` with the active_tab id.)
- `_emit_canvas_update` — superseded by `_emit_canvas_update_kind`. Rename `_emit_canvas_update_kind` → `_emit_canvas_update` to keep the public name, and update `clear_canvas` to use it.

After cleanup, the public surface of `canvas.py` should be:

```
empty_canvas_state, _canvas_sidecar_path, _derive_next_tab_id,
read_canvas_state, write_canvas_state,
CanvasOpResult, _humanize, _derive_label, _validate_widget_for_canvas,
_emit_canvas_update,
get_tab,
new_tab, update_tab, close_tab, set_active_tab,
clear_canvas
```

- [ ] **Step 3: Run all tests — green**

```bash
uv run pytest tests/ -x -q
```
Expected: all passing.

- [ ] **Step 4: Lint + commit**

```bash
uv run ruff check src/decafclaw/canvas.py
git add src/decafclaw/canvas.py tests/test_canvas.py
git commit -m "refactor(canvas): drop Phase 3 single-tab functions

set_canvas, update_canvas, get_active_tab, and _emit_canvas_update
were superseded by tab-aware ops in Tasks 1-2; canvas_tools.py and
http_server.py now use the new functions exclusively. Rename
_emit_canvas_update_kind back to _emit_canvas_update for cleanliness.

Phase 4 of #256 (#389)."
```

---
## Task 6: highlight.js vendor bundle

**Files:**
- Modify: `Makefile` (or `scripts/build_vendor.*`, locate during impl)
- Create: `src/decafclaw/web/static/vendor/bundle/highlight.js`
- Create: `src/decafclaw/web/static/styles/hljs-themes.css`
- Modify: `src/decafclaw/web/static/index.html` (importmap entry)
- Modify: `src/decafclaw/web/static/canvas-page.html` (importmap entry)

Add hljs to the existing vendor build. The project already vendors lit/marked/dompurify/etc. via `make vendor`; locate that script and add hljs alongside.

- [ ] **Step 1: Locate the vendor build**

```bash
grep -n "make vendor\|vendor" Makefile
ls scripts/
cat scripts/build_vendor.* 2>/dev/null || echo "not found"
```

If a vendor build script exists, edit it. Otherwise, look at how the existing vendored modules in `web/static/vendor/bundle/` were built — there may be a comment, README, or git log. If still unclear, ESCALATE (BLOCKED) and ask Les.

- [ ] **Step 2: Add hljs to the vendor build**

Concrete instructions depend on the discovered build. Most likely shape: an esbuild / rollup invocation that bundles a list of npm packages. Add `highlight.js` to the input list. Use:

```js
// pseudo-input
import hljs from 'highlight.js/lib/core';
import python from 'highlight.js/lib/languages/python';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import json from 'highlight.js/lib/languages/json';
import yaml from 'highlight.js/lib/languages/yaml';
import xml from 'highlight.js/lib/languages/xml';        // also serves HTML
import css from 'highlight.js/lib/languages/css';
import scss from 'highlight.js/lib/languages/scss';
import bash from 'highlight.js/lib/languages/bash';
import shell from 'highlight.js/lib/languages/shell';
import dockerfile from 'highlight.js/lib/languages/dockerfile';
import sql from 'highlight.js/lib/languages/sql';
import markdown from 'highlight.js/lib/languages/markdown';
import go from 'highlight.js/lib/languages/go';
import rust from 'highlight.js/lib/languages/rust';
import ruby from 'highlight.js/lib/languages/ruby';
import java from 'highlight.js/lib/languages/java';
import kotlin from 'highlight.js/lib/languages/kotlin';
import c from 'highlight.js/lib/languages/c';
import cpp from 'highlight.js/lib/languages/cpp';
import plaintext from 'highlight.js/lib/languages/plaintext';

hljs.registerLanguage('python', python);
hljs.registerLanguage('javascript', javascript);
hljs.registerLanguage('js', javascript);
hljs.registerLanguage('typescript', typescript);
hljs.registerLanguage('ts', typescript);
hljs.registerLanguage('json', json);
hljs.registerLanguage('yaml', yaml);
hljs.registerLanguage('yml', yaml);
hljs.registerLanguage('xml', xml);
hljs.registerLanguage('html', xml);
hljs.registerLanguage('css', css);
hljs.registerLanguage('scss', scss);
hljs.registerLanguage('bash', bash);
hljs.registerLanguage('sh', bash);
hljs.registerLanguage('shell', shell);
hljs.registerLanguage('dockerfile', dockerfile);
hljs.registerLanguage('sql', sql);
hljs.registerLanguage('markdown', markdown);
hljs.registerLanguage('md', markdown);
hljs.registerLanguage('go', go);
hljs.registerLanguage('rust', rust);
hljs.registerLanguage('ruby', ruby);
hljs.registerLanguage('java', java);
hljs.registerLanguage('kotlin', kotlin);
hljs.registerLanguage('c', c);
hljs.registerLanguage('cpp', cpp);
hljs.registerLanguage('plaintext', plaintext);

export default hljs;
```

Output: `src/decafclaw/web/static/vendor/bundle/highlight.js` (single file, ESM).

Run `make vendor` and verify the file is created and non-empty.

- [ ] **Step 3: Create theme stylesheet**

Create `src/decafclaw/web/static/styles/hljs-themes.css`:

```css
/* highlight.js theme — dual-mode based on Pico's data-theme attribute. */

/* Atom One Dark (active when [data-theme="dark"]) */
:root[data-theme="dark"] .hljs {
  color: #abb2bf;
  background: var(--pico-code-background-color);
}
:root[data-theme="dark"] .hljs-comment,
:root[data-theme="dark"] .hljs-quote {
  color: #5c6370;
  font-style: italic;
}
:root[data-theme="dark"] .hljs-doctag,
:root[data-theme="dark"] .hljs-formula,
:root[data-theme="dark"] .hljs-keyword {
  color: #c678dd;
}
:root[data-theme="dark"] .hljs-deletion,
:root[data-theme="dark"] .hljs-name,
:root[data-theme="dark"] .hljs-section,
:root[data-theme="dark"] .hljs-selector-tag,
:root[data-theme="dark"] .hljs-subst {
  color: #e06c75;
}
:root[data-theme="dark"] .hljs-literal { color: #56b6c2; }
:root[data-theme="dark"] .hljs-addition,
:root[data-theme="dark"] .hljs-attribute,
:root[data-theme="dark"] .hljs-meta .hljs-string,
:root[data-theme="dark"] .hljs-regexp,
:root[data-theme="dark"] .hljs-string {
  color: #98c379;
}
:root[data-theme="dark"] .hljs-attr,
:root[data-theme="dark"] .hljs-number,
:root[data-theme="dark"] .hljs-selector-attr,
:root[data-theme="dark"] .hljs-selector-class,
:root[data-theme="dark"] .hljs-selector-pseudo,
:root[data-theme="dark"] .hljs-template-variable,
:root[data-theme="dark"] .hljs-type,
:root[data-theme="dark"] .hljs-variable {
  color: #d19a66;
}
:root[data-theme="dark"] .hljs-bullet,
:root[data-theme="dark"] .hljs-link,
:root[data-theme="dark"] .hljs-meta,
:root[data-theme="dark"] .hljs-selector-id,
:root[data-theme="dark"] .hljs-symbol,
:root[data-theme="dark"] .hljs-title {
  color: #61aeee;
}
:root[data-theme="dark"] .hljs-built_in,
:root[data-theme="dark"] .hljs-class .hljs-title,
:root[data-theme="dark"] .hljs-title.class_ {
  color: #e6c07b;
}
:root[data-theme="dark"] .hljs-emphasis { font-style: italic; }
:root[data-theme="dark"] .hljs-strong { font-weight: bold; }
:root[data-theme="dark"] .hljs-link { text-decoration: underline; }

/* Atom One Light (active when [data-theme="light"] OR no data-theme set) */
:root[data-theme="light"] .hljs,
:root:not([data-theme="dark"]) .hljs {
  color: #383a42;
  background: var(--pico-code-background-color);
}
:root[data-theme="light"] .hljs-comment,
:root:not([data-theme="dark"]) .hljs-comment,
:root[data-theme="light"] .hljs-quote,
:root:not([data-theme="dark"]) .hljs-quote {
  color: #a0a1a7;
  font-style: italic;
}
:root[data-theme="light"] .hljs-doctag,
:root:not([data-theme="dark"]) .hljs-doctag,
:root[data-theme="light"] .hljs-keyword,
:root:not([data-theme="dark"]) .hljs-keyword,
:root[data-theme="light"] .hljs-formula,
:root:not([data-theme="dark"]) .hljs-formula {
  color: #a626a4;
}
:root[data-theme="light"] .hljs-section,
:root:not([data-theme="dark"]) .hljs-section,
:root[data-theme="light"] .hljs-name,
:root:not([data-theme="dark"]) .hljs-name,
:root[data-theme="light"] .hljs-selector-tag,
:root:not([data-theme="dark"]) .hljs-selector-tag,
:root[data-theme="light"] .hljs-deletion,
:root:not([data-theme="dark"]) .hljs-deletion,
:root[data-theme="light"] .hljs-subst,
:root:not([data-theme="dark"]) .hljs-subst {
  color: #e45649;
}
:root[data-theme="light"] .hljs-literal,
:root:not([data-theme="dark"]) .hljs-literal {
  color: #0184bb;
}
:root[data-theme="light"] .hljs-string,
:root:not([data-theme="dark"]) .hljs-string,
:root[data-theme="light"] .hljs-regexp,
:root:not([data-theme="dark"]) .hljs-regexp,
:root[data-theme="light"] .hljs-addition,
:root:not([data-theme="dark"]) .hljs-addition,
:root[data-theme="light"] .hljs-attribute,
:root:not([data-theme="dark"]) .hljs-attribute,
:root[data-theme="light"] .hljs-meta .hljs-string,
:root:not([data-theme="dark"]) .hljs-meta .hljs-string {
  color: #50a14f;
}
:root[data-theme="light"] .hljs-attr,
:root:not([data-theme="dark"]) .hljs-attr,
:root[data-theme="light"] .hljs-variable,
:root:not([data-theme="dark"]) .hljs-variable,
:root[data-theme="light"] .hljs-template-variable,
:root:not([data-theme="dark"]) .hljs-template-variable,
:root[data-theme="light"] .hljs-type,
:root:not([data-theme="dark"]) .hljs-type,
:root[data-theme="light"] .hljs-selector-class,
:root:not([data-theme="dark"]) .hljs-selector-class,
:root[data-theme="light"] .hljs-selector-attr,
:root:not([data-theme="dark"]) .hljs-selector-attr,
:root[data-theme="light"] .hljs-selector-pseudo,
:root:not([data-theme="dark"]) .hljs-selector-pseudo,
:root[data-theme="light"] .hljs-number,
:root:not([data-theme="dark"]) .hljs-number {
  color: #986801;
}
:root[data-theme="light"] .hljs-symbol,
:root:not([data-theme="dark"]) .hljs-symbol,
:root[data-theme="light"] .hljs-bullet,
:root:not([data-theme="dark"]) .hljs-bullet,
:root[data-theme="light"] .hljs-link,
:root:not([data-theme="dark"]) .hljs-link,
:root[data-theme="light"] .hljs-meta,
:root:not([data-theme="dark"]) .hljs-meta,
:root[data-theme="light"] .hljs-selector-id,
:root:not([data-theme="dark"]) .hljs-selector-id,
:root[data-theme="light"] .hljs-title,
:root:not([data-theme="dark"]) .hljs-title {
  color: #4078f2;
}
:root[data-theme="light"] .hljs-built_in,
:root:not([data-theme="dark"]) .hljs-built_in,
:root[data-theme="light"] .hljs-class .hljs-title,
:root:not([data-theme="dark"]) .hljs-class .hljs-title,
:root[data-theme="light"] .hljs-title.class_,
:root:not([data-theme="dark"]) .hljs-title.class_ {
  color: #c18401;
}
.hljs-emphasis { font-style: italic; }
.hljs-strong { font-weight: bold; }
.hljs-link { text-decoration: underline; }
```

Or alternatively: pull the official atom-one-dark.css and atom-one-light.css from highlight.js's CDN/npm package directly (`highlight.js/styles/atom-one-dark.css` and `atom-one-light.css`), prefix every selector with `:root[data-theme="dark"]` / `:root[data-theme="light"]`, and concat into a single file. Either works.

- [ ] **Step 4: Update importmap in `index.html` and `canvas-page.html`**

In both files, find the `<script type="importmap">` and add:

```json
"hljs": "/static/vendor/bundle/highlight.js"
```

In `index.html`, add a stylesheet link near the existing CSS imports:

```html
<link rel="stylesheet" href="/static/styles/hljs-themes.css">
```

(Or import via `style.css @import './styles/hljs-themes.css';` — match the project's existing CSS-loading convention.)

In `canvas-page.html`, do the same.

- [ ] **Step 5: Verify the bundle loads**

Quick smoke: start the dev server in the worktree, navigate to a page, run in console:

```js
import('hljs').then(m => console.log('hljs version:', m.default.versionString));
```

Expected: prints a version like `11.x`.

- [ ] **Step 6: Run python suite — should be unaffected**

```bash
uv run pytest tests/ -x -q
```

- [ ] **Step 7: check-js**

```bash
make check-js
```

- [ ] **Step 8: Commit**

```bash
git add Makefile scripts/ src/decafclaw/web/static/vendor/bundle/highlight.js \
        src/decafclaw/web/static/styles/hljs-themes.css \
        src/decafclaw/web/static/index.html \
        src/decafclaw/web/static/canvas-page.html
# Plus style.css if you imported the theme there.
git commit -m "feat(web): vendor highlight.js + dual-theme CSS

Adds hljs to the vendor bundle (~80KB minified, common languages
covered: python, js/ts, json, yaml, html/xml, css, sh, dockerfile,
sql, md, go, rust, ruby, java, kotlin, c/cpp, plaintext). Atom One
themes scoped under :root[data-theme=...] so highlighting follows
Pico's light/dark mode.

Phase 4 of #256 (#389)."
```

---

## Task 7: Apply hljs to existing chat code blocks

**Files:**
- Modify: `src/decafclaw/web/static/components/messages/assistant-message.js`

The chat already adds language labels and Copy buttons to fenced code blocks but doesn't apply syntax coloring. Hook hljs into the same `updated()` callback.

- [ ] **Step 1: Add the hljs call to `updated()`**

In `assistant-message.js`, find `updated() { this.querySelectorAll('pre:not(.has-copy)').forEach(...) }`. Replace with:

```js
import hljs from 'hljs';

// ... inside the class:
updated() {
  this.querySelectorAll('pre:not(.has-copy)').forEach(pre => {
    pre.classList.add('has-copy');

    // Apply syntax highlighting if a language class is present (or auto-detect).
    const code = pre.querySelector('code');
    if (code) {
      const langMatch = code.className.match(/language-(\S+)/);
      if (langMatch) {
        // Add language label
        const label = document.createElement('span');
        label.className = 'code-lang-label';
        label.textContent = langMatch[1];
        /** @type {HTMLElement} */ (pre).style.paddingTop = '2rem';
        pre.appendChild(label);
      }
      // hljs auto-detects when no language class is present; with one,
      // it uses the named language. Either way: highlight in place.
      try {
        hljs.highlightElement(/** @type {HTMLElement} */ (code));
      } catch (err) {
        console.warn('hljs failed for code block:', err);
      }
    }

    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.type = 'button';
    btn.textContent = 'Copy';
    btn.addEventListener('click', () => {
      navigator.clipboard.writeText(/** @type {HTMLElement} */ (pre).innerText).then(() => {
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
      });
    });
    pre.appendChild(btn);
  });
}
```

(Add `import hljs from 'hljs';` to the top of the file alongside other imports.)

- [ ] **Step 2: check-js**

```bash
make check-js
```

- [ ] **Step 3: Smoke check via Playwright MCP**

Have the agent return a fenced code block (e.g., ask "show me a python hello world") and verify the chat code block is now syntax-highlighted with hljs colors. Defer to Task 14's full smoke for the mobile/theme variations.

- [ ] **Step 4: Commit**

```bash
git add src/decafclaw/web/static/components/messages/assistant-message.js
git commit -m "feat(web): apply hljs syntax highlighting to chat code blocks

Existing chat fenced code blocks had a language label and Copy button
but no actual coloring. hljs.highlightElement() now runs on every
code block in assistant messages — same visual treatment as the new
code_block widget will get in Task 8.

Phase 4 of #256 (#389)."
```

---

## Task 8: `code_block` widget

**Files:**
- Create: `src/decafclaw/web/static/widgets/code_block/widget.json`
- Create: `src/decafclaw/web/static/widgets/code_block/widget.js`
- Modify: `src/decafclaw/web/static/styles/widgets.css` (or wherever bundled-widget styles live)
- Modify: `tests/test_widgets.py`

Mirrors `markdown_document`'s structure (inline collapse + Expand + Open in Canvas; canvas full + scroll preservation). Hooks hljs in `updated()`.

- [ ] **Step 1: Create the descriptor**

`src/decafclaw/web/static/widgets/code_block/widget.json`:

```json
{
  "name": "code_block",
  "description": "A syntax-highlighted code block. Inline mode shows a collapsed preview with Expand and Open in Canvas buttons; canvas mode shows the full file with scroll preservation.",
  "modes": ["inline", "canvas"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "required": ["code"],
    "properties": {
      "code": { "type": "string" },
      "language": { "type": "string" },
      "filename": { "type": "string" }
    },
    "additionalProperties": false
  }
}
```

- [ ] **Step 2: Add registry test**

In `tests/test_widgets.py`, alongside `test_bundled_markdown_document_is_registered`:

```python
def test_bundled_code_block_is_registered():
    from decafclaw.widgets import load_widget_registry
    bundled = Path(__file__).resolve().parents[1] / "src" / "decafclaw" / "web" / "static" / "widgets"
    class _Cfg:
        agent_path = Path("/nonexistent/admin")
    reg = load_widget_registry(_Cfg(), bundled_dir=bundled, admin_dir=Path("/nonexistent/admin"))
    desc = reg.get("code_block")
    assert desc is not None
    assert "inline" in desc.modes
    assert "canvas" in desc.modes
    ok, _ = reg.validate("code_block", {"code": "print('hi')"})
    assert ok
    ok, _ = reg.validate("code_block", {"code": "x", "language": "python"})
    assert ok
    bad, _ = reg.validate("code_block", {})
    assert not bad
```

(Adjust the import / fixtures to match the existing test patterns in `test_widgets.py`.)

```bash
uv run pytest tests/test_widgets.py -k code_block -v
```
Expected: passes immediately after creating the descriptor.

- [ ] **Step 3: Create the Lit component**

`src/decafclaw/web/static/widgets/code_block/widget.js`:

```js
import { LitElement, html } from 'lit';
import hljs from 'hljs';
import { getActiveConvId } from '/static/lib/canvas-state.js';

const INLINE_MAX_HEIGHT = '12rem';

/**
 * code_block widget. Two modes set by the host:
 *   mode='inline'  → collapsed preview with Expand + Open in Canvas
 *   mode='canvas'  → full file, scroll preserved across data updates
 *
 * Data shape: { code: string, language?: string, filename?: string }
 */
export class CodeBlockWidget extends LitElement {
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
    const scroller = this.querySelector('.code-block-scroll');
    if (scroller) {
      this._savedScroll = {
        top: scroller.scrollTop,
        left: scroller.scrollLeft,
      };
    }
  }

  updated(changed) {
    // Apply hljs to the code element after each render. Re-running on the
    // same node is a no-op for hljs (it skips already-highlighted code).
    const codeEl = this.querySelector('pre code');
    if (codeEl && !codeEl.dataset.highlighted) {
      try {
        hljs.highlightElement(/** @type {HTMLElement} */ (codeEl));
      } catch (err) {
        console.warn('hljs failed for code_block:', err);
      }
    }
    // Restore scroll in canvas mode after data change
    if (this.mode === 'canvas' && this._savedScroll) {
      const scroller = this.querySelector('.code-block-scroll');
      if (scroller) {
        const maxTop = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
        const maxLeft = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
        scroller.scrollTop = Math.min(this._savedScroll.top, maxTop);
        scroller.scrollLeft = Math.min(this._savedScroll.left, maxLeft);
      }
      this._savedScroll = null;
    }
  }

  _headerLabel() {
    return this.data?.filename
      || (this.data?.language ? `${this.data.language} snippet` : 'code');
  }

  _toggleExpand() {
    this.expanded = !this.expanded;
  }

  _copyCode() {
    const code = this.data?.code ?? '';
    const btn = this.querySelector('.code-block-copy');
    navigator.clipboard.writeText(code).then(() => {
      if (btn) {
        const original = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => { if (btn.textContent === 'Copied!') btn.textContent = original; }, 2000);
      }
    });
  }

  async _openInCanvas() {
    const convId = getActiveConvId() || '';
    if (!convId) return;
    const label = this._headerLabel();
    try {
      const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}/new_tab`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          widget_type: 'code_block',
          data: {
            code: this.data?.code ?? '',
            language: this.data?.language ?? undefined,
            filename: this.data?.filename ?? undefined,
          },
          label,
        }),
      });
      if (!resp.ok) {
        console.error('canvas new_tab failed', resp.status, await resp.text());
      }
    } catch (err) {
      console.error('canvas new_tab error', err);
    }
  }

  render() {
    const code = this.data?.code ?? '';
    const lang = this.data?.language ?? 'plaintext';
    const headerLabel = this._headerLabel();

    if (this.mode === 'canvas') {
      return html`
        <div class="code-block code-block-canvas">
          <header class="code-block-header">
            <span class="code-block-label">${headerLabel}</span>
            <span class="code-block-spacer"></span>
            <button class="code-block-copy" type="button"
                    aria-label="Copy code to clipboard"
                    @click=${this._copyCode}>Copy</button>
          </header>
          <div class="code-block-scroll">
            <pre><code class="language-${lang}">${code}</code></pre>
          </div>
        </div>
      `;
    }

    // inline
    const collapsedStyle = this.expanded
      ? ''
      : `max-height: ${INLINE_MAX_HEIGHT}; overflow: hidden;`;
    return html`
      <div class="code-block code-block-inline ${this.expanded ? 'expanded' : 'collapsed'}">
        <header class="code-block-header">
          <span class="code-block-label">${headerLabel}</span>
          <span class="code-block-spacer"></span>
          <button class="code-block-copy" type="button"
                  aria-label="Copy code to clipboard"
                  @click=${this._copyCode}>Copy</button>
        </header>
        <div class="code-block-body" style=${collapsedStyle}>
          <pre><code class="language-${lang}">${code}</code></pre>
        </div>
        <div class="code-block-actions">
          <button type="button" @click=${this._toggleExpand}>${this.expanded ? 'Collapse' : 'Expand'}</button>
          <button type="button" @click=${this._openInCanvas}>Open in Canvas</button>
        </div>
      </div>
    `;
  }
}

customElements.define('dc-widget-code-block', CodeBlockWidget);
```

- [ ] **Step 4: Add styles**

Append to `src/decafclaw/web/static/styles/widgets.css` (where `.md-doc-*` styles live):

```css
.code-block {
  display: flex;
  flex-direction: column;
  background: var(--pico-card-background-color);
  border: 1px solid var(--pico-muted-border-color);
  border-radius: 0.5rem;
  overflow: hidden;
}
.code-block-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 0.75rem;
  background: var(--pico-secondary-background, rgba(0,0,0,0.04));
  border-bottom: 1px solid var(--pico-muted-border-color);
  font-size: 0.85rem;
  font-family: var(--pico-font-family-monospace, monospace);
}
.code-block-label { font-weight: 600; }
.code-block-spacer { flex: 1; }
.code-block-copy {
  background: none;
  border: 1px solid var(--pico-muted-border-color);
  border-radius: 0.25rem;
  padding: 0.15rem 0.5rem;
  cursor: pointer;
  font-size: 0.8rem;
  color: var(--pico-color);
}
.code-block-copy:hover { color: var(--pico-primary); }
.code-block-body { position: relative; }
.code-block-body pre {
  margin: 0;
  padding: 0.75rem 1rem;
  background: transparent;
  overflow-x: auto;
}
.code-block-inline.collapsed .code-block-body::after {
  content: "";
  position: absolute;
  inset: auto 0 0 0;
  height: 2rem;
  background: linear-gradient(transparent, var(--pico-card-background-color, #fff));
  pointer-events: none;
}
.code-block-actions {
  display: flex;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-top: 1px solid var(--pico-muted-border-color);
}
.code-block-canvas {
  flex: 1;
  min-height: 0;
}
.code-block-canvas .code-block-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.code-block-canvas .code-block-scroll pre {
  margin: 0;
  padding: 0.75rem 1rem;
}
```

- [ ] **Step 5: Run python tests, lint, commit**

```bash
uv run pytest tests/test_widgets.py -v
make check-js
git add src/decafclaw/web/static/widgets/code_block/ \
        src/decafclaw/web/static/styles/widgets.css \
        tests/test_widgets.py
git commit -m "feat(widgets): code_block widget — inline + canvas modes

Inline: max-height 12rem with fade gradient, Expand/Collapse + Open in
Canvas buttons. Canvas: full file, scroll preserved across data
updates. hljs.highlightElement applied in updated() with
auto-detection fallback when language is missing. Header bar shows
filename → language → 'code'; Copy button on both modes.

Phase 4 of #256 (#389)."
```

---
## Task 9: `canvas-state.js` multi-tab support

**Files:**
- Modify: `src/decafclaw/web/static/lib/canvas-state.js`

The current snapshot exposes a single `tab`. Phase 4 needs `tabs` (list) + `activeTabId`. Update event handling to track each tab's data and switch active per `set_active`.

- [ ] **Step 1: Replace `canvas-state.js` with multi-tab version**

```js
/**
 * Canvas state — per-conversation multi-tab cache + dismiss flag + unread flag.
 *
 * State per conv:
 *   { tabs: [{id, label, widget_type, data}, ...], activeTabId, dismissed, unreadDot }
 *
 * Subscribers receive snapshots:
 *   { tabs, activeTabId, activeTab, visible, unreadDot }
 *
 * Dismiss persists per-conv in localStorage (canvas-dismissed.{convId});
 * cleared on new_tab / close_tab when the panel becomes empty / clear /
 * resummon click.
 */

const DISMISS_KEY_PREFIX = 'canvas-dismissed.';

const _state = {
  byConv: new Map(),  // convId -> { tabs, activeTabId, dismissed, unreadDot }
  active: null,
  subscribers: new Set(),
};

function _dismissKey(convId) { return DISMISS_KEY_PREFIX + convId; }
function _loadDismissed(convId) {
  try { return localStorage.getItem(_dismissKey(convId)) === 'true'; }
  catch { return false; }
}
function _saveDismissed(convId, value) {
  try {
    if (value) localStorage.setItem(_dismissKey(convId), 'true');
    else localStorage.removeItem(_dismissKey(convId));
  } catch { /* localStorage unavailable */ }
}

function _ensure(convId) {
  if (!_state.byConv.has(convId)) {
    _state.byConv.set(convId, {
      tabs: [],
      activeTabId: null,
      dismissed: _loadDismissed(convId),
      unreadDot: false,
    });
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
    return { tabs: [], activeTabId: null, activeTab: null, visible: false, unreadDot: false };
  }
  const s = _ensure(_state.active);
  const activeTab = s.tabs.find(t => t.id === s.activeTabId) || null;
  return {
    tabs: s.tabs.slice(),
    activeTabId: s.activeTabId,
    activeTab,
    visible: s.tabs.length > 0 && !s.dismissed,
    unreadDot: s.unreadDot,
  };
}

export function subscribe(callback) {
  _state.subscribers.add(callback);
  return () => _state.subscribers.delete(callback);
}

export function getActiveConvId() { return _state.active; }

export async function setActiveConv(convId) {
  _state.active = convId;
  if (!convId) { _publish(); return; }
  const s = _ensure(convId);
  s.unreadDot = false;
  try {
    const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}`,
                             { credentials: 'same-origin' });
    if (resp.ok) {
      const data = await resp.json();
      s.tabs = (data.tabs || []).map(t => ({...t}));
      s.activeTabId = data.active_tab || null;
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
  const kind = evt.kind || 'update';

  if (kind === 'clear') {
    s.tabs = [];
    s.activeTabId = null;
    s.unreadDot = false;
    s.dismissed = false;
    _saveDismissed(convId, false);
  } else if (kind === 'new_tab') {
    if (evt.tab) s.tabs.push({...evt.tab});
    s.activeTabId = evt.active_tab;
    s.dismissed = false;
    s.unreadDot = false;
    _saveDismissed(convId, false);
  } else if (kind === 'update') {
    if (evt.tab) {
      const idx = s.tabs.findIndex(t => t.id === evt.tab.id);
      if (idx >= 0) s.tabs[idx] = {...evt.tab};
    }
    if (s.dismissed) s.unreadDot = true;
  } else if (kind === 'close_tab') {
    s.tabs = s.tabs.filter(t => t.id !== evt.closed_tab_id);
    s.activeTabId = evt.active_tab;
    if (s.tabs.length === 0) {
      // Last tab closed — clear dismiss flag too so a future new_tab reveals.
      s.dismissed = false;
      _saveDismissed(convId, false);
    }
  } else if (kind === 'set_active') {
    s.activeTabId = evt.active_tab;
  }
  if (convId === _state.active) _publish();
}

export function dismiss() {
  if (!_state.active) return;
  const s = _ensure(_state.active);
  s.dismissed = true;
  _saveDismissed(_state.active, true);
  _publish();
}

export function resummon() {
  if (!_state.active) return;
  const s = _ensure(_state.active);
  s.dismissed = false;
  s.unreadDot = false;
  _saveDismissed(_state.active, false);
  _publish();
}

/** User clicks a tab in the strip / mobile list — switch active. */
export async function switchToTab(tabId) {
  const convId = _state.active;
  if (!convId) return;
  // Optimistic local update so UI feels responsive.
  const s = _ensure(convId);
  s.activeTabId = tabId;
  _publish();
  try {
    await fetch(`/api/canvas/${encodeURIComponent(convId)}/active_tab`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ tab_id: tabId }),
    });
  } catch (err) {
    console.warn('canvas active_tab POST failed', err);
  }
}

/** User clicks [×] on a tab — close it. */
export async function closeTabFromUi(tabId) {
  const convId = _state.active;
  if (!convId) return;
  try {
    await fetch(`/api/canvas/${encodeURIComponent(convId)}/close_tab`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ tab_id: tabId }),
    });
    // Server emits canvas_update kind=close_tab, which applyEvent handles.
  } catch (err) {
    console.warn('canvas close_tab POST failed', err);
  }
}
```

- [ ] **Step 2: check-js**

```bash
make check-js
```

- [ ] **Step 3: Commit**

```bash
git add src/decafclaw/web/static/lib/canvas-state.js
git commit -m "feat(web): canvas-state.js — multi-tab support

Snapshot now exposes tabs[] + activeTabId + activeTab. Adds switchToTab
and closeTabFromUi helpers that POST to the new REST endpoints. WS
event handler covers all five kinds: new_tab, update, close_tab,
set_active, clear.

Phase 4 of #256 (#389)."
```

---

## Task 10: `<canvas-panel>` desktop tab strip + ARIA + keyboard nav

**Files:**
- Modify: `src/decafclaw/web/static/components/canvas-panel.js`
- Modify: `src/decafclaw/web/static/styles/canvas.css`

Render the tab strip at the top of the panel (desktop). Add ARIA roles + keyboard nav (Arrow / Home / End / Delete). The mobile vertical-list disclosure happens in Task 11.

- [ ] **Step 1: Update `canvas-panel.js`** — add tab strip render

Replace the panel's render with:

```js
import { LitElement, html } from 'lit';
import {
  subscribe, currentSnapshot, dismiss, getActiveConvId,
  switchToTab, closeTabFromUi,
} from '../lib/canvas-state.js';
import { getDescriptor } from '../lib/widget-catalog.js';
import './widgets/widget-host.js';

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
    if (visible && window.matchMedia('(max-width: 639px)').matches) {
      document.getElementById('wiki-main')?.classList.add('hidden');
    }
  }

  _onClose() { dismiss(); }
  _onOpenInTab() {
    const convId = getActiveConvId();
    if (!convId) return;
    const tabId = this._snapshot.activeTabId;
    const url = tabId
      ? `/canvas/${encodeURIComponent(convId)}/${encodeURIComponent(tabId)}`
      : `/canvas/${encodeURIComponent(convId)}`;
    window.open(url, '_blank', 'noopener');
  }

  /** @param {KeyboardEvent} e */
  _onTabKeyDown(e) {
    const tabs = this._snapshot.tabs;
    if (tabs.length === 0) return;
    const currentIdx = tabs.findIndex(t => t.id === this._snapshot.activeTabId);
    let nextIdx = currentIdx;
    if (e.key === 'ArrowLeft') nextIdx = Math.max(0, currentIdx - 1);
    else if (e.key === 'ArrowRight') nextIdx = Math.min(tabs.length - 1, currentIdx + 1);
    else if (e.key === 'Home') nextIdx = 0;
    else if (e.key === 'End') nextIdx = tabs.length - 1;
    else if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault();
      const targetId = e.target?.dataset?.tabId;
      if (targetId) closeTabFromUi(targetId);
      return;
    } else {
      return;
    }
    e.preventDefault();
    if (nextIdx !== currentIdx && tabs[nextIdx]) {
      switchToTab(tabs[nextIdx].id);
      // Move focus too
      requestAnimationFrame(() => {
        const btn = this.querySelector(`[data-tab-id="${tabs[nextIdx].id}"]`);
        if (btn instanceof HTMLElement) btn.focus();
      });
    }
  }

  _renderTabStrip() {
    const tabs = this._snapshot.tabs;
    if (tabs.length === 0) return html``;
    return html`
      <div class="canvas-tab-strip" role="tablist" aria-label="Canvas tabs">
        ${tabs.map(t => html`
          <div class="canvas-tab ${t.id === this._snapshot.activeTabId ? 'active' : ''}"
               role="tab"
               tabindex=${t.id === this._snapshot.activeTabId ? 0 : -1}
               aria-selected=${t.id === this._snapshot.activeTabId ? 'true' : 'false'}
               aria-controls="canvas-tabpanel"
               data-tab-id=${t.id}
               @click=${() => switchToTab(t.id)}
               @keydown=${(e) => this._onTabKeyDown(e)}>
            <span class="canvas-tab-label">${t.label || t.id}</span>
            <button class="canvas-tab-close" type="button"
                    aria-label="Close tab ${t.label || t.id}"
                    @click=${(e) => { e.stopPropagation(); closeTabFromUi(t.id); }}>×</button>
          </div>
        `)}
      </div>
    `;
  }

  render() {
    const active = this._snapshot.activeTab;
    if (!active) {
      return html`<div class="canvas-empty">No canvas content yet.</div>`;
    }
    const descriptor = getDescriptor(active.widget_type);
    return html`
      ${this._renderTabStrip()}
      <header class="canvas-header">
        <span class="canvas-spacer"></span>
        <button class="canvas-btn" type="button"
                title="Open in new tab" aria-label="Open canvas in new tab"
                @click=${this._onOpenInTab}>↗</button>
        <button class="canvas-btn canvas-close" type="button"
                title="Close" aria-label="Close canvas panel"
                @click=${this._onClose}>×</button>
      </header>
      <main class="canvas-body" id="canvas-tabpanel"
            role="tabpanel" aria-labelledby=${active.id}>
        <dc-widget-host
          .widgetType=${active.widget_type}
          .descriptor=${descriptor}
          .data=${active.data}
          .mode=${'canvas'}
          fallbackText="Canvas widget unavailable">
        </dc-widget-host>
      </main>
    `;
  }
}

customElements.define('canvas-panel', CanvasPanel);
```

Note: dropped the redundant `<span class="canvas-label">` — the active label is now in the strip.

Add `role="region" aria-label="Canvas"` on the panel itself by setting attributes in `connectedCallback`:

```js
connectedCallback() {
  super.connectedCallback();
  this.setAttribute('role', 'region');
  this.setAttribute('aria-label', 'Canvas');
  this._unsubscribe = subscribe(snap => {
    this._snapshot = snap;
    this._reflectVisibility();
  });
  this._reflectVisibility();
}
```

- [ ] **Step 2: Add tab-strip styles**

Append to `src/decafclaw/web/static/styles/canvas.css`:

```css
.canvas-tab-strip {
  display: flex;
  overflow-x: auto;
  border-bottom: 1px solid var(--pico-muted-border-color);
  background: var(--pico-secondary-background, transparent);
  scrollbar-width: thin;
}
.canvas-tab {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.75rem;
  min-width: 7.5rem;
  max-width: 14rem;
  border: 0;
  border-bottom: 2px solid transparent;
  background: none;
  color: var(--pico-muted-color);
  cursor: pointer;
  font-size: 0.85rem;
  white-space: nowrap;
}
.canvas-tab:focus { outline: 2px solid var(--pico-primary); outline-offset: -2px; }
.canvas-tab.active {
  color: var(--pico-color);
  border-bottom-color: var(--pico-primary);
  font-weight: 600;
}
.canvas-tab-label {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
}
.canvas-tab-close {
  background: none;
  border: 0;
  cursor: pointer;
  color: inherit;
  padding: 0.1rem 0.3rem;
  border-radius: 0.25rem;
  font-size: 1rem;
  line-height: 1;
}
.canvas-tab-close:hover { background: var(--pico-muted-border-color); color: var(--pico-color); }
```

- [ ] **Step 3: check-js + smoke**

```bash
make check-js
```

Smoke via Playwright MCP after starting the worktree dev server:
- Have the agent call `canvas_new_tab` twice, verify two tabs appear in the strip.
- Click a tab → switches active; check console.
- Press ArrowLeft/Right when a tab is focused → cycles.
- Press Delete on a focused tab → closes.

Defer end-to-end verification to Task 14.

- [ ] **Step 4: Commit**

```bash
git add src/decafclaw/web/static/components/canvas-panel.js \
        src/decafclaw/web/static/styles/canvas.css
git commit -m "feat(web): canvas-panel desktop tab strip + ARIA tab pattern

Tab strip renders at the top of <canvas-panel> with horizontal
overflow scroll, per-tab close [×], roving tabindex, and ARIA roles
(tablist / tab / tabpanel / region). Keyboard nav: ArrowLeft/Right,
Home/End, Delete. Active tab gets a colored bottom border accent.

Phase 4 of #256 (#389)."
```

---

## Task 11: Mobile vertical-list disclosure

**Files:**
- Modify: `src/decafclaw/web/static/components/canvas-panel.js`
- Modify: `src/decafclaw/web/static/styles/canvas.css`

On `(max-width: 639px)`, replace the horizontal strip with a "Tabs (N) ▼" disclosure button that toggles a vertical list overlay.

- [ ] **Step 1: Add the mobile disclosure to `canvas-panel.js`**

Add a state property and helpers:

```js
static properties = {
  _snapshot: { state: true },
  _mobileListOpen: { state: true },
};

constructor() {
  super();
  this._snapshot = currentSnapshot();
  this._mobileListOpen = false;
  this._unsubscribe = null;
}

_toggleMobileList() {
  this._mobileListOpen = !this._mobileListOpen;
}

_onMobileTabClick(tabId) {
  switchToTab(tabId);
  this._mobileListOpen = false;
}

_renderMobileList() {
  if (!this._mobileListOpen) return html``;
  return html`
    <div class="canvas-mobile-list" role="tablist" aria-label="Canvas tabs">
      ${this._snapshot.tabs.map(t => html`
        <div class="canvas-mobile-tab ${t.id === this._snapshot.activeTabId ? 'active' : ''}"
             role="tab"
             aria-selected=${t.id === this._snapshot.activeTabId ? 'true' : 'false'}
             aria-controls="canvas-tabpanel"
             tabindex=${t.id === this._snapshot.activeTabId ? 0 : -1}
             @click=${() => this._onMobileTabClick(t.id)}>
          <span class="canvas-mobile-tab-label">${t.label || t.id}</span>
          <button class="canvas-mobile-tab-close" type="button"
                  aria-label="Close tab ${t.label || t.id}"
                  @click=${(e) => { e.stopPropagation(); closeTabFromUi(t.id); }}>×</button>
        </div>
      `)}
    </div>
  `;
}
```

Update `render()` so on mobile the strip is replaced by the disclosure button + list. Use a `class="mobile-only"` / `class="desktop-only"` CSS-driven approach (simpler than a JS media-query branch):

```js
render() {
  const active = this._snapshot.activeTab;
  if (!active) {
    return html`<div class="canvas-empty">No canvas content yet.</div>`;
  }
  const descriptor = getDescriptor(active.widget_type);
  return html`
    <div class="canvas-tab-strip-desktop">${this._renderTabStrip()}</div>
    <header class="canvas-header">
      <button class="canvas-btn canvas-mobile-disclosure" type="button"
              aria-label="Show tab list"
              @click=${this._toggleMobileList}>
        ☰ Tabs (${this._snapshot.tabs.length}) ▼
      </button>
      <span class="canvas-spacer"></span>
      <button class="canvas-btn" type="button"
              title="Open in new tab" aria-label="Open canvas in new tab"
              @click=${this._onOpenInTab}>↗</button>
      <button class="canvas-btn canvas-close" type="button"
              title="Close" aria-label="Close canvas panel"
              @click=${this._onClose}>×</button>
    </header>
    ${this._renderMobileList()}
    <main class="canvas-body" id="canvas-tabpanel"
          role="tabpanel" aria-labelledby=${active.id}>
      <dc-widget-host
        .widgetType=${active.widget_type}
        .descriptor=${descriptor}
        .data=${active.data}
        .mode=${'canvas'}
        fallbackText="Canvas widget unavailable">
      </dc-widget-host>
    </main>
  `;
}
```

- [ ] **Step 2: Add mobile-disclosure CSS**

Append to `canvas.css`:

```css
.canvas-mobile-disclosure { display: none; }
.canvas-tab-strip-desktop { display: block; }

.canvas-mobile-list {
  display: none;
  flex-direction: column;
  border-bottom: 1px solid var(--pico-muted-border-color);
  background: var(--pico-card-background-color, var(--pico-background-color));
}
.canvas-mobile-tab {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  min-height: 44px;
  cursor: pointer;
  border-bottom: 1px solid var(--pico-muted-border-color);
  color: var(--pico-color);
}
.canvas-mobile-tab:last-child { border-bottom: 0; }
.canvas-mobile-tab.active {
  background: var(--pico-secondary-background, rgba(0,0,0,0.04));
  font-weight: 600;
}
.canvas-mobile-tab.active::before {
  content: "";
  width: 0.5rem; height: 0.5rem; border-radius: 50%;
  background: var(--pico-primary);
}
.canvas-mobile-tab-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.canvas-mobile-tab-close {
  background: none;
  border: 0;
  cursor: pointer;
  color: inherit;
  padding: 0.5rem;
  min-width: 44px; min-height: 44px;
  font-size: 1.2rem;
}

@media (max-width: 639px) {
  .canvas-tab-strip-desktop { display: none; }
  .canvas-mobile-disclosure { display: inline-flex; }
  .canvas-mobile-list { display: flex; }
  .canvas-mobile-list[hidden] { display: none; }
}
```

(The `[hidden]` rule lets us set `hidden` attr on the list when closed — alternative to conditional-render. Pick one approach; the `_renderMobileList()` returns empty when closed, so the `[hidden]` rule isn't strictly needed.)

- [ ] **Step 3: check-js**

```bash
make check-js
```

- [ ] **Step 4: Smoke via Playwright MCP**

- Resize viewport to 600px.
- Verify tab strip is hidden, "Tabs (N) ▼" button visible.
- Click → vertical list opens.
- Tap a row → switches active, list closes.
- Verify 44px tap-target heights.

Defer to Task 14 for full smoke.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/web/static/components/canvas-panel.js \
        src/decafclaw/web/static/styles/canvas.css
git commit -m "feat(web): mobile vertical-list disclosure for canvas tabs

≤639px: tab strip replaced by 'Tabs (N) ▼' disclosure button. Tapping
opens a vertical list with 44px tap targets per row; tap a row to
switch and close the list, tap × to close that tab. ARIA roles match
the desktop strip (tablist/tab).

Phase 4 of #256 (#389)."
```

---

## Task 12: Standalone view tab-locked URL handling

**Files:**
- Modify: `src/decafclaw/web/static/canvas-page.js`
- Modify: `src/decafclaw/web/static/styles/canvas.css` (empty-state for "tab no longer exists")

Path-parse `/canvas/{conv_id}` (bare) vs `/canvas/{conv_id}/{tab_id}` (locked). WebSocket message handler branches per kind.

- [ ] **Step 1: Replace `canvas-page.js`**

```js
/**
 * Standalone canvas page controller.
 *
 * Two URL forms:
 *   /canvas/{conv_id}            → bare; renders active tab; follows active changes via WS.
 *   /canvas/{conv_id}/{tab_id}   → tab-locked; renders one specific tab; ignores active changes.
 */

const PATH_RE = /^\/canvas\/([^/?#]+)(?:\/([^/?#]+))?/;
const m = location.pathname.match(PATH_RE);
let convId = '';
let lockedTabId = null;
if (m) {
  try { convId = decodeURIComponent(m[1]); } catch { convId = ''; }
  if (m[2]) {
    try { lockedTabId = decodeURIComponent(m[2]); } catch { lockedTabId = null; }
  }
}
if (!convId) {
  document.body.innerHTML = '<p>Invalid canvas URL.</p>';
  throw new Error('no conv_id');
}

const host = /** @type {HTMLElement & {widgetType: string, data: any, mode: string}} */ (
  document.getElementById('canvas-standalone-host')
);
const empty = document.getElementById('canvas-empty-state');
const labelEl = document.getElementById('canvas-label');
const backLink = /** @type {HTMLAnchorElement} */ (document.getElementById('canvas-back-link'));
backLink.href = `/?conv=${encodeURIComponent(convId)}`;

let currentTab = null;
let allTabs = [];
let serverActiveTabId = null;

function showEmpty(msg) {
  if (host) host.hidden = true;
  if (empty) {
    empty.hidden = false;
    empty.textContent = msg;
  }
  if (labelEl) labelEl.textContent = 'Canvas';
  document.title = 'Canvas';
}

function showTab(tab) {
  if (!tab) {
    showEmpty(lockedTabId ? `Tab "${lockedTabId}" no longer exists.` : 'No canvas content yet.');
    currentTab = null;
    return;
  }
  if (empty) empty.hidden = true;
  host.hidden = false;
  host.widgetType = tab.widget_type;
  host.mode = 'canvas';
  host.data = tab.data;
  if (labelEl) labelEl.textContent = tab.label || 'Canvas';
  document.title = `Canvas — ${tab.label || 'Canvas'}`;
  currentTab = tab;
}

function pickTabForRender() {
  if (lockedTabId) {
    return allTabs.find(t => t.id === lockedTabId) || null;
  }
  return allTabs.find(t => t.id === serverActiveTabId) || null;
}

async function loadInitial() {
  try {
    const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}`,
                             { credentials: 'same-origin' });
    if (!resp.ok) {
      console.warn('canvas load failed', resp.status);
      showEmpty('No canvas content yet.');
      return;
    }
    const data = await resp.json();
    allTabs = data.tabs || [];
    serverActiveTabId = data.active_tab || null;
    showTab(pickTabForRender());
  } catch (err) {
    console.error('canvas load error', err);
    showEmpty('No canvas content yet.');
  }
}

function openWebSocket() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws/chat`);
  ws.addEventListener('open', () => {
    ws.send(JSON.stringify({ type: 'select_conv', conv_id: convId }));
  });
  ws.addEventListener('message', (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type !== 'canvas_update') return;
    if (msg.conv_id && msg.conv_id !== convId) return;

    const kind = msg.kind || 'update';
    if (kind === 'clear') {
      allTabs = [];
      serverActiveTabId = null;
      showTab(null);
      return;
    }
    if (kind === 'new_tab') {
      if (msg.tab) allTabs.push(msg.tab);
      serverActiveTabId = msg.active_tab;
      // Bare URL: don't switch (we follow active for bare); explicit URL: ignore.
      // Wait — bare URL DOES follow active, so this DOES switch.
      if (!lockedTabId) showTab(pickTabForRender());
      return;
    }
    if (kind === 'update') {
      if (msg.tab) {
        const idx = allTabs.findIndex(t => t.id === msg.tab.id);
        if (idx >= 0) allTabs[idx] = msg.tab;
        if (currentTab && currentTab.id === msg.tab.id) {
          showTab(msg.tab);
        }
      }
      return;
    }
    if (kind === 'close_tab') {
      const closed = msg.closed_tab_id;
      allTabs = allTabs.filter(t => t.id !== closed);
      serverActiveTabId = msg.active_tab;
      if (lockedTabId && closed === lockedTabId) {
        showEmpty(`Tab "${lockedTabId}" no longer exists.`);
        return;
      }
      if (!lockedTabId) showTab(pickTabForRender());
      return;
    }
    if (kind === 'set_active') {
      serverActiveTabId = msg.active_tab;
      if (!lockedTabId) showTab(pickTabForRender());
      // Locked URL: ignore.
      return;
    }
  });
  ws.addEventListener('close', () => {
    console.info('canvas WS closed');
  });
}

await loadInitial();
openWebSocket();
```

- [ ] **Step 2: Update empty-state CSS** — already styled by Phase 3; nothing new needed unless you want a distinct treatment for "tab no longer exists." Optional: add a subtle accent.

- [ ] **Step 3: check-js**

```bash
make check-js
```

- [ ] **Step 4: Smoke via Playwright MCP**

- Open `/canvas/{conv}/{tab_id}` directly → renders that tab.
- From the main UI, switch active tab → standalone DOESN'T follow (locked).
- Close the locked tab → standalone shows "Tab '..' no longer exists."
- Open `/canvas/{conv}` (bare) → renders active.
- Switch active in main UI → standalone follows.

Defer to Task 14 for end-to-end smoke.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/web/static/canvas-page.js
git commit -m "feat(web): standalone canvas — tab-locked URL handling

/canvas/{conv}/{tab_id} renders one specific tab; ignores active-tab
changes from main UI. /canvas/{conv} (bare) follows active. WS event
handler branches per kind: new_tab / update / close_tab / set_active /
clear update local state and re-render appropriately for each URL
form.

Phase 4 of #256 (#389)."
```

---

## Task 13: `markdown_document` widget — rename Open in Canvas POST URL

**Files:**
- Modify: `src/decafclaw/web/static/widgets/markdown_document/widget.js`

Phase 3's POST `/api/canvas/{conv_id}/set` is now POST `/api/canvas/{conv_id}/new_tab`. Update the inline widget's "Open in Canvas" handler.

- [ ] **Step 1: Update the URL in `_openInCanvas`**

Find the line:
```js
const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}/set`, {
```

Change to:
```js
const resp = await fetch(`/api/canvas/${encodeURIComponent(convId)}/new_tab`, {
```

(The body shape is the same: `{widget_type, data, label}`.)

- [ ] **Step 2: check-js + commit**

```bash
make check-js
git add src/decafclaw/web/static/widgets/markdown_document/widget.js
git commit -m "refactor(widgets): markdown_document Open-in-Canvas posts to /new_tab

Phase 4 renamed the canvas REST endpoint /set → /new_tab to match the
agent tool name (canvas_new_tab). Same body shape, same auth check.

Phase 4 of #256 (#389)."
```

---
## Task 14: Documentation updates + manual smoke test

**Files:**
- Modify: `docs/widgets.md`, `docs/web-ui.md`, `docs/web-ui-mobile.md`, `docs/conversations.md`, `docs/context-composer.md`

Plus the end-to-end Playwright MCP smoke test against the worktree dev server.

### Step 14a: Documentation

- [ ] **Step 1: `docs/widgets.md`**
  - Add a "Phase 4 — `code_block` + tabs" section after the existing Phase 3 section.
  - Document the `code_block` widget descriptor and modes.
  - Update the "Bundled widgets" list to include `code_block`.
  - Document the canvas tools API change: tab IDs replace the implicit-active-tab Phase 3 model.
  - Brief mention of hljs integration: chat code blocks now syntax-highlighted.

- [ ] **Step 2: `docs/web-ui.md`**
  - Replace the Phase 3 single-tab description with multi-tab.
  - Document the tab strip UX (desktop) + mobile vertical-list disclosure.
  - Update the `/canvas/{conv_id}` section to mention both bare and `/canvas/{conv_id}/{tab_id}` forms; explain the difference.
  - Update the REST endpoints table:
    - Replace `POST /api/canvas/{conv_id}/set` with `POST /api/canvas/{conv_id}/new_tab`.
    - Add `POST /api/canvas/{conv_id}/active_tab` and `POST /api/canvas/{conv_id}/close_tab`.
    - Add `GET /canvas/{conv_id}/{tab_id}`.

- [ ] **Step 3: `docs/web-ui-mobile.md`**
  - Add a note about the tab vertical-list disclosure pattern at ≤639px.
  - Document 44px tap targets on tab rows + close buttons.

- [ ] **Step 4: `docs/conversations.md`**
  - Update the canvas-sidecar shape to include `next_tab_id`.
  - Mention multi-tab in practice (Phase 3 single-tab note becomes "Phase 4 multi-tab").

- [ ] **Step 5: `docs/context-composer.md`**
  - Update the always-loaded canvas tools list:
    - Remove: `canvas_set`, the no-id `canvas_update`.
    - Add: `canvas_new_tab` (returns tab_id), `canvas_update(tab_id, data)`, `canvas_close_tab(tab_id)`.
    - Keep: `canvas_clear` (clears all), `canvas_read` (returns full state including all tabs).

- [ ] **Step 6: Commit**

```bash
git add docs/
git commit -m "docs: canvas Phase 4 — multi-tab, code_block, hljs

Updates widgets.md (code_block + tab API), web-ui.md (tab strip +
mobile disclosure + dual standalone URL forms), web-ui-mobile.md (tab
list pattern), conversations.md (next_tab_id + multi-tab),
context-composer.md (5 canvas tools, removed Phase 3 ones).

Phase 4 of #256 (#389)."
```

### Step 14b: Manual smoke test

- [ ] **Step 7: Start dev server in worktree**

```bash
cp /Users/lorchard/devel/decafclaw/.env .env  # if not already present
HTTP_PORT=18881 uv run decafclaw &
```

Wait for "uvicorn running" log.

- [ ] **Step 8: Drive Playwright MCP through the smoke checklist**

Read `data/decafclaw/web_tokens.json` for a token, log in, then walk:

1. Have the agent call `canvas_new_tab` with a `markdown_document` → strip shows tab; active.
2. Have the agent call `canvas_new_tab` with a `code_block` and a Python sample → second tab appears, active. Verify hljs colors visible (Python keywords / strings differ).
3. Click the first tab → switches active; `set_active` event broadcasts. (Inspect `network_requests` for the POST.)
4. `[×]` on the active tab → switches to neighbor; close last → panel hides.
5. Bring up an inline `code_block` widget (the agent can call `workspace_preview_markdown` on a workspace `.md` and paste the inline view of code in chat — or trigger via a debug hook). Click "Open in Canvas" → new tab created; verify network request to `/api/canvas/{conv}/new_tab`.
6. Verify hljs highlighting on existing chat fenced code blocks (have the agent emit a Python block in chat).
7. Open `/canvas/{conv}/{tab_id}` in a second browser tab → renders that tab. From main UI, switch active tab → standalone DOESN'T follow. Close the locked tab → standalone shows "Tab no longer exists."
8. Open `/canvas/{conv}` (bare URL) → renders active. Switch active in main UI → standalone follows.
9. Resize browser to 600px → strip replaced by "Tabs (N) ▼". Click → vertical list opens. Tap a row → switches active and closes list. Tap × on a row → closes that tab.
10. Keyboard nav on desktop: focus a tab (Tab key), Arrow Left/Right cycles, Home/End jumps, Delete closes.
11. ARIA tree (Playwright accessibility snapshot): verify `tablist` / `tab` / `tabpanel` / `region` roles present and `aria-selected` accurate on the active tab.

Document findings in `notes.md`.

- [ ] **Step 9: Stop dev server**

```bash
kill %1
rm .env  # remove from worktree
```

- [ ] **Step 10: Fix any issues found and commit**

Each fix is its own small commit:
```bash
git add <files>
git commit -m "fix(canvas): smoke-test correction — <brief summary>"
```

- [ ] **Step 11: Final test suite + lint pass**

```bash
uv run pytest tests/ -q
make check
```

Expected: all pass cleanly.

---

## After completion

When all 14 tasks are done:

1. Push branch: `git push -u origin widgets-phase-4-389`.
2. Open PR with `Closes #389`.
3. Add Copilot reviewer; address feedback in batches per the Phase 3 pattern.
4. After merge, file follow-on issues for the spec's out-of-scope items.
5. Move to dev-session retro phase.

---

## Self-review

**Spec coverage:**
- Tools API (new_tab/update/close_tab/clear/read with tab_ids): Tasks 1, 2, 3, 5 ✓
- Persistence with `next_tab_id` counter + Phase 3 migration: Task 1 ✓
- WebSocket event kinds (new_tab, close_tab, set_active): Tasks 2, 4 ✓
- REST endpoints (new_tab, active_tab, close_tab, tab-locked GET): Task 4 ✓
- `code_block` widget: Task 8 ✓
- hljs vendor + chat hljs hook: Tasks 6, 7 ✓
- Canvas-state.js multi-tab: Task 9 ✓
- Tab strip + ARIA + keyboard nav: Task 10 ✓
- Mobile vertical list: Task 11 ✓
- Standalone tab-locked URL: Task 12 ✓
- markdown_document POST URL rename: Task 13 ✓
- Documentation: Task 14a ✓
- Manual smoke test: Task 14b ✓
- Validation/error cases: Task 2 + 3 tests ✓

**Type / name consistency:**
- `new_tab` / `update_tab` / `close_tab` / `set_active_tab` / `get_tab` (canvas.py) ↔ `tool_canvas_new_tab` / `tool_canvas_update` / `tool_canvas_close_tab` / `tool_canvas_clear` / `tool_canvas_read` (canvas_tools.py) — verbs and underscoring consistent.
- `CanvasOpResult { ok, text, error, tab_id }` shape used uniformly across canvas.py.
- WS event payload `{type, conv_id, kind, active_tab, tab, closed_tab_id?}` consistent in canvas.py emit, websocket.py forward, frontend canvas-state.js applyEvent, canvas-page.js.
- `{widget_type, data, label?}` body shape consistent across new_tab tool, REST endpoint, and widget Open-in-Canvas POSTs.

**Placeholder scan:**
- No "TBD" / "fill in" / "implement later" markers.
- One "locate during impl" caveat in Task 6 (vendor build script location depends on the project's existing build setup, which I haven't inspected this PR — the task gives a concrete grep pattern and an escalation path if the build setup is unobvious).
- Theme stylesheet snippet in Task 6 is full inline content (~100 lines of CSS rules). Long but no shortcuts.
