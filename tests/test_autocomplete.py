"""Tests for autocomplete API endpoints."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
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


@pytest.fixture(autouse=True)
def reset_workspace_index_state():
    """Reset workspace index global state before each test."""
    import decafclaw.workspace_index as wi
    wi._workspace_index = None
    wi._index_timestamp = 0.0
    wi._refresh_task = None
    yield
    # Cleanup after test
    wi._workspace_index = None
    wi._index_timestamp = 0.0
    if wi._refresh_task and not wi._refresh_task.done():
        wi._refresh_task.cancel()
    wi._refresh_task = None


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


# -- Workspace Index Tests -----------------------------------------------------


@pytest.mark.asyncio
async def test_autocomplete_uses_file_backed_cache(http_config):
    """CRITERION: /api/autocomplete serves results from in-memory cache without os.walk.

    Verifies that get_workspace_files() returns instantly from cache and does NOT
    execute synchronous os.walk on the HTTP request thread after initial prime.
    """
    from unittest.mock import patch

    from decafclaw.workspace_index import get_workspace_files

    # Create test files
    ws = http_config.workspace_path
    (ws / "test1.md").write_text("test")
    (ws / "test2.txt").write_text("test")

    # Track os.walk calls - patch in the workspace_index module where it's used
    original_walk = os.walk
    walk_called = False

    def tracked_walk(*args, **kwargs):
        nonlocal walk_called
        walk_called = True
        return original_walk(*args, **kwargs)

    # First call: prime the cache (this WILL call os.walk to build index)
    with patch("decafclaw.workspace_index.os.walk", tracked_walk):
        walk_called = False
        files_first = await get_workspace_files(http_config)
        assert walk_called, "Initial cache build should walk filesystem"
        assert len(files_first) == 2
        assert "test1.md" in files_first
        assert "test2.txt" in files_first

    # Wait for background persistence
    import asyncio
    await asyncio.sleep(0.1)

    # Second call: must use cache (os.walk should NOT be called)
    with patch("decafclaw.workspace_index.os.walk", tracked_walk):
        walk_called = False
        files_second = await get_workspace_files(http_config)
        assert not walk_called, "Subsequent query must use cache, not os.walk"
        assert files_second == files_first


@pytest.mark.asyncio
async def test_autocomplete_background_refresh(http_config):
    """CRITERION: Cache updates in background via asyncio.create_task without blocking.

    Verifies that when cache TTL expires or is invalidated, the system updates
    the index in a background task and persists to workspace_index.json atomically.
    """
    import asyncio
    import json

    from decafclaw.workspace_index import get_workspace_files, invalidate_workspace_file_cache

    # Create initial files
    ws = http_config.workspace_path
    (ws / "initial.md").write_text("test")

    # Prime the cache
    files_initial = await get_workspace_files(http_config)
    assert len(files_initial) == 1
    assert "initial.md" in files_initial

    # Wait for background persistence
    await asyncio.sleep(0.2)
    index_path = http_config.agent_path / "workspace_index.json"
    assert index_path.exists(), "Index should be persisted to disk"
    with open(index_path) as f:
        disk_data = json.load(f)
        assert "initial.md" in disk_data["files"]

    # Add new file and invalidate cache
    (ws / "new_file.txt").write_text("test")
    invalidate_workspace_file_cache()

    # Query again - should return old cache immediately but trigger background refresh
    files_stale = await get_workspace_files(http_config)
    assert "new_file.txt" not in files_stale, "Should return stale cache immediately"

    # Wait for background refresh to complete
    await asyncio.sleep(0.5)

    # Next query should see the new file
    files_refreshed = await get_workspace_files(http_config)
    assert "new_file.txt" in files_refreshed, "Background refresh should have updated cache"

    # Verify atomic disk persistence (tmpfile + os.replace)
    with open(index_path) as f:
        final_disk_data = json.load(f)
        assert "new_file.txt" in final_disk_data["files"]

    # Verify no .tmp file left behind (atomic write cleanup)
    tmp_path = index_path.with_suffix(".tmp")
    assert not tmp_path.exists(), "Temporary file should be cleaned up after atomic write"
