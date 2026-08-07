"""Tests for autocomplete API endpoints."""

import os
from pathlib import Path
import pytest
from unittest.mock import MagicMock, AsyncMock
from httpx import ASGITransport, AsyncClient

from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import create_token


@pytest.fixture
def http_config(config, monkeypatch, tmp_path):
    config.http.enabled = True
    config.http.secret = "test-secret"
    config.http.host = "127.0.0.1"
    config.http.port = 18880
    config.http.base_url = ""
    monkeypatch.chdir(tmp_path)
    config.agent.data_home = "data"
    config.vault.vault_path = "workspace/vault/"
    config.vault.agent_folder = "agent/"
    
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    config.vault_root.mkdir(parents=True, exist_ok=True)
    return config


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def app(http_config, bus):
    return create_app(http_config, bus)


@pytest.fixture
async def client(app, http_config):
    """Client with a valid auth cookie."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        token = create_token(http_config, "testuser")
        resp = await c.post("/api/auth/login", json={"token": token})
        c.cookies = resp.cookies
        yield c


# -- Autocomplete --------------------------------------------------------------


@pytest.mark.asyncio
async def test_autocomplete_requires_auth(app):
    """Accessing the endpoint without auth should return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/autocomplete")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_autocomplete_empty(client):
    """Empty query / empty state should return empty results array."""
    resp = await client.get("/api/autocomplete")
    assert resp.status_code == 200
    assert resp.json() == {"results": []}


@pytest.mark.asyncio
async def test_autocomplete_vault_pages(client, http_config):
    """Vault page searches should match pages by title/path."""
    vault = http_config.vault_root
    (vault / "TestPage.md").write_text("# Test Page")
    (vault / "OtherPage.md").write_text("# Other Page")
    
    # Matching query
    resp = await client.get("/api/autocomplete?q=test")
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["type"] == "vault"
    assert results[0]["id"] == "TestPage"
    assert results[0]["label"] == "TestPage"

    # Match all (no query)
    resp_all = await client.get("/api/autocomplete")
    assert resp_all.status_code == 200
    results_all = resp_all.json()["results"]
    assert len(results_all) == 2


@pytest.mark.asyncio
async def test_autocomplete_workspace_files(client, http_config):
    """Workspace file searches should find matching text files, ignoring hidden/node_modules."""
    ws = http_config.workspace_path
    (ws / "src").mkdir(parents=True, exist_ok=True)
    (ws / "node_modules").mkdir(parents=True, exist_ok=True)
    (ws / ".git").mkdir(parents=True, exist_ok=True)

    (ws / "src" / "agent.py").write_text("print('hello')")
    (ws / "src" / "config.py").write_text("config = {}")
    (ws / "node_modules" / "foo.js").write_text("ignored")
    (ws / ".git" / "config").write_text("ignored")
    (ws / "src" / ".hidden.py").write_text("ignored")

    # Match a file
    resp = await client.get("/api/autocomplete?q=agent")
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["type"] == "file"
    assert results[0]["id"] == "src/agent.py"

    # Ignore node_modules, .git, and hidden files
    resp_all = await client.get("/api/autocomplete")
    assert resp_all.status_code == 200
    ids = [item["id"] for item in resp_all.json()["results"]]
    assert "src/agent.py" in ids
    assert "src/config.py" in ids
    assert "node_modules/foo.js" not in ids
    assert ".git/config" not in ids
    assert "src/.hidden.py" not in ids


@pytest.mark.asyncio
async def test_autocomplete_mcp_resources(client, monkeypatch):
    """MCP resource searches should match server and resource names."""
    # Mock MCP Registry
    mock_registry = MagicMock()
    
    # Mock resource object
    class MockResource:
        def __init__(self, name, uri, description=""):
            self.name = name
            self.uri = uri
            self.description = description

    mock_res1 = MockResource("summary", "demo://summary", "Resource Summary")
    mock_res2 = MockResource("notes", "demo://notes", "Resource Notes")
    
    mock_registry.get_resources.return_value = [
        ("demo_server", mock_res1),
        ("demo_server", mock_res2),
    ]

    monkeypatch.setattr("decafclaw.mcp_client.get_registry", lambda: mock_registry)

    # Match resource name
    resp = await client.get("/api/autocomplete?q=summary")
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["type"] == "mcp"
    assert results[0]["id"] == "demo_server/summary"
    assert results[0]["label"] == "mcp/demo_server/summary"
    assert results[0]["description"] == "Resource Summary"
