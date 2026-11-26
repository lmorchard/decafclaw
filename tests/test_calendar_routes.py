"""Tests for /api/calendar REST endpoints (#1899)."""

import asyncio
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw.calendar_events import CalendarEvent, save_calendar_events
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


# -- occurrences (#1903) ------------------------------------------------------


@pytest.mark.asyncio
async def test_occurrences_expand_a_daily_series(client):
    created = await client.post(
        "/api/calendar/events",
        json=_body(fire_at="2027-01-01T09:00:00+00:00", recurrence="FREQ=DAILY", timezone="UTC"),
    )
    assert created.status_code == 201, created.text
    response = await client.get("/api/calendar/occurrences?from=2027-01-01T00:00:00Z&to=2027-01-04T00:00:00Z")
    assert response.status_code == 200, response.text
    occurrences = response.json()["occurrences"]
    assert [o["occurrence_at"][:10] for o in occurrences] == ["2027-01-01", "2027-01-02", "2027-01-03"]
    assert all(o["recurrence"] is True for o in occurrences)


@pytest.mark.asyncio
async def test_occurrences_one_shot_and_boundaries(client):
    await client.post("/api/calendar/events", json=_body(fire_at="2027-01-02T09:00:00+00:00"))
    response = await client.get("/api/calendar/occurrences?from=2027-01-02T09:00:00Z&to=2027-01-02T09:00:00Z")
    assert response.status_code == 200
    assert len(response.json()["occurrences"]) == 1
    outside = await client.get("/api/calendar/occurrences?from=2027-01-03T00:00:00Z&to=2027-01-04T00:00:00Z")
    assert outside.json()["occurrences"] == []


@pytest.mark.asyncio
async def test_occurrences_until_and_cancelled(client):
    created = await client.post(
        "/api/calendar/events",
        json=_body(
            fire_at="2027-01-01T09:00:00+00:00",
            recurrence="FREQ=DAILY",
            timezone="UTC",
            recurrence_until="2027-01-02T09:00:00+00:00",
        ),
    )
    assert created.status_code == 201
    response = await client.get("/api/calendar/occurrences?from=2027-01-01T00:00:00Z&to=2027-01-05T00:00:00Z")
    assert len(response.json()["occurrences"]) == 2

    event_id = created.json()["event"]["event_id"]
    await client.delete(f"/api/calendar/events/{event_id}")
    after = await client.get("/api/calendar/occurrences?from=2027-01-01T00:00:00Z&to=2027-01-05T00:00:00Z")
    assert after.json()["occurrences"] == []


@pytest.mark.asyncio
async def test_occurrences_conv_filter_and_fired(client, http_config):
    conversation_dir(http_config, "conv-2").mkdir(parents=True, exist_ok=True)
    await client.post("/api/calendar/events", json=_body(fire_at="2027-01-02T09:00:00+00:00"))
    await client.post("/api/calendar/events", json=_body(conv_id="conv-2", fire_at="2027-01-02T10:00:00+00:00"))

    filtered = await client.get(
        "/api/calendar/occurrences?from=2027-01-02T00:00:00Z&to=2027-01-03T00:00:00Z&conv_id=conv-2"
    )
    assert len(filtered.json()["occurrences"]) == 1

    fired = CalendarEvent(
        event_id="cal_fired",
        conv_id="conv-1",
        target_conv_id="conv-1",
        session_mode="current",
        title="Fired",
        reason="Already ran",
        fire_at="2027-01-02T11:00:00+00:00",
        fire_at_ts=datetime.fromisoformat("2027-01-02T11:00:00+00:00").timestamp(),
        created_at="",
        updated_at="",
        user_id="testuser",
        status="fired",
    )
    save_calendar_events(http_config, "conv-1", [fired])
    without = await client.get(
        "/api/calendar/occurrences?from=2027-01-02T00:00:00Z&to=2027-01-03T00:00:00Z&conv_id=conv-1"
    )
    assert all(o["event_id"] != "cal_fired" for o in without.json()["occurrences"])
    with_fired = await client.get(
        "/api/calendar/occurrences?from=2027-01-02T00:00:00Z&to=2027-01-03T00:00:00Z&conv_id=conv-1&include_fired=true"
    )
    assert any(o["event_id"] == "cal_fired" for o in with_fired.json()["occurrences"])
