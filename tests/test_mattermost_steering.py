"""Mattermost send path forwards the conversation's busy state as `steering` (#1442).

The manager owns the final decision: a stale busy read just starts a normal
turn, because `enqueue_turn` only sets `steer_event` while still busy.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.events import EventBus
from decafclaw.mattermost import MattermostClient


def _make_client():
    """Bare client with the transport calls `_process_conversation` makes mocked."""
    client = MattermostClient.__new__(MattermostClient)
    client._pending_confirm_posts = {}
    client.cooldown_ms = 0
    client.send_placeholder = AsyncMock(return_value=None)
    client.send_typing = AsyncMock()
    client._download_attachments = AsyncMock(return_value=None)
    client._ensure_subscribed = MagicMock()
    return client


def _msgs():
    return [
        {
            "text": "hello",
            "post_id": "post1",
            "channel_id": "ch1",
            "channel_type": "D",
            "root_id": "",
            "sender_name": "user",
            "mentioned": False,
        }
    ]


async def _run(client, config, manager):
    app_ctx = SimpleNamespace(config=config, event_bus=EventBus())
    await client._process_conversation(
        "conv1",
        "ch1",
        _msgs(),
        app_ctx,
        {},
        manager,
    )


@pytest.mark.asyncio
async def test_busy_conversation_passes_steering_true(config):
    client = _make_client()
    manager = MagicMock()
    manager.get_state.return_value = SimpleNamespace(busy=True)
    manager.send_message = AsyncMock()

    await _run(client, config, manager)

    manager.send_message.assert_awaited_once()
    assert manager.send_message.await_args.kwargs["steering"] is True


@pytest.mark.asyncio
async def test_idle_conversation_passes_steering_false(config):
    client = _make_client()
    manager = MagicMock()
    manager.get_state.return_value = SimpleNamespace(busy=False)
    manager.send_message = AsyncMock()

    await _run(client, config, manager)

    manager.send_message.assert_awaited_once()
    assert manager.send_message.await_args.kwargs["steering"] is False


# -- Denial feedback via a reply to the confirmation card (#1559) --------------


def _pending_shell(confirmation_id="conf1"):
    return SimpleNamespace(
        confirmation_id=confirmation_id,
        action_type=SimpleNamespace(value="run_shell_command"),
    )


@pytest.mark.asyncio
async def test_reply_to_confirmation_card_denies_with_feedback(config):
    """A direct reply to the card is a denial carrying the reply text."""
    client = _make_client()
    client.send = AsyncMock(return_value="ack")
    client._pending_confirm_posts["conv1"] = "card1"

    manager = MagicMock()
    manager.get_state.return_value = SimpleNamespace(busy=False, pending_confirmation=_pending_shell())
    manager.respond_to_confirmation = AsyncMock()
    manager.send_message = AsyncMock()

    msgs = _msgs()
    msgs[0].update({"text": "use workspace_search instead", "root_id": "card1"})
    app_ctx = SimpleNamespace(config=config, event_bus=EventBus())

    await client._process_conversation("conv1", "ch1", msgs, app_ctx, {}, manager)

    manager.respond_to_confirmation.assert_awaited_once_with(
        "conv1", "conf1", approved=False, feedback="use workspace_search instead"
    )
    manager.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_reply_elsewhere_keeps_normal_routing(config):
    """A reply to a different post is not treated as confirmation feedback."""
    client = _make_client()
    client._pending_confirm_posts["conv1"] = "card1"

    manager = MagicMock()
    manager.get_state.return_value = SimpleNamespace(busy=False, pending_confirmation=_pending_shell())
    manager.respond_to_confirmation = AsyncMock()
    manager.send_message = AsyncMock()

    # A plain top-level message (no root) while a confirmation pends is not
    # feedback — only a reply thread rooted at the card is.
    msgs = _msgs()
    app_ctx = SimpleNamespace(config=config, event_bus=EventBus())

    await client._process_conversation("conv1", "ch1", msgs, app_ctx, {}, manager)

    manager.respond_to_confirmation.assert_not_called()
    manager.send_message.assert_awaited_once()
