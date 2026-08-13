import pytest
from decafclaw.archive import append_message
from decafclaw.friction import analyze_friction
from decafclaw.commands import dispatch_command

@pytest.mark.asyncio
async def test_friction_analysis_groups_corrections(config):
    # create some fake archives with corrections across multiple sessions
    append_message(config, "sess-1", {"role": "user", "content": "No, I told you to always use standard standard logging."})
    append_message(config, "sess-1", {"role": "assistant", "content": "Sorry."})
    
    append_message(config, "sess-2", {"role": "user", "content": "Stop using print statements, use the logger!"})
    append_message(config, "sess-2", {"role": "assistant", "content": "My apologies."})
    
    append_message(config, "sess-3", {"role": "user", "content": "Can we please stick to the logger pattern? No print allowed."})
    append_message(config, "sess-3", {"role": "assistant", "content": "Yes, of course."})
    
    # unrelated messages
    append_message(config, "sess-4", {"role": "user", "content": "How are you today?"})
    
    themes = await analyze_friction(config)
    
    assert len(themes) > 0, "No themes extracted"
    assert any("logger" in t.theme.lower() or "print" in t.theme.lower() for t in themes), "Did not extract the logging/print theme"
    assert any(t.occurrences >= 3 for t in themes), "Occurrences count should reflect multiple sessions"

@pytest.mark.asyncio
async def test_friction_command_execution(ctx):
    # This should dispatch '!friction' and we should get a mode="fork" or mode="inline" and text
    result = await dispatch_command(ctx, "!friction")
    
    assert result.mode != "not_command"
    assert result.mode != "unknown", "Command !friction not recognized"
    
    # The result should contain proposed updates
    assert "proposed" in result.text.lower() or "theme" in result.text.lower()
