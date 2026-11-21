"""Unit tests for session search, listing, switching, and home tools (#1609, #1630)."""

import json

import pytest

from decafclaw.archive import append_message
from decafclaw.events import EventBus
from decafclaw.tools.session_tools import (
    tool_session_create,
    tool_session_home,
    tool_session_list,
    tool_session_search,
    tool_session_switch,
)
from decafclaw.web.conversations import ConversationIndex


def _append(config, conv_id, role, content):
    append_message(config, conv_id, {"role": role, "content": content})


def _set_updated_at(config, conv_id, timestamp):
    """Rewrite one index entry's updated_at so recency is deterministic."""
    path = config.agent_path / "web_conversations.json"
    data = json.loads(path.read_text())
    for entry in data:
        if entry["conv_id"] == conv_id:
            entry["updated_at"] = timestamp
    path.write_text(json.dumps(data, indent=2) + "\n")


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
    async def test_session_search_archived_filtering(self, session_ctx):
        index = ConversationIndex(session_ctx.config)
        active = index.create("lmorchard", title="Active Vocal Transceiver")
        archived = index.create("lmorchard", title="Archived Vocal Prototype")
        index.archive(archived.conv_id)

        # By default, excludes archived
        default_res = await tool_session_search(session_ctx, "vocal")
        assert "Active Vocal Transceiver" in default_res.text
        assert "Archived Vocal Prototype" not in default_res.text
        assert default_res.data is not None and len(default_res.data["matches"]) == 1
        assert default_res.data["matches"][0]["conv_id"] == active.conv_id
        assert default_res.data["matches"][0]["archived"] is False

        # With include_archived=True, includes both and tags archived
        all_res = await tool_session_search(session_ctx, "vocal", include_archived=True)
        assert "Active Vocal Transceiver" in all_res.text
        assert "Archived Vocal Prototype" in all_res.text
        assert "[Archived]" in all_res.text
        assert all_res.data is not None and len(all_res.data["matches"]) == 2

    @pytest.mark.asyncio
    async def test_session_search_matches_user_message_content(self, session_ctx):
        index = ConversationIndex(session_ctx.config)
        target = index.create("lmorchard", title="Homelab Edge Routing")
        decoy = index.create("lmorchard", title="Homelab VLAN Notes")
        _append(
            session_ctx.config,
            target.conv_id,
            "user",
            "The weather widget fails on CORS headers; add an allowlist for the origin.",
        )
        _append(session_ctx.config, decoy.conv_id, "user", "VLAN trunks on the switch need tagging.")

        res = await tool_session_search(session_ctx, "CORS headers")

        assert res.data is not None
        assert [m["conv_id"] for m in res.data["matches"]] == [target.conv_id]
        match = res.data["matches"][0]
        assert match["matched_role"] == "user"
        assert "CORS headers" in match["snippet"]
        assert 'matched user message: "The weather widget fails on CORS headers' in res.text

    @pytest.mark.asyncio
    async def test_session_search_prefers_user_over_assistant_content(self, session_ctx):
        index = ConversationIndex(session_ctx.config)
        user_session = index.create("lmorchard", title="Pricing Check")
        assistant_session = index.create("lmorchard", title="Provider Comparison")
        same_time = "2026-11-20T12:00:00+00:00"
        _set_updated_at(session_ctx.config, user_session.conv_id, same_time)
        _set_updated_at(session_ctx.config, assistant_session.conv_id, same_time)
        _append(session_ctx.config, user_session.conv_id, "user", "Check DeepSeek pricing per million tokens.")
        _append(
            session_ctx.config,
            assistant_session.conv_id,
            "assistant",
            "Check DeepSeek pricing per million tokens.",
        )

        res = await tool_session_search(session_ctx, "DeepSeek pricing")

        assert res.data is not None
        assert [m["conv_id"] for m in res.data["matches"]] == [
            user_session.conv_id,
            assistant_session.conv_id,
        ]
        assert res.data["matches"][0]["matched_role"] == "user"
        assert res.data["matches"][1]["matched_role"] == "assistant"
        assert "matched assistant message" in res.text

    @pytest.mark.asyncio
    async def test_session_search_recency_breaks_content_ties(self, session_ctx):
        index = ConversationIndex(session_ctx.config)
        older = index.create("lmorchard", title="Old Debate")
        newer = index.create("lmorchard", title="New Debate")
        _set_updated_at(session_ctx.config, older.conv_id, "2026-09-01T12:00:00+00:00")
        _set_updated_at(session_ctx.config, newer.conv_id, "2026-11-20T12:00:00+00:00")
        for conv in (older, newer):
            _append(session_ctx.config, conv.conv_id, "user", "Which migrates faster, SQLite WAL or Postgres?")

        res = await tool_session_search(session_ctx, "SQLite WAL")

        assert res.data is not None
        assert [m["conv_id"] for m in res.data["matches"]] == [newer.conv_id, older.conv_id]

    @pytest.mark.asyncio
    async def test_session_search_recency_does_not_outrank_stronger_match(self, session_ctx):
        index = ConversationIndex(session_ctx.config)
        stale_title = index.create("lmorchard", title="DeepSeek Pricing")
        fresh_content = index.create("lmorchard", title="Misc Notes")
        _set_updated_at(session_ctx.config, stale_title.conv_id, "2026-09-01T12:00:00+00:00")
        _set_updated_at(session_ctx.config, fresh_content.conv_id, "2026-11-20T12:00:00+00:00")
        _append(session_ctx.config, fresh_content.conv_id, "user", "DeepSeek pricing per million tokens.")

        res = await tool_session_search(session_ctx, "DeepSeek Pricing")

        assert res.data is not None
        assert [m["conv_id"] for m in res.data["matches"]] == [
            stale_title.conv_id,
            fresh_content.conv_id,
        ]

    @pytest.mark.asyncio
    async def test_session_search_ignores_tool_and_metadata_content(self, session_ctx):
        index = ConversationIndex(session_ctx.config)
        conv = index.create("lmorchard", title="Build Log")
        _append(session_ctx.config, conv.conv_id, "tool", "kubectl apply ingress traefik")
        _append(session_ctx.config, conv.conv_id, "memory_context", "traefik ingress notes")

        res = await tool_session_search(session_ctx, "traefik ingress")

        assert res.data is not None and res.data["matches"] == []

    @pytest.mark.asyncio
    async def test_session_search_streams_past_corrupt_archive_line(self, session_ctx):
        index = ConversationIndex(session_ctx.config)
        conv = index.create("lmorchard", title="Recovered Thread")
        _append(session_ctx.config, conv.conv_id, "user", "Recover the CORS debug notes.")
        from decafclaw.archive import archive_path

        with archive_path(session_ctx.config, conv.conv_id).open("a", encoding="utf-8") as f:
            f.write("{not valid json\n")

        res = await tool_session_search(session_ctx, "CORS debug")

        assert res.data is not None
        assert [m["conv_id"] for m in res.data["matches"]] == [conv.conv_id]

    @pytest.mark.asyncio
    async def test_session_search_title_only_match_has_no_snippet(self, session_ctx):
        index = ConversationIndex(session_ctx.config)
        index.create("lmorchard", title="Fungal Planet Short Story")

        res = await tool_session_search(session_ctx, "fungal")

        assert res.data is not None
        assert res.data["matches"][0]["matched_role"] is None
        assert res.data["matches"][0]["snippet"] is None

    @pytest.mark.asyncio
    async def test_session_search_does_not_scan_other_users_sessions(self, session_ctx):
        index = ConversationIndex(session_ctx.config)
        other = index.create("someone-else", title="Private Planned Trip")
        _append(session_ctx.config, other.conv_id, "user", "Secret shoulder season pricing notes.")

        res = await tool_session_search(session_ctx, "shoulder season pricing")

        assert res.data is not None and res.data["matches"] == []

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

    @pytest.mark.asyncio
    async def test_session_switch_no_duplicate_delivery(self, session_ctx):
        from unittest.mock import AsyncMock

        mock_manager = AsyncMock()
        session_ctx.manager = mock_manager
        index = ConversationIndex(session_ctx.config)
        target = index.create("lmorchard", title="Single Delivery")

        bus_events = []

        async def _subscriber(event):
            bus_events.append(event)

        session_ctx.event_bus.subscribe(_subscriber)

        await tool_session_switch(session_ctx, target.conv_id, reason="No dupes")

        # Manager emit was called exactly once
        assert mock_manager.emit.await_count == 1
        # Event bus was bypassed because manager.emit handled delivery
        assert len(bus_events) == 0
