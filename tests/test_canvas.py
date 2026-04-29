"""Tests for canvas.py — per-conversation canvas state sidecar."""

import json
from types import SimpleNamespace

import pytest

from decafclaw import canvas


@pytest.fixture
def config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SimpleNamespace(workspace_path=workspace)


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
        "next_tab_id": 2,
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
        for r in desc.required:
            if r not in data:
                return False, f"missing required field '{r}'"
        return True, None

    def normalize(self, name, data):
        # No registered normalizers in tests — pass through.
        return data


@pytest.fixture
def md_doc_registry(monkeypatch):
    reg = _FakeRegistry({
        "markdown_document": SimpleNamespace(modes=["inline", "canvas"], required=["content"]),
        "data_table": SimpleNamespace(modes=["inline"], required=[]),
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


async def test_clear_canvas_when_empty(config, md_doc_registry, emit_recorder):
    result = await canvas.clear_canvas(config, "c", emit=emit_recorder)
    assert result.ok
    assert result.text == "canvas already empty"
    assert emit_recorder.events == []


async def test_clear_canvas_with_tab(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document",
                         {"content": "v1"}, emit=emit_recorder)
    emit_recorder.events.clear()
    result = await canvas.clear_canvas(config, "c", emit=emit_recorder)
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    assert state["tabs"] == []
    assert state["active_tab"] is None
    _, event = emit_recorder.events[0]
    assert event["kind"] == "clear"
    assert event["tab"] is None


async def test_clear_canvas_preserves_next_tab_id(config, md_doc_registry,
                                                   emit_recorder):
    """Clear must not reset next_tab_id — closed ids never get rebound."""
    await canvas.new_tab(config, "c", "markdown_document",
                         {"content": "a"}, emit=emit_recorder)
    await canvas.new_tab(config, "c", "markdown_document",
                         {"content": "b"}, emit=emit_recorder)
    state = canvas.read_canvas_state(config, "c")
    assert state["next_tab_id"] == 3

    await canvas.clear_canvas(config, "c", emit=emit_recorder)
    state = canvas.read_canvas_state(config, "c")
    assert state["next_tab_id"] == 3, \
        "clear must preserve next_tab_id so a new tab gets a fresh id"

    result = await canvas.new_tab(config, "c", "markdown_document",
                                   {"content": "c"}, emit=emit_recorder)
    assert result.tab_id == "canvas_3"


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


# ---------------------------------------------------------------------------
# Phase 4 tab-aware state ops
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Per-widget normalization (e.g. iframe_sandbox CSP wrapping)
# ---------------------------------------------------------------------------

class _NormalizingFakeRegistry(_FakeRegistry):
    """Fake registry that runs a normalize hook by widget name."""

    def __init__(self, descriptors, normalizers):
        super().__init__(descriptors)
        self._normalizers = normalizers

    def normalize(self, name, data):
        fn = self._normalizers.get(name)
        return fn(data) if fn else data


@pytest.mark.asyncio
async def test_new_tab_runs_normalize(config, monkeypatch, emit_recorder):
    """new_tab should invoke registry.normalize after validate, so widgets
    like iframe_sandbox get their server-controlled fields injected before
    state is persisted or events are emitted."""
    def normalize_iframe(data):
        return {**data, "html": f"WRAPPED:{data.get('body', '')}"}

    reg = _NormalizingFakeRegistry(
        descriptors={
            "iframe_sandbox": SimpleNamespace(modes=["inline", "canvas"],
                                              required=["body"]),
        },
        normalizers={"iframe_sandbox": normalize_iframe},
    )
    monkeypatch.setattr(canvas, "get_widget_registry", lambda: reg)

    result = await canvas.new_tab(
        config, "c", "iframe_sandbox",
        {"body": "<p>hi</p>"}, emit=emit_recorder,
    )
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    stored = state["tabs"][0]["data"]
    # Normalized form: original body preserved, html field added.
    assert stored["body"] == "<p>hi</p>"
    assert stored["html"] == "WRAPPED:<p>hi</p>"
    # Emitted event also carries the normalized data.
    _, event = emit_recorder.events[0]
    assert event["tab"]["data"]["html"] == "WRAPPED:<p>hi</p>"


@pytest.mark.asyncio
async def test_update_tab_runs_normalize(config, monkeypatch, emit_recorder):
    """update_tab must re-run normalize so a stale html field can't survive
    a round-trip."""
    def normalize_iframe(data):
        return {**data, "html": f"WRAPPED:{data.get('body', '')}"}

    reg = _NormalizingFakeRegistry(
        descriptors={
            "iframe_sandbox": SimpleNamespace(modes=["inline", "canvas"],
                                              required=["body"]),
        },
        normalizers={"iframe_sandbox": normalize_iframe},
    )
    monkeypatch.setattr(canvas, "get_widget_registry", lambda: reg)

    r = await canvas.new_tab(config, "c", "iframe_sandbox",
                             {"body": "v1"}, emit=emit_recorder)
    emit_recorder.events.clear()
    result = await canvas.update_tab(
        config, "c", r.tab_id,
        # Agent passes a stale html alongside fresh body — normalize should overwrite it.
        {"body": "v2", "html": "STALE"},
        emit=emit_recorder,
    )
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    stored = state["tabs"][0]["data"]
    assert stored["body"] == "v2"
    assert stored["html"] == "WRAPPED:v2"


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
    await canvas.new_tab(config, "c", "markdown_document", {"content": "2"}, emit=emit_recorder)
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


@pytest.mark.asyncio
async def test_get_tab_by_id(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document",
                        {"content": "x"}, label="L", emit=emit_recorder)
    tab = canvas.get_tab(config, "c", "canvas_1")
    assert tab is not None
    assert tab["label"] == "L"
    assert canvas.get_tab(config, "c", "canvas_99") is None
