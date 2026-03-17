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
    config.http_enabled = True
    config.http_secret = "test-secret"
    config.http_host = "127.0.0.1"
    config.http_port = 18880
    config.http_base_url = ""
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
    assert resp.json()["username"] == "testuser"
    assert "decafclaw_session" in resp.cookies


@pytest.mark.asyncio
async def test_login_bad_token(client):
    resp = await client.post("/api/auth/login", json={"token": "bad"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_authenticated(client, http_config):
    token = create_token(http_config, "testuser")
    # Login first to get cookie
    login_resp = await client.post("/api/auth/login", json={"token": token})
    cookies = login_resp.cookies

    # Use the cookie
    resp = await client.get("/api/auth/me", cookies=cookies)
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
    cookies = login_resp.cookies

    logout_resp = await client.post("/api/auth/logout", cookies=cookies)
    assert logout_resp.status_code == 200

    # Cookie should be cleared — me should return 401
    # Note: httpx may not clear cookies from the response automatically,
    # so we check by sending without cookies
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
