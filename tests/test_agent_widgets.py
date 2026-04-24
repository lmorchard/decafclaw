"""Tests for widget validation + serialization in the agent tool-execution path."""

import json

import pytest

from decafclaw import widgets as widgets_module
from decafclaw.agent import _execute_tool_calls, _resolve_widget
from decafclaw.archive import read_archive
from decafclaw.media import ToolResult, WidgetRequest

_PANEL_SCHEMA = {
    "type": "object",
    "required": ["columns", "rows"],
    "properties": {
        "columns": {"type": "array"},
        "rows": {"type": "array"},
    },
}


def _make_test_registry(tmp_path):
    """Build a registry with a single 'data_table' widget for tests."""
    bundled = tmp_path / "bundled"
    d = bundled / "data_table"
    d.mkdir(parents=True)
    (d / "widget.json").write_text(json.dumps({
        "name": "data_table",
        "description": "test",
        "modes": ["inline"],
        "data_schema": _PANEL_SCHEMA,
    }))
    (d / "widget.js").write_text("// stub\n")

    class _Cfg:
        agent_path = tmp_path / "agent_home"
    return widgets_module.load_widget_registry(
        _Cfg(), bundled_dir=bundled, admin_dir=tmp_path / "admin")


@pytest.fixture
def test_registry(tmp_path, monkeypatch):
    registry = _make_test_registry(tmp_path)
    monkeypatch.setattr(widgets_module, "_registry", registry)
    yield registry


# -------------- _resolve_widget unit tests --------------


def test_resolve_widget_valid(test_registry):
    result = ToolResult(text="ok", widget=WidgetRequest(
        widget_type="data_table",
        data={"columns": [{"key": "a", "label": "A"}], "rows": []}))
    payload = _resolve_widget("my_tool", result)
    assert payload is not None
    assert payload["widget_type"] == "data_table"
    assert payload["target"] == "inline"
    assert payload["data"]["columns"][0]["key"] == "a"
    # result.widget remains set on success
    assert result.widget is not None


def test_resolve_widget_invalid_data_strips(test_registry, caplog):
    result = ToolResult(text="ok", widget=WidgetRequest(
        widget_type="data_table",
        data={"columns": []}))  # missing rows
    payload = _resolve_widget("my_tool", result)
    assert payload is None
    assert result.widget is None  # stripped
    assert any("failed validation" in r.message for r in caplog.records)


def test_resolve_widget_unknown_type_strips(test_registry, caplog):
    result = ToolResult(text="ok", widget=WidgetRequest(
        widget_type="nonexistent_widget",
        data={"anything": 1}))
    payload = _resolve_widget("my_tool", result)
    assert payload is None
    assert result.widget is None
    assert any("failed validation" in r.message for r in caplog.records)


def test_resolve_widget_no_widget(test_registry):
    result = ToolResult(text="no widget here")
    payload = _resolve_widget("my_tool", result)
    assert payload is None


def test_resolve_widget_unknown_target_strips(test_registry, caplog):
    result = ToolResult(text="ok", widget=WidgetRequest(
        widget_type="data_table",
        data={"columns": [], "rows": []},
        target="bogus"))
    payload = _resolve_widget("my_tool", result)
    assert payload is None
    assert result.widget is None
    assert any("unknown target" in r.message for r in caplog.records)


def test_resolve_widget_target_not_in_modes_strips(
        tmp_path, monkeypatch, caplog):
    """A target that's a valid name but not in the widget's declared
    modes should be stripped."""
    bundled = tmp_path / "bundled"
    d = bundled / "inline_only"
    d.mkdir(parents=True)
    (d / "widget.json").write_text(
        '{"name": "inline_only", "description": "x", "modes": ["inline"], '
        '"data_schema": {"type": "object"}}')
    (d / "widget.js").write_text("// stub")

    class _Cfg:
        agent_path = tmp_path / "agent_home"
    registry = widgets_module.load_widget_registry(
        _Cfg(), bundled_dir=bundled, admin_dir=tmp_path / "admin")
    monkeypatch.setattr(widgets_module, "_registry", registry)

    result = ToolResult(text="ok", widget=WidgetRequest(
        widget_type="inline_only",
        data={},
        target="canvas"))  # not in modes
    payload = _resolve_widget("my_tool", result)
    assert payload is None
    assert result.widget is None
    assert any("not in declared modes" in r.message for r in caplog.records)


