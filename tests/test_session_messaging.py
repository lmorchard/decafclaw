"""Tests for session-scoped asynchronous inter-conversation messaging (#1114)."""

import asyncio

import pytest

from decafclaw.conversation_manager import ConversationManager, TurnKind
from decafclaw.events import EventBus
from decafclaw.media import ToolResult
from decafclaw.session_messaging import deliver_session_message, is_child_conv_id, parent_conv_id_of
from decafclaw.tools.session_tools import tool_session_send


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
    async def test_deliver_enqueues_wake_turn_in_target(self, ctx, monkeypatch):
        """deliver_session_message enqueues a WAKE turn into the target conv."""
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
        assert captured["kind"] is TurnKind.WAKE
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


class TestDeliverSessionMessageBusyPath:
    """Real-manager tests for the never-interrupts delivery guarantee.

    The busy path is load-bearing (#1114 success condition 3) and the
    unit tests above stub `enqueue_turn` entirely, so these run the real
    ConversationManager queue — with `turn_on_new_message="cancel"`,
    the adversarial setting under which a USER-kind delivery would
    cancel the in-flight turn.
    """

    @pytest.fixture
    def wake_config(self, config):
        config.agent.turn_on_new_message = "cancel"
        config.llm.streaming = False
        return config

    @pytest.mark.asyncio
    async def test_delivered_wake_queues_and_never_cancels_in_flight_turn(self, wake_config, monkeypatch):
        manager = ConversationManager(wake_config, EventBus())
        conv_id = "sess-conv"
        source_conv_id = f"{conv_id}--child-abc123"

        first_started = asyncio.Event()
        release_first = asyncio.Event()
        order = []

        async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
            if user_message == "first turn":
                order.append("first-start")
                first_started.set()
                await release_first.wait()
                order.append("first-end")
                return ToolResult(text="first done")
            order.append(f"wake:{user_message}")
            return ToolResult(text="wake done")

        monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

        first_future = await manager.enqueue_turn(conv_id, kind=TurnKind.USER, prompt="first turn", user_id="testuser")
        await asyncio.wait_for(first_started.wait(), 5.0)

        # Deliver while the target is mid-turn: must queue, not interrupt.
        fut = await deliver_session_message(manager, conv_id, source_conv_id, "peer status update", user_id="testuser")
        assert getattr(fut, "queued", False) is True
        assert not first_future.done()

        release_first.set()
        first_result = await asyncio.wait_for(first_future, 5.0)
        wake_result = await asyncio.wait_for(fut, 5.0)

        # The in-flight turn ran to completion (not cancelled), and the
        # delivered wake turn handled the message afterward.
        assert first_result is not None and first_result.text == "first done"
        assert wake_result is not None and wake_result.text == "wake done"
        assert order == ["first-start", "first-end", "wake:peer status update"]

    @pytest.mark.asyncio
    async def test_delivered_wake_runs_immediately_when_idle(self, wake_config, monkeypatch):
        """With no in-flight turn, the delivered wake starts a turn at once."""
        manager = ConversationManager(wake_config, EventBus())
        conv_id = "idle-conv"

        async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
            return ToolResult(text="handled")

        monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

        fut = await deliver_session_message(manager, conv_id, "other-conv", "ping", user_id="testuser")
        assert getattr(fut, "queued", True) is False
        result = await asyncio.wait_for(fut, 5.0)
        assert result is not None and result.text == "handled"


