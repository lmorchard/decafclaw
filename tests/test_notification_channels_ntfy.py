"""Tests for the ntfy notification channel adapter (#1494)."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw import notifications as notifs
from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.mattermost_ui import get_token_registry
from decafclaw.notification_channels import ntfy as ntfy_mod
from decafclaw.notification_channels.ntfy import (
    _headers,
    _publish_url,
    build_confirmation_payload,
    build_notification_payload,
    make_ntfy_adapter,
)

# -- Helpers ------------------------------------------------------------------


def _rec(**overrides) -> notifs.NotificationRecord:
    return notifs.NotificationRecord(
        id=overrides.get("id", "abc123"),
        timestamp=overrides.get("timestamp", "2026-11-18T12:00:00Z"),
        category=overrides.get("category", "heartbeat"),
        title=overrides.get("title", "Heartbeat completed"),
        priority=overrides.get("priority", "high"),
        body=overrides.get("body", "1 OK, 0 alerts"),
        link=overrides.get("link"),
        conv_id=overrides.get("conv_id"),
    )


def _enable_ntfy(config, *, topic="agent-alerts", min_priority="high", notify_on_confirmation=True):
    cfg = config.notifications.channels.ntfy
    cfg.enabled = True
    cfg.server_url = "https://ntfy.example"
    cfg.topic = topic
    cfg.min_priority = min_priority
    cfg.notify_on_confirmation = notify_on_confirmation
    return config


def _enable_http(config, *, base_url="https://agent.example"):
    config.http.enabled = True
    config.http.secret = "test-secret"
    config.http.host = "127.0.0.1"
    config.http.port = 18880
    config.http.base_url = base_url
    return config


def _confirm_event(**overrides) -> dict:
    return {
        "type": "confirmation_request",
        "conv_id": overrides.get("conv_id", "conv-1"),
        "confirmation_id": overrides.get("confirmation_id", "conf-1"),
        "action_type": overrides.get("action_type", "run_shell_command"),
        "action_data": overrides.get("action_data", {"command": "rm -rf build"}),
        "message": overrides.get("message", ""),
    }


def _token_from(url: str) -> str:
    return parse_qs(urlparse(url).query)["token"][0]


@pytest.fixture(autouse=True)
def _clean_token_registry():
    registry = get_token_registry()
    registry._tokens.clear()
    yield
    registry._tokens.clear()


# -- Endpoint + header building ----------------------------------------------


class TestPublishEndpoint:
    def test_root_url_used_for_json_publish(self, config):
        cfg = config.notifications.channels.ntfy
        cfg.server_url = "https://ntfy.example/"
        cfg.topic = "agent-alerts"
        assert _publish_url(cfg) == "https://ntfy.example/"

    def test_empty_topic_disables(self, config):
        cfg = config.notifications.channels.ntfy
        cfg.server_url = "https://ntfy.example"
        cfg.topic = ""
        assert _publish_url(cfg) == ""

    def test_non_http_server_url_disables(self, config):
        cfg = config.notifications.channels.ntfy
        cfg.server_url = "ntfy.example"
        cfg.topic = "agent-alerts"
        assert _publish_url(cfg) == ""


class TestHeaders:
    def test_bearer_header_only_when_token_set(self, config):
        cfg = config.notifications.channels.ntfy
        cfg.access_token = "tk_secret"
        assert _headers(cfg)["Authorization"] == "Bearer tk_secret"

    def test_no_authorization_without_token(self, config):
        cfg = config.notifications.channels.ntfy
        cfg.access_token = ""
        assert "Authorization" not in _headers(cfg)


# -- Notification payloads ---------------------------------------------------


class TestNotificationPayload:
    def test_core_fields_and_priority_mapping(self, config):
        _enable_ntfy(config)
        payload = build_notification_payload(config, _rec(priority="high"))
        assert payload["topic"] == "agent-alerts"
        assert payload["title"] == "Heartbeat completed"
        assert payload["message"] == "1 OK, 0 alerts"
        assert payload["priority"] == 4  # high
        assert payload["tags"] == ["heartbeat"]

    def test_low_priority_maps_to_2(self, config):
        _enable_ntfy(config)
        assert build_notification_payload(config, _rec(priority="low"))["priority"] == 2

    def test_empty_body_falls_back_to_title(self, config):
        _enable_ntfy(config)
        payload = build_notification_payload(config, _rec(title="Only a title", body=""))
        assert payload["message"] == "Only a title"

    def test_empty_title_and_body_uses_placeholder(self, config):
        _enable_ntfy(config)
        payload = build_notification_payload(config, _rec(title="", body=""))
        assert payload["message"] == "Notification"

    def test_no_tags_without_category(self, config):
        _enable_ntfy(config)
        payload = build_notification_payload(config, _rec(category=""))
        assert "tags" not in payload

    def test_explicit_http_link_becomes_click(self, config):
        _enable_ntfy(config)
        _enable_http(config)
        payload = build_notification_payload(config, _rec(link="https://example.com/x", conv_id="c-1"))
        assert payload["click"] == "https://example.com/x"

    def test_conv_id_maps_to_session_link(self, config):
        _enable_ntfy(config)
        _enable_http(config)
        payload = build_notification_payload(config, _rec(link="conv://c-1", conv_id="c-1"))
        assert payload["click"] == "https://agent.example/#conv=c-1"

    def test_no_click_when_http_disabled(self, config):
        _enable_ntfy(config)
        config.http.enabled = False
        payload = build_notification_payload(config, _rec(conv_id="c-1"))
        assert "click" not in payload


# -- Confirmation payloads ---------------------------------------------------


class TestConfirmationPayload:
    def test_actions_approve_deny_view(self, config):
        _enable_ntfy(config)
        _enable_http(config)
        payload = build_confirmation_payload(config, _confirm_event())

        assert payload["priority"] == 5  # urgent
        assert payload["title"] == "[test-agent] Approval needed: Run shell command"
        assert payload["message"] == "rm -rf build"
        actions = payload["actions"]
        assert [a["action"] for a in actions] == ["http", "http", "view"]
        assert [a["label"] for a in actions] == ["Approve", "Deny", "Open Session"]
        assert actions[0]["method"] == "POST"
        assert actions[0]["clear"] is True
        assert actions[2]["url"] == "https://agent.example/#conv=conv-1"
        assert payload["click"] == "https://agent.example/#conv=conv-1"

    def test_action_tokens_bind_confirmation_and_action(self, config):
        _enable_ntfy(config)
        _enable_http(config)
        payload = build_confirmation_payload(config, _confirm_event())

        approve = get_token_registry().consume(_token_from(payload["actions"][0]["url"]))
        deny = get_token_registry().consume(_token_from(payload["actions"][1]["url"]))
        assert approve is not None and deny is not None
        assert approve["action"] == "approve"
        assert deny["action"] == "deny"
        for token_data in (approve, deny):
            assert token_data["conv_id"] == "conv-1"
            assert token_data["confirmation_id"] == "conf-1"
            assert token_data["tool"] == "run_shell_command"

    def test_message_used_verbatim_when_present(self, config):
        _enable_ntfy(config)
        _enable_http(config)
        payload = build_confirmation_payload(config, _confirm_event(message="Run `make deploy`?"))
        assert payload["message"] == "Run `make deploy`?"

    def test_no_actions_when_http_disabled(self, config):
        _enable_ntfy(config)
        config.http.enabled = False
        payload = build_confirmation_payload(config, _confirm_event())
        assert "actions" not in payload
        assert "click" not in payload

    def test_no_actions_without_routing_ids(self, config):
        _enable_ntfy(config)
        _enable_http(config)
        payload = build_confirmation_payload(config, _confirm_event(conv_id="", confirmation_id=""))
        assert "actions" not in payload


# -- Adapter handler ---------------------------------------------------------


class TestAdapterHandler:
    @pytest.fixture(autouse=True)
    def _patch_deliver(self, monkeypatch):
        self.deliver = AsyncMock()
        monkeypatch.setattr(ntfy_mod, "_deliver", self.deliver)

    async def _drain(self):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_notification_dispatched(self, config):
        _enable_ntfy(config, min_priority="normal")
        handler = make_ntfy_adapter(config)
        await handler({"type": "notification_created", "record": _rec(priority="high").to_dict()})
        await self._drain()
        self.deliver.assert_awaited_once()
        call = self.deliver.await_args
        assert call is not None
        cfg, payload = call.args
        assert cfg.topic == "agent-alerts"
        assert payload["title"] == "Heartbeat completed"

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self, config):
        _enable_ntfy(config)
        config.notifications.channels.ntfy.enabled = False
        handler = make_ntfy_adapter(config)
        await handler({"type": "notification_created", "record": _rec().to_dict()})
        await self._drain()
        self.deliver.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_below_min_priority(self, config):
        _enable_ntfy(config, min_priority="high")
        handler = make_ntfy_adapter(config)
        await handler({"type": "notification_created", "record": _rec(priority="normal").to_dict()})
        await self._drain()
        self.deliver.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirmation_dispatched_with_actions(self, config):
        _enable_ntfy(config)
        _enable_http(config)
        handler = make_ntfy_adapter(config)
        await handler(_confirm_event())
        await self._drain()
        self.deliver.assert_awaited_once()
        call = self.deliver.await_args
        assert call is not None
        _cfg, payload = call.args
        assert len(payload["actions"]) == 3

    @pytest.mark.asyncio
    async def test_confirmation_skipped_when_disabled_by_flag(self, config):
        _enable_ntfy(config, notify_on_confirmation=False)
        _enable_http(config)
        handler = make_ntfy_adapter(config)
        await handler(_confirm_event())
        await self._drain()
        self.deliver.assert_not_called()

    @pytest.mark.asyncio
    async def test_widget_confirmation_skipped(self, config):
        """Approve/Deny cannot supply widget selection data; web UI owns it."""
        _enable_ntfy(config)
        _enable_http(config)
        handler = make_ntfy_adapter(config)
        await handler(_confirm_event(action_type="widget_response", message="Pick one"))
        await self._drain()
        self.deliver.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_unrelated_events(self, config):
        _enable_ntfy(config)
        handler = make_ntfy_adapter(config)
        await handler({"type": "tool_start", "tool": "shell"})
        await self._drain()
        self.deliver.assert_not_called()


# -- HTTP delivery -----------------------------------------------------------


class _FakeResponse:
    def raise_for_status(self):
        return None


class _RecordingClient:
    instances: list["_RecordingClient"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        _RecordingClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()


class TestDelivery:
    @pytest.mark.asyncio
    async def test_posts_json_with_bearer_token(self, config, monkeypatch):
        _enable_ntfy(config)
        cfg = config.notifications.channels.ntfy
        cfg.access_token = "tk_secret"
        _RecordingClient.instances.clear()
        monkeypatch.setattr(ntfy_mod.httpx, "AsyncClient", _RecordingClient)

        await ntfy_mod._deliver(cfg, {"topic": "agent-alerts", "title": "T"})

        client = _RecordingClient.instances[-1]
        assert client.calls == [
            {
                "url": "https://ntfy.example/",
                "json": {"topic": "agent-alerts", "title": "T"},
                "headers": {"Content-Type": "application/json", "Authorization": "Bearer tk_secret"},
            }
        ]

    @pytest.mark.asyncio
    async def test_failure_logs_and_does_not_raise(self, config, monkeypatch, caplog):
        _enable_ntfy(config)

        class _BoomClient(_RecordingClient):
            async def post(self, url, json=None, headers=None):
                raise RuntimeError("connection refused")

        monkeypatch.setattr(ntfy_mod.httpx, "AsyncClient", _BoomClient)

        with caplog.at_level(logging.WARNING, logger=ntfy_mod.__name__):
            await ntfy_mod._deliver(config.notifications.channels.ntfy, {"title": "T"})
        assert any("NTFY delivery failed" in rec.message for rec in caplog.records)


# -- Callback resolution through the HTTP endpoint ---------------------------


class TestCallbackResolution:
    @pytest.mark.asyncio
    async def test_approve_token_resolves_pending_confirmation(self, config):
        """The Approve action URL from the ntfy payload resolves the manager
        confirmation with an empty POST body (ntfy sends no body)."""
        _enable_ntfy(config)
        _enable_http(config)
        manager = AsyncMock()
        app = create_app(config, EventBus(), manager=manager)

        payload = build_confirmation_payload(config, _confirm_event())
        approve_url = payload["actions"][0]["url"]
        path = urlparse(approve_url).path + "?" + urlparse(approve_url).query

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(path)  # no body at all

        assert response.status_code == 200
        manager.respond_to_confirmation.assert_awaited_once()
        args, kwargs = manager.respond_to_confirmation.await_args
        assert args == ("conv-1", "conf-1")
        assert kwargs["approved"] is True
