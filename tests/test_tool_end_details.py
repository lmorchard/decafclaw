"""UI-only tool details (#1654).

`ToolResult.details` carries the shell purpose and approval decision to the
final tool row without polluting the model-visible text. These tests pin the
wire projection and the archive shape.
"""

from __future__ import annotations

from decafclaw.media import ToolResult
from decafclaw.web.message_types import WSMessageType
from decafclaw.web.websocket import _project_tool_end


def test_projects_details_when_present():
    out = _project_tool_end(
        {
            "tool": "shell",
            "result_text": "ok",
            "tool_call_id": "tc1",
            "details": {
                "purpose": "check the tree",
                "approval": {"decision": "allow", "tier": "read_only", "reason": ""},
            },
        },
        "conv-1",
    )

    assert out["type"] == WSMessageType.TOOL_END
    assert out["details"] == {
        "purpose": "check the tree",
        "approval": {"decision": "allow", "tier": "read_only", "reason": ""},
    }


def test_omits_details_when_absent():
    out = _project_tool_end({"tool": "web_fetch", "result_text": "ok", "tool_call_id": "tc2"}, "conv-1")

    assert "details" not in out


def test_tool_result_defaults_details_to_none():
    assert ToolResult(text="ok").details is None
