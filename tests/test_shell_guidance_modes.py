from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.config import Config
from decafclaw.context import Context
from decafclaw.events import EventBus
from decafclaw.tools.shell_tools import tool_shell_guidance


@pytest.fixture
def ctx(tmp_path):
    config = Config()
    config.agent.data_home = str(tmp_path)
    bus = EventBus()
    c = Context(config=config, event_bus=bus)
    c.conv_id = "test-conv-123"
    return c


@pytest.mark.asyncio
async def test_shell_guidance_list_shows_modes(ctx):
    ctx.active_mode = "dev"
    result = await tool_shell_guidance(ctx, action="list")
    assert isinstance(result, str)
    assert "### Session Modes" in result
    assert "**`dev`** [ACTIVE]" in result
    assert "**`research`**" in result
    assert "**`admin`**" in result
    assert "**`default`**" in result


@pytest.mark.asyncio
async def test_shell_guidance_set_mode_validation(ctx):
    # Missing mode param
    err1 = await tool_shell_guidance(ctx, action="set_mode")
    assert "[error: 'mode' is required for action 'set_mode']" in str(err1)

    # Unknown mode
    err2 = await tool_shell_guidance(ctx, action="set_mode", mode="nonexistent")
    assert "[error: unknown mode 'nonexistent'" in str(err2)


@pytest.mark.asyncio
async def test_shell_guidance_set_mode_unattended_denied(ctx):
    ctx.task_mode = "heartbeat"
    assert ctx.is_unattended is True

    err = await tool_shell_guidance(ctx, action="set_mode", mode="dev")
    assert "[error: denied on unattended turn" in str(err)
    assert ctx.active_mode == "default"


@pytest.mark.asyncio
async def test_shell_guidance_set_mode_user_denied(ctx):
    with patch(
        "decafclaw.tools.shell_tools.request_confirmation",
        new_callable=AsyncMock,
        return_value={"approved": False},
    ) as mock_confirm:
        result = await tool_shell_guidance(ctx, action="set_mode", mode="dev")
        assert "[error: denied]" in str(result)
        assert ctx.active_mode == "default"
        assert mock_confirm.called


@pytest.mark.asyncio
async def test_shell_guidance_set_mode_approved(ctx):
    ctx.tools.llm_approved_shell_patterns.append("cached_pattern")
    emitted_events = []

    async def fake_emit(event):
        emitted_events.append(event)

    ctx.manager = MagicMock()
    ctx.manager.emit = fake_emit

    with patch(
        "decafclaw.tools.shell_tools.request_confirmation",
        new_callable=AsyncMock,
        return_value={"approved": True},
    ) as mock_confirm:
        result = await tool_shell_guidance(ctx, action="set_mode", mode="dev")
        assert "Switched session mode to `dev`" in str(result)
        assert ctx.active_mode == "dev"
        assert set(ctx.tools.active_aux_approval_presets) == {"developer", "github"}
        # Cache cleared
        assert ctx.tools.llm_approved_shell_patterns == []
        # Event emitted
        assert len(emitted_events) == 1
        assert emitted_events[0]["type"] == "mode_changed"
        assert emitted_events[0]["mode"] == "dev"
        assert "developer" in emitted_events[0]["presets"]
        assert "workspace_diff" in emitted_events[0]["promoted_tools"]

        # Confirmation message should mention presets and promoted tools
        call_kwargs = mock_confirm.call_args.kwargs
        assert "developer" in call_kwargs["message"]
        assert "workspace_diff" in call_kwargs["message"]
