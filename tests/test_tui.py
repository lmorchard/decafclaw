import asyncio

import pytest

from decafclaw.config import Config
from decafclaw.conversation_manager import ConversationManager
from decafclaw.events import EventBus
from decafclaw.tui.app import DecafClawApp


@pytest.fixture
def manager():
    config = Config()
    bus = EventBus()
    return ConversationManager(config, bus)

@pytest.mark.asyncio
async def test_tui_appends_chunks(manager):
    app = DecafClawApp(manager=manager, config=Config(), event_bus=manager.event_bus)
    async with app.run_test() as pilot:
        await manager.emit("interactive", {"type": "chunk", "text": "Hello"})
        await pilot.pause(0.1)
        assert "Hello" in app.query_one("#message-log").my_text

@pytest.mark.asyncio
async def test_tui_shows_confirmation_modal(manager):
    app = DecafClawApp(manager=manager, config=Config(), event_bus=manager.event_bus)
    async with app.run_test() as pilot:
        await manager.emit("interactive", {
            "type": "confirmation_request",
            "confirmation_id": "123",
            "message": "Do you want to continue?",
            "action_type": "run_shell_command",
            "action_data": {"command": "ls -l"},
        })
        await pilot.pause(0.1)
        from decafclaw.tui.app import ConfirmationModal
        assert isinstance(app.screen, ConfirmationModal)

        # Test clicking yes
        await pilot.press("y")
        await pilot.pause(0.1)

@pytest.mark.asyncio
async def test_tui_switches_conversation_on_click(manager):
    app = DecafClawApp(manager=manager, config=Config(), event_bus=manager.event_bus)
    async with app.run_test() as pilot:
        app.query_one("#sidebar")
        app.switch_conversation("conv_2")
        await pilot.pause(0.1)
        assert app.active_conv_id == "conv_2"
