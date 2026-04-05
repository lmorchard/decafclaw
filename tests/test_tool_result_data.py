"""Tests for ToolResult.data field and JSON serialization in tool messages."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.media import ToolResult


def test_data_field_accessible():
    """ToolResult.data stores structured data dict."""
    result = ToolResult(text="hello", data={"exit_status": "success", "cost": 1.23})
    assert result.data == {"exit_status": "success", "cost": 1.23}


def test_data_defaults_to_none():
    """ToolResult.data is None by default."""
    result = ToolResult(text="hello")
    assert result.data is None


def test_data_none_explicit():
    """Explicit data=None is the same as default."""
    result = ToolResult(text="hello", data=None)
    assert result.data is None


def test_json_block_appended_when_data_present():
    """When data is set, a fenced JSON block should be appendable to text."""
    result = ToolResult(text="hello", data={"k": "v"})
    content = result.text
    if result.data is not None:
        content += "\n\n```json\n" + json.dumps(result.data, indent=2) + "\n```"

    assert content.startswith("hello")
    assert "```json" in content
    assert "```" in content

    # Extract and validate the JSON block
    json_start = content.index("```json\n") + len("```json\n")
    json_end = content.index("\n```", json_start)
    parsed = json.loads(content[json_start:json_end])
    assert parsed == {"k": "v"}


def test_no_json_block_when_data_is_none():
    """When data is None, text should be unchanged."""
    result = ToolResult(text="hello", data=None)
    content = result.text
    if result.data is not None:
        content += "\n\n```json\n" + json.dumps(result.data, indent=2) + "\n```"

    assert content == "hello"


def test_complex_data_serializes():
    """Nested dicts and lists serialize correctly."""
    data = {
        "files_changed": ["src/foo.py", "tests/test_foo.py"],
        "tools_used": {"Edit": 3, "Read": 5},
        "errors": [{"message": "ImportError", "file": "src/bar.py"}],
        "cost_usd": 0.45,
        "exit_code": None,
    }
    result = ToolResult(text="done", data=data)
    content = result.text + "\n\n```json\n" + json.dumps(result.data, indent=2) + "\n```"

    json_start = content.index("```json\n") + len("```json\n")
    json_end = content.index("\n```", json_start)
    parsed = json.loads(content[json_start:json_end])
    assert parsed == data


@pytest.mark.asyncio
async def test_agent_loop_appends_json_block(ctx):
    """The actual agent loop appends a JSON block when ToolResult.data is set."""
    from decafclaw.agent import _execute_single_tool

    test_data = {"exit_status": "success", "cost_usd": 1.23}
    mock_result = ToolResult(text="task completed", data=test_data)

    tc = {
        "id": "call_123",
        "function": {"name": "test_tool", "arguments": "{}"},
    }
    semaphore = asyncio.Semaphore(1)

    with patch("decafclaw.agent.execute_tool", new_callable=AsyncMock, return_value=mock_result):
        tool_msg = await _execute_single_tool(ctx, tc, semaphore)

    assert tool_msg["role"] == "tool"
    assert tool_msg["content"].startswith("task completed")
    assert "```json" in tool_msg["content"]

    # Extract and validate the JSON block
    content = tool_msg["content"]
    json_start = content.index("```json\n") + len("```json\n")
    json_end = content.index("\n```", json_start)
    parsed = json.loads(content[json_start:json_end])
    assert parsed == test_data


@pytest.mark.asyncio
async def test_agent_loop_no_json_block_without_data(ctx):
    """No JSON block when ToolResult.data is None."""
    from decafclaw.agent import _execute_single_tool

    mock_result = ToolResult(text="plain result")
    tc = {
        "id": "call_456",
        "function": {"name": "test_tool", "arguments": "{}"},
    }
    semaphore = asyncio.Semaphore(1)

    with patch("decafclaw.agent.execute_tool", new_callable=AsyncMock, return_value=mock_result):
        tool_msg = await _execute_single_tool(ctx, tc, semaphore)

    assert tool_msg["content"] == "plain result"
    assert "```json" not in tool_msg["content"]


@pytest.mark.asyncio
async def test_agent_loop_handles_unserializable_data(ctx):
    """Non-serializable data doesn't crash the tool call."""
    from decafclaw.agent import _execute_single_tool

    mock_result = ToolResult(text="result", data={"bad": object()})
    tc = {
        "id": "call_789",
        "function": {"name": "test_tool", "arguments": "{}"},
    }
    semaphore = asyncio.Semaphore(1)

    with patch("decafclaw.agent.execute_tool", new_callable=AsyncMock, return_value=mock_result):
        tool_msg = await _execute_single_tool(ctx, tc, semaphore)

    assert "result" in tool_msg["content"]
    assert "serialization error" in tool_msg["content"]
    assert "```json" not in tool_msg["content"]