def test_resolve_widget_no_registry(monkeypatch, caplog):
    monkeypatch.setattr(widgets_module, "_registry", None)
    result = ToolResult(text="ok", widget=WidgetRequest(
        widget_type="data_table",
        data={"columns": [], "rows": []}))
    payload = _resolve_widget("my_tool", result)
    assert payload is None
    assert result.widget is None
    assert any("registry is not" in r.message for r in caplog.records)


# -------------- _execute_tool_calls integration tests --------------


async def _fake_execute_tool_with_widget(call_ctx, fn_name, fn_args):
    """Stand-in for execute_tool that returns a widget."""
    return ToolResult(
        text="Found 1 result",
        display_short_text="1 result",
        widget=WidgetRequest(
            widget_type="data_table",
            data={"columns": [{"key": "page", "label": "Page"}],
                  "rows": [{"page": "Hello"}]}))


async def _fake_execute_tool_with_bad_widget(call_ctx, fn_name, fn_args):
    return ToolResult(
        text="Found 1 result",
        widget=WidgetRequest(widget_type="data_table",
                             data={"columns": []}))  # missing rows


async def _fake_execute_tool_no_widget(call_ctx, fn_name, fn_args):
    return ToolResult(text="just text")


@pytest.mark.asyncio
async def test_execute_tool_calls_propagates_valid_widget(
        ctx, config, test_registry, monkeypatch):
    monkeypatch.setattr(
        "decafclaw.agent.execute_tool", _fake_execute_tool_with_widget)
    ctx.conv_id = "test-widget-valid"
    tool_calls = [{"id": "tc1",
                   "function": {"name": "fake_tool", "arguments": "{}"}}]
    history = []
    messages = []

    events_seen: list[dict] = []

    async def _capture(ev):
        events_seen.append(ev)

    ctx.event_bus.subscribe(_capture)

    await _execute_tool_calls(ctx, tool_calls, history, messages)

    # History tool record carries widget payload
    tool_msgs = [m for m in history if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["widget"]["widget_type"] == "data_table"
    assert tool_msgs[0]["widget"]["target"] == "inline"

    # Archive round-trips the widget
    archived = read_archive(config, "test-widget-valid")
    tool_archived = [m for m in archived if m.get("role") == "tool"]
    assert len(tool_archived) == 1
    assert tool_archived[0]["widget"]["widget_type"] == "data_table"

    # tool_end event carries widget
    tool_end_events = [e for e in events_seen
                       if e.get("type") == "tool_end"]
    assert tool_end_events, "tool_end event not observed"
    assert tool_end_events[0]["widget"]["widget_type"] == "data_table"


@pytest.mark.asyncio
async def test_execute_tool_calls_strips_invalid_widget(
        ctx, config, test_registry, monkeypatch, caplog):
    monkeypatch.setattr(
        "decafclaw.agent.execute_tool", _fake_execute_tool_with_bad_widget)
    ctx.conv_id = "test-widget-invalid"
    tool_calls = [{"id": "tc1",
                   "function": {"name": "fake_tool", "arguments": "{}"}}]
    history = []
    messages = []

    events_seen: list[dict] = []

    async def _capture(ev):
        events_seen.append(ev)

    ctx.event_bus.subscribe(_capture)

    await _execute_tool_calls(ctx, tool_calls, history, messages)

    tool_msgs = [m for m in history if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "widget" not in tool_msgs[0]

    archived = read_archive(config, "test-widget-invalid")
    tool_archived = [m for m in archived if m.get("role") == "tool"]
    assert "widget" not in tool_archived[0]

    tool_end_events = [e for e in events_seen
                       if e.get("type") == "tool_end"]
    assert tool_end_events
    assert "widget" not in tool_end_events[0]
    assert any("failed validation" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_execute_tool_calls_no_widget(
        ctx, config, test_registry, monkeypatch):
    """Tools that don't set widget produce tool_end/archive records
    with no widget key (not widget:null)."""
    monkeypatch.setattr(
        "decafclaw.agent.execute_tool", _fake_execute_tool_no_widget)
    ctx.conv_id = "test-no-widget"
    tool_calls = [{"id": "tc1",
                   "function": {"name": "fake_tool", "arguments": "{}"}}]
    history = []
    messages = []

    await _execute_tool_calls(ctx, tool_calls, history, messages)

    tool_msgs = [m for m in history if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "widget" not in tool_msgs[0]
