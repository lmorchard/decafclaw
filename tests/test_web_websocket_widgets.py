"""Tests for widget projection in the WebSocket tool_end forwarder."""

from decafclaw.web.websocket import _project_tool_end


def test_tool_end_without_widget():
    payload = _project_tool_end({
        "type": "tool_end",
        "tool": "vault_search",
        "result_text": "Found 3 results",
        "tool_call_id": "tc-1",
    }, conv_id="conv-a")
    assert payload == {
        "type": "tool_end",
        "conv_id": "conv-a",
        "tool": "vault_search",
        "result_text": "Found 3 results",
        "tool_call_id": "tc-1",
    }
    assert "widget" not in payload
    assert "display_short_text" not in payload


def test_tool_end_with_display_short():
    payload = _project_tool_end({
        "type": "tool_end",
        "tool": "x",
        "display_short_text": "short",
        "result_text": "full",
        "tool_call_id": "tc-2",
    }, conv_id="c")
    assert payload["display_short_text"] == "short"


def test_tool_end_with_widget():
    widget = {
        "widget_type": "data_table",
        "target": "inline",
        "data": {"columns": [], "rows": []},
    }
    payload = _project_tool_end({
        "type": "tool_end",
        "tool": "vault_search",
        "result_text": "Found 1 result",
        "tool_call_id": "tc-3",
        "widget": widget,
    }, conv_id="c")
    assert payload["widget"] == widget


def test_tool_end_widget_null_omitted():
    """An explicit None widget value stays out of the payload (truthy check)."""
    payload = _project_tool_end({
        "type": "tool_end",
        "tool": "x",
        "result_text": "t",
        "tool_call_id": "tc-4",
        "widget": None,
    }, conv_id="c")
    assert "widget" not in payload


def test_tool_end_falls_back_to_caller_conv_id():
    """If the event lacks conv_id, the forwarder uses the conv it belongs to."""
    payload = _project_tool_end({
        "type": "tool_end",
        "tool": "x",
        "result_text": "t",
        "tool_call_id": "tc-5",
        # no conv_id
    }, conv_id="caller-conv")
    assert payload["conv_id"] == "caller-conv"
