"""Tests for web gateway token authentication."""

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import (
    create_token,
    list_tokens,
    revoke_token,
    validate_token,
)

# -- Token management tests ----------------------------------------------------


def test_create_and_validate_token(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    token = create_token(config, "testuser")
    assert token.startswith("dfc_")
    assert validate_token(config, token) == "testuser"


def test_validate_invalid_token(config):
    assert validate_token(config, "bad-token") is None


def test_validate_empty_token(config):
    assert validate_token(config, "") is None


def test_revoke_token(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    token = create_token(config, "testuser")
    assert revoke_token(config, token) is True
    assert validate_token(config, token) is None


def test_revoke_nonexistent_token(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    assert revoke_token(config, "dfc_nonexistent") is False


def test_list_tokens(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    create_token(config, "alice")
    create_token(config, "bob")
    tokens = list_tokens(config)
    assert len(tokens) == 2
    usernames = {t["username"] for t in tokens}
    assert usernames == {"alice", "bob"}


def test_multiple_tokens_same_user(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    t1 = create_token(config, "alice")
    t2 = create_token(config, "alice")
    assert t1 != t2
    assert validate_token(config, t1) == "alice"
    assert validate_token(config, t2) == "alice"


# -- Auth route tests ----------------------------------------------------------


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
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_login_sets_cookie(client, http_config):
    token = create_token(http_config, "testuser")
    resp = await client.post("/api/auth/login", json={"token": token})
    assert resp.status_code == 200
    assert resp.json() == {"username": "testuser"}
    assert resp.cookies["decafclaw_session"] == token
    cookie = resp.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=2592000" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie


@pytest.mark.asyncio
async def test_login_bad_token(client):
    resp = await client.post("/api/auth/login", json={"token": "bad"})
    assert resp.status_code == 401
    assert resp.json() == {"error": "invalid token"}
    assert "set-cookie" not in resp.headers


@pytest.mark.asyncio
async def test_auth_me_authenticated(client, http_config):
    token = create_token(http_config, "testuser")
    # Login first to get cookie
    login_resp = await client.post("/api/auth/login", json={"token": token})
    client.cookies.update(login_resp.cookies)

    # Use the cookie (set on client, not per-request)
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["username"] == "testuser"


@pytest.mark.asyncio
async def test_auth_me_unauthenticated(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_cookie(client, http_config):
    token = create_token(http_config, "testuser")
    login_resp = await client.post("/api/auth/login", json={"token": token})
    client.cookies.update(login_resp.cookies)

    logout_resp = await client.post("/api/auth/logout")
    assert logout_resp.status_code == 200

    assert logout_resp.json() == {"ok": True}
    cookie = logout_resp.headers["set-cookie"]
    assert 'decafclaw_session=""' in cookie
    assert "Max-Age=0" in cookie
    assert "expires=" in cookie.lower()
    assert "Path=/" in cookie
    assert "SameSite=lax" in cookie
    assert "decafclaw_session" not in client.cookies
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": None}, {"token": "bad", "extra": True}])
async def test_login_preserves_manual_request_handling(client, body):
    resp = await client.post("/api/auth/login", json=body)
    assert resp.status_code == 401
    assert resp.json() == {"error": "invalid token"}
    assert "set-cookie" not in resp.headers


@pytest.mark.asyncio
async def test_login_accepts_extra_fields(client, http_config):
    token = create_token(http_config, "testuser")
    resp = await client.post("/api/auth/login", json={"token": token, "extra": True})
    assert resp.status_code == 200
    assert resp.json() == {"username": "testuser"}
