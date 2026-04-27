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


async def test_set_canvas_replaces_existing_tab(config, md_doc_registry, emit_recorder):
    await canvas.set_canvas(config, "c", "markdown_document",
                            {"content": "first"}, emit=emit_recorder)
    await canvas.set_canvas(config, "c", "markdown_document",
                            {"content": "second"}, emit=emit_recorder)
    state = canvas.read_canvas_state(config, "c")
    assert len(state["tabs"]) == 1
    assert state["tabs"][0]["data"]["content"] == "second"


async def test_set_canvas_unknown_widget(config, md_doc_registry, emit_recorder):
    result = await canvas.set_canvas(
        config, "c", "no_such_widget", {"x": 1}, emit=emit_recorder,
    )
    assert not result.ok
    assert "not registered" in result.error
    assert canvas.read_canvas_state(config, "c") == canvas.empty_canvas_state()
    assert emit_recorder.events == []


async def test_set_canvas_widget_without_canvas_mode(config, md_doc_registry, emit_recorder):
    result = await canvas.set_canvas(
        config, "c", "data_table", {}, emit=emit_recorder,
    )
    assert not result.ok
    assert "does not support canvas mode" in result.error


async def test_set_canvas_invalid_data(config, md_doc_registry, emit_recorder):
    result = await canvas.set_canvas(
        config, "c", "markdown_document", {"wrong_field": "x"},
        emit=emit_recorder,
    )
    assert not result.ok
    assert "schema validation failed" in result.error


async def test_set_canvas_default_label_from_h1(config, md_doc_registry, emit_recorder):
    await canvas.set_canvas(
        config, "c", "markdown_document",
        {"content": "# Project Summary\n\nSome text"},
        emit=emit_recorder,
    )
    state = canvas.read_canvas_state(config, "c")
    assert state["tabs"][0]["label"] == "Project Summary"


async def test_set_canvas_default_label_fallback(config, md_doc_registry, emit_recorder):
    await canvas.set_canvas(
        config, "c", "markdown_document",
        {"content": "no heading here"},
        emit=emit_recorder,
    )
    state = canvas.read_canvas_state(config, "c")
    assert state["tabs"][0]["label"] == "Untitled"


async def test_update_canvas_with_no_tab_fails(config, md_doc_registry, emit_recorder):
    result = await canvas.update_canvas(
        config, "c", {"content": "x"}, emit=emit_recorder,
    )
    assert not result.ok
    assert "no canvas widget set" in result.error
    assert emit_recorder.events == []


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


async def test_update_canvas_invalid_data(config, md_doc_registry, emit_recorder):
    await canvas.set_canvas(config, "c", "markdown_document",
                            {"content": "v1"}, emit=emit_recorder)
    result = await canvas.update_canvas(
        config, "c", {"oops": True}, emit=emit_recorder,
    )
    assert not result.ok
    assert "schema validation failed" in result.error


async def test_clear_canvas_when_empty(config, md_doc_registry, emit_recorder):
    result = await canvas.clear_canvas(config, "c", emit=emit_recorder)
    assert result.ok
    assert result.text == "canvas already empty"
    assert emit_recorder.events == []


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


async def test_get_active_tab_present(config, md_doc_registry, emit_recorder):
    await canvas.set_canvas(
        config, "c", "markdown_document",
        {"content": "x"}, emit=emit_recorder,
    )
    tab = canvas.get_active_tab(config, "c")
    assert tab is not None
    assert tab["widget_type"] == "markdown_document"
