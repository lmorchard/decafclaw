"""Tests for /api/calendar REST endpoints (#1899)."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw.calendar_manager import CalendarManager
from decafclaw.conversation_paths import conversation_dir
from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import create_token


@pytest.fixture
def http_config(config):
    config.http.enabled = True
    config.http.secret = "test-secret"
    config.http.host = "127.0.0.1"
    config.http.port = 18880
    config.http.base_url = ""
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    conversation_dir(config, "conv-1").mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
async def app(http_config, bus):
    app = create_app(http_config, bus)
    manager = CalendarManager(http_config)
    app.state.manager = SimpleNamespace(calendar=manager)
    yield app
    await manager.shutdown()


@pytest.fixture
async def client(app, http_config):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = create_token(http_config, "testuser")
        resp = await c.post("/api/auth/login", json={"token": token})
        c.cookies = resp.cookies
        yield c


def _body(**overrides):
    body = {
        "conv_id": "conv-1",
        "title": "Dentist",
        "reason": "Call the dentist",
        "fire_at": "2027-01-01T09:00:00+00:00",
        "timezone": "UTC",
        "recurrence": "",
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_create_list_update_snooze_cancel(client):
    created = await client.post("/api/calendar/events", json=_body())
    assert created.status_code == 201, created.text
    event = created.json()["event"]
    event_id = event["event_id"]
    assert event["origin_title"] == ""  # no index entry in this fixture
    assert event["status"] == "pending"

    listed = await client.get("/api/calendar/events")
    assert listed.status_code == 200
    assert [e["event_id"] for e in listed.json()["events"]] == [event_id]

    updated = await client.put(f"/api/calendar/events/{event_id}", json={"title": "Dentist (moved)"})
    assert updated.status_code == 200
    assert updated.json()["event"]["title"] == "Dentist (moved)"

    snoozed = await client.post(f"/api/calendar/events/{event_id}/snooze", json={"duration_seconds": 600})
    assert snoozed.status_code == 200
    assert snoozed.json()["event"]["fire_at_ts"] > event["fire_at_ts"]

    cancelled = await client.delete(f"/api/calendar/events/{event_id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is True
    listed_after = await client.get("/api/calendar/events?status=all")
    assert listed_after.json()["events"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_create_recurring_and_invalid_rule(client):
    created = await client.post(
        "/api/calendar/events",
        json=_body(recurrence="FREQ=DAILY", timezone="America/Los_Angeles"),
    )
    assert created.status_code == 201, created.text
    event = created.json()["event"]
    assert event["recurrence"] == "FREQ=DAILY"
    assert event["recurrence_start"] == event["fire_at"]
    assert event["recurrence_summary"] == "daily"

    invalid = await client.post("/api/calendar/events", json=_body(recurrence="FREQ=NOPE"))
    assert invalid.status_code == 400
    assert "invalid recurrence" in invalid.json()["error"]


@pytest.mark.asyncio
async def test_create_requires_existing_conversation(client):
    response = await client.post("/api/calendar/events", json=_body(conv_id="missing"))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_unknown_event_ids_404(client):
    assert (await client.put("/api/calendar/events/nope", json={"title": "x"})).status_code == 404
    assert (await client.delete("/api/calendar/events/nope")).status_code == 404
    assert (await client.post("/api/calendar/events/nope/snooze", json={"duration_seconds": 60})).status_code == 404


@pytest.mark.asyncio
async def test_dedicated_session_target(client):
    created = await client.post(
        "/api/calendar/events",
        json=_body(
            session_mode="dedicated",
            session_key="morning-briefing",
            session_title="Morning Briefing",
        ),
    )
    assert created.status_code == 201, created.text
    event = created.json()["event"]
    assert event["target_conv_id"].startswith("web-testuser-cal")
    assert event["target_conv_id"] != event["conv_id"]


@pytest.mark.asyncio
async def test_unauthenticated_401(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/calendar/events")).status_code == 401


@pytest.mark.asyncio
async def test_calendar_changed_published(client, bus):
    events: list[dict] = []

    async def capture(event):
        if event.get("type") == "calendar_changed":
            events.append(event)

    bus.subscribe(capture)
    created = await client.post("/api/calendar/events", json=_body())
    assert created.status_code == 201
    assert events and events[0]["action"] == "created"


@pytest.mark.asyncio
async def test_cross_user_isolation(client, http_config):
    created = await client.post("/api/calendar/events", json=_body())
    assert created.status_code == 201

    # Build a second authenticated client from the same app.
    app = client._transport.app if hasattr(client, "_transport") else None
    if app is None:
        pytest.skip("httpx client does not expose its transport app")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        token = create_token(http_config, "otheruser")
        resp = await other.post("/api/auth/login", json={"token": token})
        other.cookies = resp.cookies
        listed = await other.get("/api/calendar/events?status=all")
        assert listed.status_code == 200
        assert listed.json()["events"] == []
