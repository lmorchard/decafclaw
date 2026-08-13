import asyncio
from unittest.mock import AsyncMock, patch
import pytest

from decafclaw.media import ToolResult
from decafclaw.conversation_paths import conversation_dir

@pytest.mark.asyncio
async def test_tool_output_exceeding_limit_is_managed(ctx):
    """When config.max_tool_output_bytes is set, output exceeding it is written to disk and truncated."""
    from decafclaw.tool_execution import execute_single_tool

    # Setup config limit
    ctx.config.max_tool_output_bytes = 100
    
    # Generate output > 100 bytes
    large_text = "A" * 150
    mock_result = ToolResult(text=large_text)

    tc = {
        "id": "call_123",
        "function": {"name": "test_tool", "arguments": "{}"},
    }
    semaphore = asyncio.Semaphore(1)

    with patch("decafclaw.tool_execution.execute_tool", new_callable=AsyncMock, return_value=mock_result):
        tool_msg, _ = await execute_single_tool(ctx, tc, semaphore)

    # Check that text is truncated
    content = tool_msg["content"]
    assert "A" * 150 not in content # Original string is removed
    assert "[Output truncated. Full output saved to" in content
    
    # Check that the file was written
    c_dir = conversation_dir(ctx.config, ctx.conv_id)
    output_path = c_dir / "tool_outputs" / "call_123.txt"
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8") == large_text

@pytest.mark.asyncio
async def test_read_managed_tool_output_file(ctx):
    """The 'read' tool should be able to inspect the truncated tool output file."""
    # We will simulate writing the output, then calling the 'read' tool on that path.
    from decafclaw.tool_execution import execute_single_tool
    
    ctx.config.max_tool_output_bytes = 100
    large_text = "B" * 200
    mock_result = ToolResult(text=large_text)

    tc = {
        "id": "call_abc",
        "function": {"name": "test_tool", "arguments": "{}"},
    }
    semaphore = asyncio.Semaphore(1)

    with patch("decafclaw.tool_execution.execute_tool", new_callable=AsyncMock, return_value=mock_result):
        tool_msg, _ = await execute_single_tool(ctx, tc, semaphore)
    
    content = tool_msg["content"]
    
    # Extract the file path from the truncated message
    # It ends with: [Output truncated. Full output saved to {filepath}. Use the 'read' tool to inspect it.]
    import re
    match = re.search(r"Full output saved to (.*?). Use the 'read' tool to inspect it", content)
    assert match is not None
    filepath = match.group(1)
    
    # Now simulate the read tool
    from decafclaw.tools.workspace_tools import tool_workspace_read
    
    # Check that tool_workspace_read can read it
    read_result = tool_workspace_read(ctx, filepath)
    if isinstance(read_result, ToolResult):
        read_result_text = read_result.text
    else:
        read_result_text = read_result
        
    assert "B" * 200 in read_result_text