class TestSessionSendTool:
    """The agent-facing producer: session-scoped validation + delivery."""

    @pytest.fixture
    def deliver_mock(self, monkeypatch):
        """Stub the delivery primitive; returns the captured-kwargs holder."""
        captured = {}

        async def fake_deliver(manager, target, source, text, *, user_id="", context_setup=None):
            captured["manager"] = manager
            captured["target"] = target
            captured["source"] = source
            captured["text"] = text
            captured["user_id"] = user_id
            fut = asyncio.get_running_loop().create_future()
            fut.set_result(None)
            fut.queued = False
            fut.rate_limited = False
            return fut

        monkeypatch.setattr("decafclaw.tools.session_tools.deliver_session_message", fake_deliver)
        return captured

    @pytest.mark.asyncio
    async def test_child_sends_to_session_root(self, ctx, deliver_mock):
        ctx.conv_id = "test-conv--child-abc123"
        result = await tool_session_send(ctx, "test-conv", "progress: half done")

        assert "queued for delivery to test-conv" in result.text
        assert deliver_mock["target"] == "test-conv"
        assert deliver_mock["source"] == "test-conv--child-abc123"
        assert deliver_mock["manager"] is ctx.manager
        assert deliver_mock["user_id"] == "testuser"
        # Bare-prompt attribution so the handling agent can route a reply.
        assert "test-conv--child-abc123" in deliver_mock["text"]
        assert "progress: half done" in deliver_mock["text"]

    @pytest.mark.asyncio
    async def test_parent_sends_to_own_child(self, ctx, deliver_mock):
        result = await tool_session_send(ctx, "test-conv--child-def456", "push update")

        assert "queued for delivery to test-conv--child-def456" in result.text
        assert deliver_mock["target"] == "test-conv--child-def456"
        assert deliver_mock["source"] == "test-conv"

    @pytest.mark.asyncio
    async def test_cross_session_send_refused(self, ctx, deliver_mock):
        ctx.conv_id = "unrelated-root"
        result = await tool_session_send(ctx, "test-conv", "hello?")

        assert result.text.startswith("[error:")
        assert "outside this session" in result.text
        assert deliver_mock == {}

    @pytest.mark.asyncio
    async def test_self_send_refused(self, ctx, deliver_mock):
        result = await tool_session_send(ctx, "test-conv", "note to self")

        assert result.text.startswith("[error:")
        assert deliver_mock == {}

    @pytest.mark.asyncio
    async def test_no_manager_returns_error(self, config, deliver_mock):
        from decafclaw.context import Context

        bare = Context(config=config, event_bus=EventBus())
        bare.conv_id = "test-conv"
        bare.user_id = "testuser"
        bare.manager = None
        result = await tool_session_send(bare, "test-conv--child-abc", "hi")

        assert result.text.startswith("[error:")
        assert "no manager" in result.text
        assert deliver_mock == {}

    @pytest.mark.asyncio
    async def test_empty_text_refused(self, ctx, deliver_mock):
        result = await tool_session_send(ctx, "test-conv", "   ")

        assert result.text.startswith("[error:")
        assert deliver_mock == {}

    @pytest.mark.asyncio
    async def test_rate_limited_delivery_reports_drop(self, ctx, monkeypatch):
        async def fake_deliver(manager, target, source, text, *, user_id="", context_setup=None):
            fut = asyncio.get_running_loop().create_future()
            fut.set_result(None)
            fut.queued = False
            fut.rate_limited = True
            return fut

        monkeypatch.setattr("decafclaw.tools.session_tools.deliver_session_message", fake_deliver)
        result = await tool_session_send(ctx, "test-conv--child-abc", "hello")

        assert result.text.startswith("[error:")
        assert "rate limiter" in result.text

    @pytest.mark.asyncio
    async def test_delivery_error_returns_error_result(self, ctx, monkeypatch):
        async def fake_deliver(*args, **kwargs):
            raise RuntimeError("bus overloaded")

        monkeypatch.setattr("decafclaw.tools.session_tools.deliver_session_message", fake_deliver)
        result = await tool_session_send(ctx, "test-conv--child-abc", "hello")

        assert result.text.startswith("[error:")
        assert "bus overloaded" in result.text


class TestSessionSendRegistration:
    def test_tool_registered(self):
        from decafclaw.tools import TOOL_DEFINITIONS, TOOLS

        assert "session_send" in TOOLS
        defs = {d["function"]["name"]: d for d in TOOL_DEFINITIONS}
        assert "session_send" in defs
        assert defs["session_send"].get("priority") == "normal"
