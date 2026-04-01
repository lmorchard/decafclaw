"""Tests for vault REST API endpoints."""

from pathlib import Path

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
    # Use a relative data_home so vault_root is relative — this triggers
    # the bug where resolve_page() returns absolute but vault_root is relative.
    monkeypatch.chdir(tmp_path)
    config.agent.data_home = "data"
    config.vault.vault_path = "workspace/vault/"
    config.vault.agent_folder = "agent/"
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.vault_root.mkdir(parents=True, exist_ok=True)
    config.vault_agent_pages_dir.mkdir(parents=True, exist_ok=True)
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


# -- vault_list ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_vault_list_empty(client):
    resp = await client.get("/api/vault")
    assert resp.status_code == 200
    data = resp.json()
    assert data["folder"] == ""
    assert data["pages"] == []
    assert isinstance(data["folders"], list)


@pytest.mark.asyncio
async def test_vault_list_with_pages(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Foo.md").write_text("# Foo")
    (pages_dir / "Bar.md").write_text("# Bar")
    resp = await client.get("/api/vault?folder=agent/pages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["folder"] == "agent/pages"
    paths = [p["path"] for p in data["pages"]]
    assert "agent/pages/Bar" in paths
    assert "agent/pages/Foo" in paths


@pytest.mark.asyncio
async def test_vault_list_root_shows_folders(client, http_config):
    """Root listing should show 'agent' as a subfolder."""
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Foo.md").write_text("# Foo")
    resp = await client.get("/api/vault")
    assert resp.status_code == 200
    data = resp.json()
    folder_names = [f["name"] for f in data["folders"]]
    assert "agent" in folder_names
    assert data["pages"] == []


@pytest.mark.asyncio
async def test_vault_list_invalid_folder(client):
    resp = await client.get("/api/vault?folder=../etc")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_vault_list_nonexistent_folder(client):
    resp = await client.get("/api/vault?folder=does/not/exist")
    assert resp.status_code == 404


# -- vault_read ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_vault_read_page(client, http_config):
    """Read a page — resolved path must work with relative vault root.

    Regression test: resolve_page returns absolute paths but _vault_root
    was relative, causing relative_to() to fail with ValueError.
    """
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "TestPage.md").write_text("# Test\n\nHello world.")
    resp = await client.get("/api/vault/agent/pages/TestPage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "TestPage"
    assert data["path"] == "agent/pages/TestPage"
    assert "Hello world." in data["content"]


@pytest.mark.asyncio
async def test_vault_read_by_stem(client, http_config):
    """Read a page by stem name (without full path)."""
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "SomePage.md").write_text("# Some Page")
    resp = await client.get("/api/vault/SomePage")
    assert resp.status_code == 200
    assert resp.json()["title"] == "SomePage"


@pytest.mark.asyncio
async def test_vault_read_not_found(client):
    resp = await client.get("/api/vault/NonExistent")
    assert resp.status_code == 404


# -- vault_write ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_vault_write_new_page(client, http_config):
    resp = await client.put(
        "/api/vault/agent/pages/NewPage",
        json={"content": "# New Page\n\nContent."},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    path = http_config.vault_agent_pages_dir / "NewPage.md"
    assert path.exists()
    assert "Content." in path.read_text()


@pytest.mark.asyncio
async def test_vault_write_path_traversal(client):
    resp = await client.put(
        "/api/vault/../../../etc/passwd",
        json={"content": "hack"},
    )
    # Starlette normalizes the path, so this may be 400 or 404 — either way, not 200
    assert resp.status_code != 200


# -- vault_create --------------------------------------------------------------


@pytest.mark.asyncio
async def test_vault_create_page(client, http_config):
    resp = await client.post(
        "/api/vault",
        json={"name": "agent/pages/Created"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    path = http_config.vault_agent_pages_dir / "Created.md"
    assert path.exists()


@pytest.mark.asyncio
async def test_vault_create_duplicate(client, http_config):
    (http_config.vault_agent_pages_dir / "Dupe.md").write_text("exists")
    resp = await client.post(
        "/api/vault",
        json={"name": "agent/pages/Dupe"},
    )
    assert resp.status_code == 409


# -- vault_rename --------------------------------------------------------------


@pytest.mark.asyncio
async def test_vault_rename_page(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "OldName.md").write_text("# Old")
    resp = await client.put(
        "/api/vault/agent/pages/OldName",
        json={"rename_to": "agent/pages/NewName"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["path"] == "agent/pages/NewName"
    assert not (pages_dir / "OldName.md").exists()
    assert (pages_dir / "NewName.md").exists()


@pytest.mark.asyncio
async def test_vault_rename_to_new_folder(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "MovMe.md").write_text("# Move me")
    resp = await client.put(
        "/api/vault/agent/pages/MovMe",
        json={"rename_to": "agent/subfolder/MovMe"},
    )
    assert resp.status_code == 200
    vault = http_config.vault_root
    assert (vault / "agent" / "subfolder" / "MovMe.md").exists()
    assert not (pages_dir / "MovMe.md").exists()


@pytest.mark.asyncio
async def test_vault_rename_conflict(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "A.md").write_text("# A")
    (pages_dir / "B.md").write_text("# B")
    resp = await client.put(
        "/api/vault/agent/pages/A",
        json={"rename_to": "agent/pages/B"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_vault_rename_not_found(client):
    resp = await client.put(
        "/api/vault/agent/pages/NonExistent",
        json={"rename_to": "agent/pages/Whatever"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_vault_rename_traversal(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Safe.md").write_text("# Safe")
    resp = await client.put(
        "/api/vault/agent/pages/Safe",
        json={"rename_to": "../../../etc/passwd"},
    )
    assert resp.status_code == 400


# -- vault_create_folder -------------------------------------------------------


@pytest.mark.asyncio
async def test_vault_create_folder(client, http_config):
    resp = await client.post(
        "/api/vault/folders",
        json={"folder": "agent/pages/newfolder"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert (http_config.vault_root / "agent" / "pages" / "newfolder").is_dir()


@pytest.mark.asyncio
async def test_vault_create_folder_duplicate(client, http_config):
    (http_config.vault_root / "agent" / "pages" / "existing").mkdir(parents=True)
    resp = await client.post(
        "/api/vault/folders",
        json={"folder": "agent/pages/existing"},
    )
    assert resp.status_code == 409


# -- vault_delete --------------------------------------------------------------


@pytest.mark.asyncio
async def test_vault_delete_page(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "ToDelete.md").write_text("# Delete me")
    resp = await client.delete("/api/vault/agent/pages/ToDelete")
    assert resp.status_code == 200
    assert not (pages_dir / "ToDelete.md").exists()


@pytest.mark.asyncio
async def test_vault_delete_not_found(client):
    resp = await client.delete("/api/vault/agent/pages/NonExistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_vault_delete_cleans_empty_dirs(client, http_config):
    vault = http_config.vault_root
    sub = vault / "agent" / "temp" / "deep"
    sub.mkdir(parents=True)
    (sub / "Only.md").write_text("# Only page")
    resp = await client.delete("/api/vault/agent/temp/deep/Only")
    assert resp.status_code == 200
    # Both temp/ and temp/deep/ should be cleaned up
    assert not (vault / "agent" / "temp").exists()
