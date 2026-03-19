"""Tests for MattermostClient._handle_posted message filtering."""

import json

from decafclaw.config import Config
from decafclaw.config_types import MattermostConfig
from decafclaw.mattermost import MattermostClient


def _make_config(**overrides):
    """Create a config with Mattermost settings."""
    defaults = dict(
        url="https://mm.example.com",
        token="test-token",
        bot_username="testbot",
        ignore_bots=True,
        ignore_webhooks=False,
        require_mention=True,
        channel_blocklist=[],
    )
    defaults.update(overrides)
    return Config(mattermost=MattermostConfig(**defaults))


def _make_client(config=None):
    client = MattermostClient(config or _make_config())
    client.bot_user_id = "bot-user-id"
    return client


def _make_event(message="hello", user_id="user123", channel_type="D",
                root_id="", post_type="", from_bot=None, from_webhook=None,
                channel_id="chan1", post_id="post1", sender_name="testuser"):
    """Build a Mattermost websocket posted event."""
    props = {}
    if from_bot is not None:
        props["from_bot"] = from_bot
    if from_webhook is not None:
        props["from_webhook"] = from_webhook
    post = {
        "id": post_id,
        "message": message,
        "user_id": user_id,
        "channel_id": channel_id,
        "root_id": root_id,
        "type": post_type,
        "props": props,
    }
    return {
        "event": "posted",
        "data": {
            "post": json.dumps(post),
            "sender_name": sender_name,
            "channel_type": channel_type,
        },
    }


def test_normal_dm_message_dispatched():
    client = _make_client()
    received = []
    evt = _make_event(message="hello", channel_type="D")
    client._handle_posted(evt, received.append)
    assert len(received) == 1
    assert received[0]["text"] == "hello"


def test_ignores_own_messages():
    client = _make_client()
    received = []
    evt = _make_event(user_id="bot-user-id")
    client._handle_posted(evt, received.append)
    assert len(received) == 0


def test_ignores_system_messages():
    client = _make_client()
    received = []
    evt = _make_event(post_type="system_join_channel")
    client._handle_posted(evt, received.append)
    assert len(received) == 0


def test_ignores_empty_messages():
    client = _make_client()
    received = []
    evt = _make_event(message="   ")
    client._handle_posted(evt, received.append)
    assert len(received) == 0


def test_ignores_bot_messages_when_configured():
    client = _make_client(_make_config(ignore_bots=True))
    received = []
    evt = _make_event(from_bot="true")
    client._handle_posted(evt, received.append)
    assert len(received) == 0


def test_allows_bot_messages_when_not_configured():
    client = _make_client(_make_config(ignore_bots=False))
    received = []
    evt = _make_event(from_bot="true", channel_type="D")
    client._handle_posted(evt, received.append)
    assert len(received) == 1


def test_ignores_webhook_messages_when_configured():
    client = _make_client(_make_config(ignore_webhooks=True))
    received = []
    evt = _make_event(from_webhook="true")
    client._handle_posted(evt, received.append)
    assert len(received) == 0


def test_allows_webhook_messages_when_not_configured():
    client = _make_client(_make_config(ignore_webhooks=False))
    received = []
    evt = _make_event(from_webhook="true", channel_type="D")
    client._handle_posted(evt, received.append)
    assert len(received) == 1


def test_channel_blocklist():
    config = _make_config(channel_blocklist=["blocked-chan", "other-blocked"])
    client = _make_client(config)
    received = []
    evt = _make_event(channel_id="blocked-chan")
    client._handle_posted(evt, received.append)
    assert len(received) == 0


def test_channel_not_in_blocklist():
    config = _make_config(channel_blocklist=["blocked-chan"])
    client = _make_client(config)
    received = []
    evt = _make_event(channel_id="allowed-chan", channel_type="D")
    client._handle_posted(evt, received.append)
    assert len(received) == 1


def test_require_mention_in_public_channel():
    """In public channels with require_mention, messages without @-mention are ignored."""
    config = _make_config(require_mention=True)
    client = _make_client(config)
    received = []
    evt = _make_event(message="hello", channel_type="O")
    client._handle_posted(evt, received.append)
    assert len(received) == 0


def test_mention_in_public_channel():
    """In public channels, messages with @-mention are dispatched."""
    config = _make_config(require_mention=True)
    client = _make_client(config)
    received = []
    evt = _make_event(message="@testbot hello", channel_type="O")
    client._handle_posted(evt, received.append)
    assert len(received) == 1
    assert received[0]["text"] == "hello"
    assert received[0]["mentioned"] is True


def test_dm_does_not_require_mention():
    """DM messages don't require @-mention even if require_mention is True."""
    config = _make_config(require_mention=True)
    client = _make_client(config)
    received = []
    evt = _make_event(message="hello", channel_type="D")
    client._handle_posted(evt, received.append)
    assert len(received) == 1


def test_thread_reply_does_not_require_mention():
    """Thread replies don't require @-mention."""
    config = _make_config(require_mention=True)
    client = _make_client(config)
    received = []
    evt = _make_event(message="hello", channel_type="O", root_id="parent-post-id")
    client._handle_posted(evt, received.append)
    assert len(received) == 1


def test_mention_stripped_from_text():
    client = _make_client()
    received = []
    evt = _make_event(message="@testbot what time is it", channel_type="D")
    client._handle_posted(evt, received.append)
    assert received[0]["text"] == "what time is it"


def test_malformed_json_post_ignored():
    client = _make_client()
    received = []
    evt = {"event": "posted", "data": {"post": "not valid json{{{", "channel_type": "D"}}
    client._handle_posted(evt, received.append)
    assert len(received) == 0


def test_message_fields_populated():
    """Verify all expected fields are in the dispatched message."""
    client = _make_client()
    received = []
    evt = _make_event(
        message="test msg", user_id="u1", channel_id="c1",
        post_id="p1", root_id="r1", sender_name="alice",
        channel_type="O",
    )
    # In a thread (root_id present), doesn't need mention
    client._handle_posted(evt, received.append)
    msg = received[0]
    assert msg["text"] == "test msg"
    assert msg["channel_id"] == "c1"
    assert msg["post_id"] == "p1"
    assert msg["root_id"] == "r1"
    assert msg["user_id"] == "u1"
    assert msg["sender_name"] == "alice"
    assert msg["channel_type"] == "O"
    assert msg["mentioned"] is False
