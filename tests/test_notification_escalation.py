"""Deferred notification delivery and confirmation escalation (#1664)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from decafclaw import notification_escalation
from decafclaw import notifications as notifs
from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest
from decafclaw.conversation_manager import ConversationManager
from decafclaw.events import EventBus
from decafclaw.notification_channels import ntfy as ntfy_mod
from decafclaw.notification_channels import should_defer
from decafclaw.notification_channels.email import make_email_adapter
from decafclaw.notification_channels.ntfy import make_ntfy_adapter
from decafclaw.notification_escalation import EscalationScheduler, pending_path


def _rec(**overrides) -> notifs.NotificationRecord:
    return notifs.NotificationRecord(
        id=overrides.get("id", "rec-1"),
        timestamp=overrides.get("timestamp", "2026-11-21T12:00:00Z"),
        category=overrides.get("category", "heartbeat"),
        title=overrides.get("title", "Heartbeat completed"),
        priority=overrides.get("priority", "normal"),
        body=overrides.get("body", ""),
        conv_id=overrides.get("conv_id"),
        attended=overrides.get("attended", True),
    )


def _enable_ntfy(config, *, escalate=60.0, confirmation_escalate=60.0, delivery="deferred"):
    cfg = config.notifications.channels.ntfy
    cfg.enabled = True
    cfg.server_url = "https://ntfy.example"
    cfg.topic = "agent-alerts"
    cfg.min_priority = "low"
    cfg.delivery = delivery
    cfg.escalate_after_sec = escalate
    cfg.confirmation_escalate_after_sec = confirmation_escalate
    cfg.notify_on_confirmation = True
    return config


@pytest.fixture(autouse=True)
def _clean_scheduler_registry():
    notification_escalation.reset_schedulers()
    yield
    notification_escalation.reset_schedulers()


def _scheduler(config, bus=None, now=None) -> EscalationScheduler:
    return EscalationScheduler(config, bus, now=now)


@pytest.mark.asyncio
async def test_arm_preserves_sub_second_delay(config):
    fixed = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    scheduler = _scheduler(config, now=lambda: fixed)
    try:
        entry = scheduler.arm("ntfy", "rec-1", 0.25, {"record_id": "rec-1"})
        assert datetime.fromisoformat(entry.fire_at) == fixed + timedelta(seconds=0.25)
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_deferred_delivery_waits_then_fires(config):
    delivered = asyncio.Event()
    payloads: list[dict] = []

    async def deliver(payload: dict) -> None:
        payloads.append(payload)
        delivered.set()

    scheduler = _scheduler(config)
    scheduler.register("ntfy", deliver)
    scheduler.arm("ntfy", "rec-1", 0.01, {"record_id": "rec-1"})
    try:
        assert payloads == []
        await asyncio.wait_for(delivered.wait(), timeout=2)
        assert payloads == [{"record_id": "rec-1"}]
        assert scheduler.pending() == []
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_notification_read_cancels_before_fire(config):
    bus = EventBus()
    delivered = asyncio.Event()

    async def deliver(_payload: dict) -> None:
        delivered.set()

    scheduler = _scheduler(config, bus)
    scheduler.register("ntfy", deliver)
    scheduler.start()
    try:
        scheduler.arm("ntfy", "rec-1", 60, {"record_id": "rec-1"})
        assert len(scheduler.pending()) == 1

        await bus.publish({"type": "notification_read", "ids": ["rec-1"], "unread_count": 0})

        assert scheduler.pending() == []
        await asyncio.sleep(0)
        assert not delivered.is_set()
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_pending_escalation_survives_restart(config):
    """An armed entry is re-armed by a new scheduler after a restart."""
    first = _scheduler(config)
    first.arm("ntfy", "rec-1", 0.01, {"record_id": "rec-1"})
    # Simulate a crash: the first scheduler's timer never runs.
    for task in first._tasks.values():
        task.cancel()
    await asyncio.gather(*first._tasks.values(), return_exceptions=True)

    path = pending_path(config)
    assert path.exists()
    assert config.workspace_path not in path.parents

    delivered = asyncio.Event()

    async def deliver(_payload: dict) -> None:
        delivered.set()

    second = _scheduler(config)
    second.register("ntfy", deliver)
    second.start()
    try:
        await asyncio.wait_for(delivered.wait(), timeout=2)
    finally:
        await second.stop()


@pytest.mark.asyncio
async def test_overdue_escalation_fires_on_startup(config):
    scheduler = _scheduler(config)
    scheduler.arm("ntfy", "rec-1", 0, {"record_id": "rec-1"})
    for task in scheduler._tasks.values():
        task.cancel()
    await asyncio.gather(*scheduler._tasks.values(), return_exceptions=True)

    delivered = asyncio.Event()

    async def deliver(_payload: dict) -> None:
        delivered.set()

    restarted = _scheduler(config)
    restarted.register("ntfy", deliver)
    restarted.start()
    try:
        await asyncio.wait_for(delivered.wait(), timeout=2)
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_ntfy_deferred_record_is_not_pushed_until_due(config, monkeypatch):
    _enable_ntfy(config, escalate=60)
    deliver = AsyncMock()
    monkeypatch.setattr(ntfy_mod, "_deliver", deliver)

    scheduler = _scheduler(config)
    handler = make_ntfy_adapter(config, scheduler=scheduler)
    try:
        await handler({"type": "notification_created", "record": _rec(priority="normal").to_dict()})
        await asyncio.sleep(0)
        deliver.assert_not_awaited()
        assert [e.key for e in scheduler.pending()] == ["rec-1"]
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_ntfy_deferred_push_fires_from_the_durable_record(config, monkeypatch):
    _enable_ntfy(config, escalate=0.01)
    fired = asyncio.Event()
    pushed: list[dict] = []

    async def deliver(_cfg, payload):
        pushed.append(payload)
        fired.set()

    monkeypatch.setattr(ntfy_mod, "_deliver", deliver)

    record = await notifs.notify(config, category="agent", title="deferred live", priority="normal", attended=True)
    scheduler = _scheduler(config)
    handler = make_ntfy_adapter(config, scheduler=scheduler)
    try:
        await handler({"type": "notification_created", "record": record.to_dict()})
        await asyncio.wait_for(fired.wait(), timeout=2)
        assert pushed and pushed[0]["title"] == "deferred live"
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_ntfy_immediate_channel_pushes_at_publish(config, monkeypatch):
    _enable_ntfy(config, delivery="immediate")
    deliver = AsyncMock()
    monkeypatch.setattr(ntfy_mod, "_deliver", deliver)

    scheduler = _scheduler(config)
    handler = make_ntfy_adapter(config, scheduler=scheduler)
    try:
        await handler({"type": "notification_created", "record": _rec(priority="normal").to_dict()})
        await asyncio.sleep(0)
        deliver.assert_awaited_once()
        assert scheduler.pending() == []
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_ntfy_high_priority_bypasses_deferral(config, monkeypatch):
    _enable_ntfy(config, escalate=60)
    deliver = AsyncMock()
    monkeypatch.setattr(ntfy_mod, "_deliver", deliver)

    scheduler = _scheduler(config)
    handler = make_ntfy_adapter(config, scheduler=scheduler)
    try:
        await handler({"type": "notification_created", "record": _rec(priority="high").to_dict()})
        await asyncio.sleep(0)
        deliver.assert_awaited_once()
        assert scheduler.pending() == []
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_email_deferred_channel_waits_for_escalation(config, monkeypatch):
    """The same scheduler path serves every interrupting channel."""
    config.email.enabled = True
    config.email.smtp_host = "smtp.example.com"
    config.email.sender_address = "bot@example.com"
    cfg = config.notifications.channels.email
    cfg.enabled = True
    cfg.recipient_addresses = ["ops@example.com"]
    cfg.min_priority = "low"
    cfg.delivery = "deferred"
    cfg.escalate_after_sec = 0.01

    fired = asyncio.Event()
    sent: list[dict] = []

    async def fake_send(_config, to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})
        fired.set()

    monkeypatch.setattr("decafclaw.mail.send_mail", fake_send)

    # Deferred delivery re-reads the durable inbox record at fire time, so
    # the record must exist there (the real notify() path writes it).
    record = await notifs.notify(config, category="agent", title="live", priority="normal", attended=True)

    scheduler = _scheduler(config)
    handler = make_email_adapter(config, scheduler=scheduler)
    try:
        await handler({"type": "notification_created", "record": record.to_dict()})
        assert sent == []
        assert [e.key for e in scheduler.pending()] == [record.id]
        await asyncio.wait_for(fired.wait(), timeout=2)
        assert sent and sent[0]["to"] == ["ops@example.com"]
    finally:
        await scheduler.stop()


class TestEligibility:
    def test_high_priority_never_defers(self, config):
        _enable_ntfy(config)
        cfg = config.notifications.channels.ntfy
        assert not should_defer(cfg, _rec(priority="high"))

    def test_unattended_record_never_defers(self, config):
        _enable_ntfy(config)
        cfg = config.notifications.channels.ntfy
        assert not should_defer(cfg, _rec(attended=False))

    def test_boundary_categories_never_defer(self, config):
        _enable_ntfy(config)
        cfg = config.notifications.channels.ntfy
        for category in ("security", "denial", "egress", "outside_workspace"):
            assert not should_defer(cfg, _rec(category=category))

    def test_immediate_mode_never_defers(self, config):
        _enable_ntfy(config, delivery="immediate")
        cfg = config.notifications.channels.ntfy
        assert not should_defer(cfg, _rec())

    def test_attended_normal_record_defers(self, config):
        _enable_ntfy(config)
        cfg = config.notifications.channels.ntfy
        assert should_defer(cfg, _rec())


@pytest.mark.asyncio
async def test_confirmation_answered_in_transcript_never_pushes(config, monkeypatch):
    """The motivating case: answer the card before the threshold."""
    _enable_ntfy(config, confirmation_escalate=60)
    config.http.enabled = True
    config.http.secret = "test-secret"
    config.http.enabled = True
    config.http.base_url = "https://agent.example"

    deliver = AsyncMock()
    monkeypatch.setattr(ntfy_mod, "_deliver", deliver)

    bus = EventBus()
    scheduler = _scheduler(config, bus)
    handler = make_ntfy_adapter(config, scheduler=scheduler)
    bus.subscribe(handler)
    scheduler.start()
    notification_escalation.register_scheduler(scheduler)

    manager = ConversationManager(config, bus)
    conv_id = "conv-1664"
    manager._get_or_create(conv_id)
    seen = asyncio.Event()

    def observe(event: dict) -> None:
        if event.get("type") == "confirmation_request":
            seen.set()

    bus.subscribe(observe)
    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow shell command?",
        timeout=None,
    )
    waiter = asyncio.create_task(manager.request_confirmation(conv_id, request))
    try:
        await asyncio.wait_for(seen.wait(), timeout=2)
        assert [e.key for e in scheduler.pending()] == [request.confirmation_id]

        await manager.respond_to_confirmation(conv_id, request.confirmation_id, approved=True)
        response = await asyncio.wait_for(waiter, timeout=2)
        assert response.approved is True

        assert scheduler.pending() == []
        await asyncio.sleep(0)
        deliver.assert_not_awaited()
    finally:
        await scheduler.stop()
        if not waiter.done():
            waiter.cancel()


@pytest.mark.asyncio
async def test_confirmation_escalation_fires_when_still_pending(config, monkeypatch):
    _enable_ntfy(config, confirmation_escalate=0.01)
    config.http.enabled = False

    fired = asyncio.Event()
    pushed: list[dict] = []

    async def deliver(_cfg, payload):
        pushed.append(payload)
        fired.set()

    monkeypatch.setattr(ntfy_mod, "_deliver", deliver)

    bus = EventBus()
    scheduler = _scheduler(config, bus)
    handler = make_ntfy_adapter(config, scheduler=scheduler)
    bus.subscribe(handler)
    scheduler.start()
    try:
        await handler(
            {
                "type": "confirmation_request",
                "conv_id": "conv-1",
                "confirmation_id": "conf-1",
                "action_type": "run_shell_command",
                "action_data": {"command": "make deploy"},
                "message": "Allow?",
            }
        )
        assert pushed == []

        await asyncio.wait_for(fired.wait(), timeout=2)
        assert len(pushed) == 1
        assert pushed[0]["title"].endswith("Approval needed: Run shell command")
    finally:
        await scheduler.stop()
