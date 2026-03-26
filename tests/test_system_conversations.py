"""Tests for system conversation discovery and WebSocket access."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.archive import append_message
from decafclaw.events import EventBus
from decafclaw.web.conversations import (
    ConversationIndex,
    _classify_conv_id,
    list_system_conversations,
)
from decafclaw.web.websocket import (
    _handle_load_history,
    _handle_select_conv,
)


class TestClassifyConvId:
    def test_schedule(self):
        conv_type, title = _classify_conv_id("schedule-dream-20260324-125204")
        assert conv_type == "schedule"
        assert "dream" in title
        assert "2026-03-24 12:52" in title

    def test_heartbeat(self):
        conv_type, title = _classify_conv_id("heartbeat-20260324-125204-0")
        assert conv_type == "heartbeat"
        assert "2026-03-24 12:52" in title
        assert "#0" in title

    def test_delegated(self):
        conv_type, title = _classify_conv_id("web-lmorchard-25d5a8e2--child-e5b1aeb9")
        assert conv_type == "delegated"
        assert "e5b1aeb9" in title

    def test_web_conv(self):
        conv_type, _ = _classify_conv_id("web-lmorchard-25d5a8e2")
        assert conv_type == "unknown"

    def test_unknown(self):
        conv_type, title = _classify_conv_id("some-random-id")
        assert conv_type == "unknown"
        assert title == "some-random-id"


class TestListSystemConversations:
    def test_discovers_schedule_archives(self, config):
        config.workspace_path.mkdir(parents=True, exist_ok=True)
        conv_dir = config.workspace_path / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        # Create a schedule archive
        (conv_dir / "schedule-dream-20260324-125204.jsonl").write_text(
            json.dumps({"role": "user", "content": "test"}) + "\n"
        )
        # Create a web archive (should be excluded)
        (conv_dir / "web-user-abc123.jsonl").write_text(
            json.dumps({"role": "user", "content": "test"}) + "\n"
        )

        results = list_system_conversations(config)
        conv_ids = [r["conv_id"] for r in results]
        assert "schedule-dream-20260324-125204" in conv_ids
        assert "web-user-abc123" not in conv_ids

    def test_excludes_compacted_sidecars(self, config):
        conv_dir = config.workspace_path / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        (conv_dir / "schedule-foo-20260324-120000.jsonl").write_text("{}\n")
        (conv_dir / "schedule-foo-20260324-120000.compacted.jsonl").write_text("{}\n")

        results = list_system_conversations(config)
        assert len(results) == 1

    def test_includes_delegated_children(self, config):
        conv_dir = config.workspace_path / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        (conv_dir / "web-user-abc123--child-def456.jsonl").write_text("{}\n")

        results = list_system_conversations(config)
        assert len(results) == 1
        assert results[0]["conv_type"] == "delegated"

    def test_sorted_newest_first(self, config):
        import os
        conv_dir = config.workspace_path / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        old = conv_dir / "schedule-old-20260101-120000.jsonl"
        new = conv_dir / "schedule-new-20260324-120000.jsonl"
        old.write_text("{}\n")
        new.write_text("{}\n")
        # Force deterministic mtimes
        os.utime(old, (1000000, 1000000))
        os.utime(new, (2000000, 2000000))

        results = list_system_conversations(config)
        assert results[0]["conv_id"] == "schedule-new-20260324-120000"

    def test_empty_dir(self, config):
        config.workspace_path.mkdir(parents=True, exist_ok=True)
        results = list_system_conversations(config)
        assert results == []


@pytest.fixture
def ws_state(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return {
        "config": config,
        "event_bus": EventBus(),
        "app_ctx": MagicMock(config=config, event_bus=EventBus()),
        "websocket": MagicMock(),
    }


@pytest.fixture
def index(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return ConversationIndex(config)


class TestDelegatedFiltering:
    def test_filters_delegated_by_username(self, config):
        conv_dir = config.workspace_path / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        (conv_dir / "web-alice-abc123--child-def456.jsonl").write_text("{}\n")
        (conv_dir / "web-bob-abc123--child-ghi789.jsonl").write_text("{}\n")

        results = list_system_conversations(config, username="alice")
        conv_ids = [r["conv_id"] for r in results]
        assert "web-alice-abc123--child-def456" in conv_ids
        assert "web-bob-abc123--child-ghi789" not in conv_ids

    def test_no_filter_without_username(self, config):
        conv_dir = config.workspace_path / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        (conv_dir / "web-alice-abc123--child-def456.jsonl").write_text("{}\n")
        (conv_dir / "web-bob-abc123--child-ghi789.jsonl").write_text("{}\n")

        results = list_system_conversations(config, username="")
        assert len(results) == 2


class TestSystemConvAccess:
    @pytest.mark.asyncio
    async def test_select_system_conv(self, ws_state, index):
        """Selecting a system conversation should succeed with read_only flag."""
        config = ws_state["config"]
        conv_dir = config.workspace_path / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        (conv_dir / "schedule-test-20260324-120000.jsonl").write_text(
            json.dumps({"role": "user", "content": "hello"}) + "\n"
        )

        ws_send = AsyncMock()
        await _handle_select_conv(ws_send, index, "testuser",
                                  {"conv_id": "schedule-test-20260324-120000"},
                                  ws_state)

        msg = ws_send.call_args[0][0]
        assert msg["type"] == "conv_selected"
        assert msg["read_only"] is True

    @pytest.mark.asyncio
    async def test_load_system_conv_history(self, ws_state, index):
        """Loading history for a system conversation should work (read-only)."""
        config = ws_state["config"]
        append_message(config, "schedule-test-20260324-120000",
                       {"role": "user", "content": "scheduled task output"})

        ws_send = AsyncMock()
        await _handle_load_history(ws_send, index, "testuser",
                                   {"conv_id": "schedule-test-20260324-120000"},
                                   ws_state)

        msg = ws_send.call_args[0][0]
        assert msg["type"] == "conv_history"
        assert msg["read_only"] is True
        assert len(msg["messages"]) == 1

    @pytest.mark.asyncio
    async def test_select_nonexistent_conv_fails(self, ws_state, index):
        """Selecting a conversation that doesn't exist should error."""
        ws_send = AsyncMock()
        await _handle_select_conv(ws_send, index, "testuser",
                                  {"conv_id": "does-not-exist"}, ws_state)

        msg = ws_send.call_args[0][0]
        assert msg["type"] == "error"

    @pytest.mark.asyncio
    async def test_rejects_path_traversal_select(self, ws_state, index):
        """conv_id with path traversal should be rejected."""
        ws_send = AsyncMock()
        await _handle_select_conv(ws_send, index, "testuser",
                                  {"conv_id": "../../../etc/passwd"}, ws_state)
        msg = ws_send.call_args[0][0]
        assert msg["type"] == "error"

    @pytest.mark.asyncio
    async def test_rejects_path_traversal_history(self, ws_state, index):
        """conv_id with path traversal should be rejected in load_history."""
        ws_send = AsyncMock()
        await _handle_load_history(ws_send, index, "testuser",
                                   {"conv_id": "../../secrets"}, ws_state)
        msg = ws_send.call_args[0][0]
        assert msg["type"] == "error"

    @pytest.mark.asyncio
    async def test_rejects_other_users_web_conv(self, ws_state, index):
        """Cannot select another user's web conversation as read-only."""
        config = ws_state["config"]
        # Create a conv belonging to another user
        other_conv = index.create("otheruser", title="Secret")
        # Create the archive file
        from decafclaw.archive import append_message
        append_message(config, other_conv.conv_id,
                       {"role": "user", "content": "secret"})

        ws_send = AsyncMock()
        await _handle_select_conv(ws_send, index, "testuser",
                                  {"conv_id": other_conv.conv_id}, ws_state)
        msg = ws_send.call_args[0][0]
        assert msg["type"] == "error"
