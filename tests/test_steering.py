import asyncio
import json
import pytest
from unittest.mock import patch, AsyncMock
from decafclaw.conversation_manager import ConversationManager, TurnKind
from decafclaw.config import load_config
from decafclaw.media import ToolResult

@pytest.fixture
def config():
    c = load_config()
    c.agent.turn_on_new_message = "ignore" # Default behavior for testing
    c.reflection.enabled = False # disable reflection to avoid extra LLM calls
    return c

@pytest.fixture
def event_bus():
    from decafclaw.events import EventBus
    return EventBus()

@pytest.fixture
def manager(config, event_bus):
    return ConversationManager(config, event_bus)

def _mock_llm_response(content, tool_calls=None):
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
        "usage": {"prompt_tokens": 10, "completion_tokens": 10}
    }

@pytest.mark.asyncio
async def test_steering_interrupts_after_tool_call(manager):
    tool_call_response = _mock_llm_response(
        content=None,
        tool_calls=[{
            "id": "tc1",
            "function": {
                "name": "notes_read",
                "arguments": json.dumps({"limit": 1}),
            },
        }],
    )
    final_response = _mock_llm_response("Here are your memories.")
    
    tool_started = asyncio.Event()
    tool_resume = asyncio.Event()
    
    async def slow_execute_tool(ctx, name, args):
        tool_started.set()
        await tool_resume.wait()
        return ToolResult(text="slow memory")
        
    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [tool_call_response, final_response, final_response, final_response]
        
        with patch("decafclaw.tool_execution.execute_tool", side_effect=slow_execute_tool):
            first_future = await manager.enqueue_turn(
                conv_id="c1",
                kind=TurnKind.USER,
                prompt="show memories",
                user_id="u",
            )
            
            # Wait until the tool starts executing
            await asyncio.wait_for(tool_started.wait(), 5.0)
            
            # Now send the steering message
            steer_future = await manager.enqueue_turn(
                conv_id="c1",
                kind=TurnKind.USER,
                prompt="ACTUALLY stop and do something else",
                user_id="u",
                metadata={"steering": True}
            )
            
            # Resume the tool
            tool_resume.set()
            
            # Wait for first turn to finish
            await asyncio.wait_for(first_future, 5.0)
            
            assert mock_llm.call_count == 1, "LLM was called after tool completed despite steering message!"

@pytest.mark.asyncio
async def test_follow_up_message_queued(manager):
    tool_call_response = _mock_llm_response(
        content=None,
        tool_calls=[{
            "id": "tc1",
            "function": {
                "name": "notes_read",
                "arguments": json.dumps({"limit": 1}),
            },
        }],
    )
    final_response = _mock_llm_response("Here are your memories.")
    steer_response = _mock_llm_response("Got the follow up.")
    
    tool_started = asyncio.Event()
    tool_resume = asyncio.Event()
    
    async def slow_execute_tool(ctx, name, args):
        tool_started.set()
        await tool_resume.wait()
        return ToolResult(text="slow memory")
        
    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [tool_call_response, final_response, steer_response, steer_response]
        
        with patch("decafclaw.tool_execution.execute_tool", side_effect=slow_execute_tool):
            first_future = await manager.enqueue_turn(
                conv_id="c2",
                kind=TurnKind.USER,
                prompt="show memories",
                user_id="u",
            )
            
            await asyncio.wait_for(tool_started.wait(), 5.0)
            
            follow_up_future = await manager.enqueue_turn(
                conv_id="c2",
                kind=TurnKind.USER,
                prompt="Also fetch tags",
                user_id="u",
            )
            
            tool_resume.set()
            await asyncio.wait_for(first_future, 5.0)
            await asyncio.wait_for(follow_up_future, 5.0)
            
            # Follow-up should queue, so 1st turn takes 2 calls, 2nd turn takes 1 call.
            assert mock_llm.call_count == 3
            
            state = manager.get_state("c2")
            assert len(state.history) == 6
            assert state.history[4]["content"] == "Also fetch tags"
