"""Route regressions for editable config files."""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import create_token


@pytest.fixture
def http_config(config):
    config.http.enabled = True
    config.http.secret = "test-secret"
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def app(http_config):
    return create_app(http_config, EventBus())


@pytest.fixture
async def client(app, http_config):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as result:
        token = create_token(http_config, "testuser")
        response = await result.post("/api/auth/login", json={"token": token})
        result.cookies = response.cookies
        yield result


@pytest.mark.asyncio
async def test_config_routes_require_authentication(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/config/files")).status_code == 401
        assert (await client.get("/api/config/files/AGENT.md")).status_code == 401
        assert (
            await client.put(
                "/api/config/files/AGENT.md",
                json={"content": "denied"},
            )
        ).status_code == 401


@pytest.mark.asyncio
async def test_list_is_bare_array_with_nullable_missing_mtime(client):
    response = await client.get("/api/config/files")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    agent = next(item for item in payload if item["path"] == "AGENT.md")
    assert agent == {
        "name": "AGENT.md",
        "path": "AGENT.md",
        "description": "Behavioral instructions",
        "scope": "admin",
        "modified": None,
        "exists": False,
    }


@pytest.mark.asyncio
async def test_read_falls_back_to_bundled_default(client):
    response = await client.get("/api/config/files/AGENT.md")
    assert response.status_code == 200
    payload = response.json()
    assert payload["content"]
    assert payload["modified"] is None
    assert payload["name"] == "AGENT.md"
    assert payload["default"] is True


@pytest.mark.asyncio
async def test_encoded_schedule_path_round_trips_and_force_save_omits_modified(
    client,
    http_config,
):
    relative = "workspace/schedules/Daily #1.md"
    target = http_config.workspace_path / "schedules" / "Daily #1.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Initial")

    read = await client.get("/api/config/files/workspace/schedules/Daily%20%231.md")
    assert read.status_code == 200
    assert read.json()["content"] == "# Initial"
    assert read.json()["default"] is False

    saved = await client.put(
        "/api/config/files/workspace/schedules/Daily%20%231.md",
        json={"content": "# Forced"},
    )
    assert saved.status_code == 200
    assert saved.json()["ok"] is True
    assert isinstance(saved.json()["modified"], float)
    assert target.read_text() == "# Forced"

    listed = (await client.get("/api/config/files")).json()
    assert any(item["path"] == relative for item in listed)


@pytest.mark.asyncio
async def test_write_keeps_conflict_and_legacy_400_responses(client, http_config):
    target = http_config.agent_path / "USER.md"
    target.write_text("old")
    stale_mtime = target.stat().st_mtime
    os.utime(target, (stale_mtime + 100, stale_mtime + 100))

    conflict = await client.put(
        "/api/config/files/USER.md",
        json={"content": "stale", "modified": stale_mtime},
    )
    assert conflict.status_code == 409
    assert target.read_text() == "old"

    invalid_body = await client.put(
        "/api/config/files/USER.md",
        json={"content": 7},
    )
    assert invalid_body.status_code == 400
    assert invalid_body.json() == {"error": "content (string) required"}

    invalid_path = await client.put(
        "/api/config/files/not-allowed.md",
        json={"content": "no"},
    )
    assert invalid_path.status_code == 400
    assert invalid_path.json() == {"error": "invalid config path"}
