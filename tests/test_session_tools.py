"""Unit tests for session search, listing, switching, and home tools (#1609)."""

import pytest

from decafclaw.events import EventBus
from decafclaw.tools.session_tools import (
    tool_session_create,
    tool_session_home,
    tool_session_list,
    tool_session_search,
    tool_session_switch,
)
from decafclaw.web.conversations import ConversationIndex


@pytest.fixture
def session_ctx(config):
    from decafclaw.context import Context

    bus = EventBus()
    context = Context(config=config, event_bus=bus)
    context.conv_id = "web-lmorchard-current"
    context.channel_id = "test-channel"
    context.user_id = "lmorchard"
    return context


class TestHomeConversation:
    def test_get_or_create_home_creates_and_idempotent(self, config):
        index = ConversationIndex(config)
        home = index.get_or_create_home("lmorchard")
        assert home.conv_id == "web-lmorchard-home"
        assert home.title == "Lobby"
        assert home.custom_title is True

        # Second call returns existing without duplicating
        home2 = index.get_or_create_home("lmorchard")
        assert home2.conv_id == home.conv_id
        assert len([c for c in index.list_for_user("lmorchard") if c.conv_id == "web-lmorchard-home"]) == 1


class TestSessionTools:
    @pytest.mark.asyncio
    async def test_session_list_empty(self, session_ctx):
        res = await tool_session_list(session_ctx)
        assert "No conversation sessions found" in res.text

    @pytest.mark.asyncio
    async def test_session_list_and_search(self, session_ctx):
        index = ConversationIndex(session_ctx.config)
        index.create("lmorchard", title="Fungal Planet Short Story")
        index.create("lmorchard", title="Homelab Edge Routing")
        index.get_or_create_home("lmorchard")

        # List
        list_res = await tool_session_list(session_ctx, limit=10)
        assert "Fungal Planet Short Story" in list_res.text
        assert "Homelab Edge Routing" in list_res.text
        assert "[Home/Lobby]" in list_res.text

        # Search matches
        search_res = await tool_session_search(session_ctx, "fungal")
        assert "Fungal Planet Short Story" in search_res.text
        assert search_res.data is not None and len(search_res.data["matches"]) == 1

        # Search no match
        search_empty = await tool_session_search(session_ctx, "nonexistent topic")
        assert "No sessions found" in search_empty.text

    @pytest.mark.asyncio
    async def test_session_switch_and_event(self, session_ctx):
        index = ConversationIndex(session_ctx.config)
        target = index.create("lmorchard", title="Target Session")

        events = []

        async def _subscriber(event):
            events.append(event)

        session_ctx.event_bus.subscribe(_subscriber)

        # Switch to valid session
        res = await tool_session_switch(session_ctx, target.conv_id, reason="Working on target")
        assert "Switching client to session 'Target Session'" in res.text
        assert len(events) == 1
        assert events[0]["type"] == "switch_conversation"
        assert events[0]["target_conv_id"] == target.conv_id
        assert events[0]["title"] == "Target Session"
        assert events[0]["reason"] == "Working on target"

    @pytest.mark.asyncio
    async def test_session_switch_home_alias(self, session_ctx):
        events = []

        async def _subscriber(event):
            events.append(event)

        session_ctx.event_bus.subscribe(_subscriber)

        res = await tool_session_switch(session_ctx, "home")
        assert "web-lmorchard-home" in res.text
        assert len(events) == 1
        assert events[0]["target_conv_id"] == "web-lmorchard-home"

    @pytest.mark.asyncio
    async def test_session_home_tool(self, session_ctx):
        events = []

        async def _subscriber(event):
            events.append(event)

        session_ctx.event_bus.subscribe(_subscriber)

        res = await tool_session_home(session_ctx)
        assert "web-lmorchard-home" in res.text
        assert len(events) == 1
        assert events[0]["target_conv_id"] == "web-lmorchard-home"

    @pytest.mark.asyncio
    async def test_session_create_and_switch(self, session_ctx):
        events = []

        async def _subscriber(event):
            events.append(event)

        session_ctx.event_bus.subscribe(_subscriber)

        res = await tool_session_create(session_ctx, title="Kitchen Blinds Debug")
        assert "Created and switched client to new session 'Kitchen Blinds Debug'" in res.text
        assert len(events) == 1
        assert events[0]["type"] == "switch_conversation"
        assert events[0]["title"] == "Kitchen Blinds Debug"
        assert events[0]["target_conv_id"].startswith("web-lmorchard-")

    @pytest.mark.asyncio
    async def test_session_switch_calls_manager_emit(self, session_ctx):
        from unittest.mock import AsyncMock

        mock_manager = AsyncMock()
        session_ctx.manager = mock_manager
        index = ConversationIndex(session_ctx.config)
        target = index.create("lmorchard", title="Target Session")

        res = await tool_session_switch(session_ctx, target.conv_id, reason="Testing emit")
        assert "Switching client to session 'Target Session'" in res.text

        mock_manager.emit.assert_awaited_once()
        args = mock_manager.emit.await_args[0]
        assert args[0] == session_ctx.conv_id
        assert args[1]["type"] == "switch_conversation"
        assert args[1]["target_conv_id"] == target.conv_id
        assert args[1]["title"] == "Target Session"
        assert args[1]["reason"] == "Testing emit"
