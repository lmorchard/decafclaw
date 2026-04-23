"""Tests for notification inbox REST endpoints."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw import notifications as notifs
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
    return config


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def app(http_config, bus):
    return create_app(http_config, bus)


@pytest.fixture
async def authed_client(app, http_config):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = create_token(http_config, "testuser")
        login = await c.post("/api/auth/login", json={"token": token})
        c.cookies.update(login.cookies)
        yield c


@pytest.fixture
async def anon_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# -- Auth guards --------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requires_auth(anon_client):
    resp = await anon_client.get("/api/notifications")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unread_count_requires_auth(anon_client):
    resp = await anon_client.get("/api/notifications/unread-count")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_mark_read_requires_auth(anon_client):
    resp = await anon_client.post("/api/notifications/abc/read")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_mark_all_read_requires_auth(anon_client):
    resp = await anon_client.post("/api/notifications/read-all")
    assert resp.status_code == 401


# -- GET /api/notifications ---------------------------------------------------


@pytest.mark.asyncio
async def test_empty_inbox_returns_empty(authed_client):
    resp = await authed_client.get("/api/notifications")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"records": [], "has_more": False}


@pytest.mark.asyncio
async def test_returns_records_with_read_flag(authed_client, http_config):
    a = await notifs.notify(http_config, category="t", title="A")
    await notifs.notify(http_config, category="t", title="B")
    await notifs.mark_read(http_config, a.id)

    resp = await authed_client.get("/api/notifications")
    data = resp.json()
    assert resp.status_code == 200
    assert len(data["records"]) == 2
    by_id = {r["id"]: r for r in data["records"]}
    assert by_id[a.id]["read"] is True
    assert by_id[a.id]["title"] == "A"
    # Other record is unread
    other = next(r for r in data["records"] if r["id"] != a.id)
    assert other["read"] is False


@pytest.mark.asyncio
async def test_limit_and_has_more(authed_client, http_config):
    for i in range(5):
        await notifs.notify(http_config, category="t", title=f"#{i}")
        await asyncio.sleep(0.01)
    resp = await authed_client.get("/api/notifications?limit=3")
    data = resp.json()
    assert len(data["records"]) == 3
    assert data["has_more"] is True


@pytest.mark.asyncio
async def test_before_cursor(authed_client, http_config):
    a = await notifs.notify(http_config, category="t", title="A")
    await asyncio.sleep(1.05)
    b = await notifs.notify(http_config, category="t", title="B")
    resp = await authed_client.get(f"/api/notifications?before={b.timestamp}")
    data = resp.json()
    ids = [r["id"] for r in data["records"]]
    assert a.id in ids
    assert b.id not in ids


@pytest.mark.asyncio
async def test_invalid_limit_rejected(authed_client):
    resp = await authed_client.get("/api/notifications?limit=0")
    assert resp.status_code == 400

    resp = await authed_client.get("/api/notifications?limit=999")
    assert resp.status_code == 400

    resp = await authed_client.get("/api/notifications?limit=abc")
    assert resp.status_code == 400


# -- GET /api/notifications/unread-count --------------------------------------


@pytest.mark.asyncio
async def test_unread_count_zero(authed_client):
    resp = await authed_client.get("/api/notifications/unread-count")
    assert resp.json() == {"count": 0}


@pytest.mark.asyncio
async def test_unread_count_after_notify(authed_client, http_config):
    await notifs.notify(http_config, category="t", title="A")
    await notifs.notify(http_config, category="t", title="B")
    resp = await authed_client.get("/api/notifications/unread-count")
    assert resp.json() == {"count": 2}


# -- POST /api/notifications/{id}/read ----------------------------------------


@pytest.mark.asyncio
async def test_mark_read_updates_state(authed_client, http_config):
    rec = await notifs.notify(http_config, category="t", title="A")

    resp = await authed_client.post(f"/api/notifications/{rec.id}/read")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Subsequent GET shows read=true
    list_resp = await authed_client.get("/api/notifications")
    data = list_resp.json()
    assert data["records"][0]["read"] is True

    # unread_count reflects the change
    count_resp = await authed_client.get("/api/notifications/unread-count")
    assert count_resp.json() == {"count": 0}


@pytest.mark.asyncio
async def test_mark_read_idempotent(authed_client, http_config):
    rec = await notifs.notify(http_config, category="t", title="A")
    await authed_client.post(f"/api/notifications/{rec.id}/read")
    # Second call — still succeeds
    resp = await authed_client.post(f"/api/notifications/{rec.id}/read")
    assert resp.status_code == 200


# -- POST /api/notifications/read-all -----------------------------------------


@pytest.mark.asyncio
async def test_mark_all_read(authed_client, http_config):
    await notifs.notify(http_config, category="t", title="A")
    await notifs.notify(http_config, category="t", title="B")
    await notifs.notify(http_config, category="t", title="C")

    resp = await authed_client.post("/api/notifications/read-all")
    assert resp.status_code == 200

    count = (await authed_client.get("/api/notifications/unread-count")).json()
    assert count == {"count": 0}

    listing = (await authed_client.get("/api/notifications")).json()
    assert all(r["read"] for r in listing["records"])
