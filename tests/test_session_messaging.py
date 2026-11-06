"""Tests for session-scoped asynchronous inter-conversation messaging (#1114)."""

import asyncio
from types import SimpleNamespace

import pytest

from decafclaw.conversation_manager import ConversationManager, TurnKind
from decafclaw.events import EventBus
from decafclaw.session_messaging import deliver_session_message, is_child_conv_id, parent_conv_id_of


@pytest.fixture
def ctx(config):
    """ctx fixture with a ConversationManager attached (required for delivery)."""
    from decafclaw.context import Context

    bus = EventBus()
    context = Context(config=config, event_bus=bus)
    context.conv_id = "test-conv"
    context.channel_id = "test-channel"
    context.user_id = "testuser"
    context.manager = ConversationManager(config, bus)
    return context


class TestChildConvIdConventions:
    def test_child_conv_id_detected(self):
        assert is_child_conv_id("parent-abc--child-xyz", "parent-abc") is True
        assert is_child_conv_id("parent-abc--child-xyz", "parent-other") is False

    def test_standalone_conv_id_not_child(self):
        assert is_child_conv_id("standalone", "standalone") is False
        assert is_child_conv_id("standalone", "") is False

    def test_parent_conv_id_of_child(self):
        assert parent_conv_id_of("parent-abc--child-xyz") == "parent-abc"

    def test_parent_conv_id_of_standalone_is_none(self):
        assert parent_conv_id_of("standalone") is None
        assert parent_conv_id_of("") is None


class TestDeliverSessionMessage:
    @pytest.mark.asyncio
    async def test_deliver_enqueues_user_turn_in_target(self, ctx, monkeypatch):
        """deliver_session_message enqueues a USER turn into the target conv."""
        captured = {}

        async def fake_enqueue(
            conv_id,
            *,
            kind,
            prompt,
            history=None,
            context_setup=None,
            user_id="",
            archive_text="",
            attachments=None,
            command_ctx=None,
            wiki_page=None,
            task_mode=None,
            metadata=None,
        ):
            captured["conv_id"] = conv_id
            captured["kind"] = kind
            captured["prompt"] = prompt
            captured["history"] = history
            captured["context_setup"] = context_setup
            captured["user_id"] = user_id
            captured["metadata"] = metadata
            fut = asyncio.get_running_loop().create_future()
            fut.set_result(None)
            return fut

        monkeypatch.setattr(ctx.manager, "enqueue_turn", fake_enqueue)
        fut = await deliver_session_message(
            ctx.manager,
            "test-conv--child-abc123",
            "test-conv",
            "status update please",
            user_id="testuser",
        )

        assert fut is not None
        assert captured["conv_id"] == "test-conv--child-abc123"
        assert captured["kind"] is TurnKind.USER
        assert captured["prompt"] == "status update please"
        assert captured["history"] is None or captured["history"] == []
        assert captured["context_setup"] is None
        assert captured["user_id"] == "testuser"
        assert (captured["metadata"] or {}).get("source_conv_id") == "test-conv"
        assert (captured["metadata"] or {}).get("session_message") is True

    @pytest.mark.asyncio
    async def test_deliver_propagates_manager_errors(self, ctx, monkeypatch):
        """Delivery errors surface (fail-open policy is at the caller)."""

        async def fake_enqueue(*args, **kwargs):
            raise RuntimeError("bus overloaded")

        monkeypatch.setattr(ctx.manager, "enqueue_turn", fake_enqueue)
        with pytest.raises(RuntimeError):
            await deliver_session_message(ctx.manager, "test-conv", "test-conv", "hello")
