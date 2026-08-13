import pytest
from decafclaw.tui.app import DecafClawApp
from decafclaw.events import EventBus
from decafclaw.config import Config
from decafclaw.conversation_manager import ConversationManager
import asyncio

@pytest.fixture
def manager():
    config = Config()
    bus = EventBus()
    return ConversationManager(config, bus)

@pytest.mark.asyncio
async def test_tui_appends_chunks(manager):
    app = DecafClawApp(manager=manager, config=Config(), event_bus=manager.event_bus)
    async with app.run_test() as pilot:
        await manager.event_bus.publish("chunk", {"type": "chunk", "text": "Hello", "conv_id": "tui"})
        await pilot.pause(0.1)
        # Wait, the app needs to process the event and append to the message widget
        # Assert that the text is updated.
        assert "Hello" in app.query_one("#message-log").text

@pytest.mark.asyncio
async def test_tui_shows_confirmation_modal(manager):
    app = DecafClawApp(manager=manager, config=Config(), event_bus=manager.event_bus)
    async with app.run_test() as pilot:
        await manager.event_bus.publish("confirmation_request", {
            "type": "confirmation_request",
            "confirmation_id": "123",
            "message": "Do you want to continue?",
            "action_type": "run_shell_command",
            "action_data": {"command": "ls -l"},
            "conv_id": "tui"
        })
        await pilot.pause(0.1)
        # assert that a modal is shown
        from decafclaw.tui.app import ConfirmationModal
        assert isinstance(app.screen, ConfirmationModal)

@pytest.mark.asyncio
async def test_tui_switches_conversation_on_click(manager):
    app = DecafClawApp(manager=manager, config=Config(), event_bus=manager.event_bus)
    async with app.run_test() as pilot:
        # Assuming there is a Sidebar that we can click on
        # and it changes the active conversation.
        sidebar = app.query_one("#sidebar")
        # For now, just test that the conversation changes.
        app.switch_conversation("conv_2")
        await pilot.pause(0.1)
        assert app.active_conv_id == "conv_2"

