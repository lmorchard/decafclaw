import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.media import ToolResult
from decafclaw.tool_execution import execute_single_tool


@pytest.mark.asyncio
async def test_tool_output_exceeding_limit_is_managed(ctx):
    ctx.config.max_tool_output_bytes = 100

    # 150 bytes text
    long_text = "A" * 150
    mock_result = ToolResult(text=long_text)

    tc = {
        "id": "call_123",
        "function": {"name": "test_tool", "arguments": "{}"},
    }
    semaphore = asyncio.Semaphore(1)

    with patch("decafclaw.tool_execution.execute_tool", new_callable=AsyncMock, return_value=mock_result):
        tool_msg, _ = await execute_single_tool(ctx, tc, semaphore)

    content = tool_msg["content"]
    assert "Output truncated" in content
    assert "Full output saved to" in content
    assert "Use the 'read' tool to inspect it." in content

    # Verify the truncated size
    # The file path logic puts it in:
    # workspace/conversations/{conv_id}/tool_outputs/{tool_call_id}.txt
    from decafclaw.conversation_paths import conversation_dir
    tool_outputs_dir = conversation_dir(ctx.config, ctx.conv_id, create=False) / "tool_outputs"
    filepath = tool_outputs_dir / "call_123.txt"

    assert str(filepath) in content
    assert filepath.exists()
    assert filepath.read_text() == long_text


@pytest.mark.asyncio
async def test_read_managed_tool_output_file(ctx):
    # Setup test file
    ctx.config.max_tool_output_bytes = 100
    long_text = "B" * 150

    from decafclaw.conversation_paths import conversation_dir
    tool_outputs_dir = conversation_dir(ctx.config, ctx.conv_id, create=True) / "tool_outputs"
    tool_outputs_dir.mkdir(parents=True, exist_ok=True)
    filepath = tool_outputs_dir / "call_123.txt"
    filepath.write_text(long_text)

    # Test reading the file using the read tool
    from decafclaw.tools.workspace_tools import tool_workspace_read

    result = tool_workspace_read(ctx, path=str(filepath))
    assert result.text.strip() == f"1| {long_text}"


@pytest.mark.asyncio
async def test_tool_output_under_limit_is_unmodified(ctx):
    ctx.config.max_tool_output_bytes = 100

    # 50 bytes text
    short_text = "A" * 50
    mock_result = ToolResult(text=short_text)

    tc = {
        "id": "call_124",
        "function": {"name": "test_tool", "arguments": "{}"},
    }
    semaphore = asyncio.Semaphore(1)

    with patch("decafclaw.tool_execution.execute_tool", new_callable=AsyncMock, return_value=mock_result):
        tool_msg, _ = await execute_single_tool(ctx, tc, semaphore)

    content = tool_msg["content"]
    assert content == short_text
    assert "Output truncated" not in content
