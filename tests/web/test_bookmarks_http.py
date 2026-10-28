"""Tests for the /api/bookmarks REST endpoints (issue #1086).

Shared fixtures (``http_config``, ``bus``, ``app``, ``client``) live in
``tests/web/conftest.py``.
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from decafclaw.web.bookmarks import BookmarkStore


def _bookmark_file(http_config):
    return http_config.agent_path / "bookmarks.json"


def _read_bookmarks(http_config) -> dict:
    return json.loads(_bookmark_file(http_config).read_text(encoding="utf-8"))


# -- GET ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bookmarks_get_empty(client):
    resp = await client.get("/api/bookmarks")
    assert resp.status_code == 200
    assert resp.json() == {"files": [], "vault": []}


@pytest.mark.asyncio
async def test_bookmarks_get_requires_auth(app, http_config):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/bookmarks")
    assert resp.status_code == 401


# -- POST --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bookmarks_add_file(client, http_config):
    resp = await client.post(
        "/api/bookmarks",
        json={"target": "files", "path": "content/blog/drafts", "label": "Blog Drafts", "kind": "dir"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["files"] == [{"path": "content/blog/drafts", "label": "Blog Drafts", "kind": "dir"}]
    assert data["vault"] == []
    # Persisted to data/{agent_id}/bookmarks.json
    assert _read_bookmarks(http_config) == data


@pytest.mark.asyncio
async def test_bookmarks_add_vault_page(client, http_config):
    resp = await client.post(
        "/api/bookmarks",
        json={"target": "vault", "path": "journals/2026-10-20.md", "label": "Today's Journal", "kind": "page"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["vault"] == [{"path": "journals/2026-10-20.md", "label": "Today's Journal", "kind": "page"}]
    assert _read_bookmarks(http_config) == data


@pytest.mark.asyncio
async def test_bookmarks_add_defaults_label(client):
    resp = await client.post("/api/bookmarks", json={"target": "files", "path": "a/b/README.md", "kind": "file"})
    assert resp.status_code == 200
    entry = resp.json()["files"][0]
    assert entry["label"] == "README.md"
    assert entry["path"] == "a/b/README.md"


@pytest.mark.asyncio
async def test_bookmarks_add_upserts(client):
    resp = await client.post(
        "/api/bookmarks", json={"target": "files", "path": "notes.md", "kind": "file", "label": "Notes"}
    )
    assert resp.status_code == 200
    resp = await client.post(
        "/api/bookmarks", json={"target": "files", "path": "notes.md", "kind": "file", "label": "My Notes"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["files"]) == 1
    assert data["files"][0]["label"] == "My Notes"


@pytest.mark.asyncio
async def test_bookmarks_add_invalid_target(client):
    resp = await client.post("/api/bookmarks", json={"target": "pages", "path": "x.md", "kind": "page"})
    assert resp.status_code == 400
    assert "target" in resp.json()["error"]


@pytest.mark.asyncio
async def test_bookmarks_add_kind_must_match_target(client):
    resp = await client.post("/api/bookmarks", json={"target": "files", "path": "x.md", "kind": "page"})
    assert resp.status_code == 400
    resp = await client.post("/api/bookmarks", json={"target": "vault", "path": "x", "kind": "dir"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_bookmarks_add_path_validation(client):
    for bad_path in ("", "/", "a/../b", "a//b", "a/.."):
        resp = await client.post("/api/bookmarks", json={"target": "files", "path": bad_path, "kind": "file"})
        assert resp.status_code == 400, f"expected 400 for {bad_path!r}"


@pytest.mark.asyncio
async def test_bookmarks_add_missing_path(client):
    resp = await client.post("/api/bookmarks", json={"target": "files", "kind": "file"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_bookmarks_add_invalid_label(client):
    resp = await client.post("/api/bookmarks", json={"target": "files", "path": "x.md", "kind": "file", "label": 42})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_bookmarks_add_invalid_json(client):
    resp = await client.post("/api/bookmarks", content=b"not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_bookmarks_add_malformed_target_rejected(client):
    """A JSON array/object `target` must be a 400, not an unhandled TypeError."""
    resp = await client.post("/api/bookmarks", json={"target": [], "path": "notes.md", "kind": "file"})
    assert resp.status_code == 400
    assert "target" in resp.json()["error"]

    resp = await client.post("/api/bookmarks", json={"target": {}, "path": "notes.md", "kind": "file"})
    assert resp.status_code == 400
    assert "target" in resp.json()["error"]


# -- DELETE ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bookmarks_delete(client, http_config):
    await client.post("/api/bookmarks", json={"target": "files", "path": "notes.md", "kind": "file"})
    await client.post("/api/bookmarks", json={"target": "vault", "path": "pages/x", "kind": "page"})
    resp = await client.delete("/api/bookmarks", params={"target": "files", "path": "notes.md"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["files"] == []
    assert [b["path"] for b in data["vault"]] == ["pages/x"]
    assert _read_bookmarks(http_config) == data


@pytest.mark.asyncio
async def test_bookmarks_delete_missing_bookmark(client):
    resp = await client.delete("/api/bookmarks", params={"target": "files", "path": "nope.md"})
    assert resp.status_code == 404
    assert resp.json()["error"] == "bookmark not found"


@pytest.mark.asyncio
async def test_bookmarks_delete_missing_params(client):
    resp = await client.delete("/api/bookmarks", params={"target": "files"})
    assert resp.status_code == 400
    resp = await client.delete("/api/bookmarks")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_bookmarks_delete_accepts_body(client):
    await client.post("/api/bookmarks", json={"target": "files", "path": "notes.md", "kind": "file"})
    resp = await client.request(
        "DELETE",
        "/api/bookmarks",
        content=json.dumps({"target": "files", "path": "notes.md"}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["files"] == []


@pytest.mark.asyncio
async def test_bookmarks_delete_malformed_target_rejected(client):
    """A malformed `target` via the DELETE-body fallback must be a 400, not a 500."""
    resp = await client.request(
        "DELETE",
        "/api/bookmarks",
        content=json.dumps({"target": {}, "path": "notes.md"}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert "target" in resp.json()["error"]

    resp = await client.request(
        "DELETE",
        "/api/bookmarks",
        content=json.dumps({"target": "files", "path": []}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


# -- persistence across "restarts" --------------------------------------------


@pytest.mark.asyncio
async def test_bookmarks_persist_across_store_instances(client, http_config):
    await client.post("/api/bookmarks", json={"target": "files", "path": "a/b/c.md", "label": "Deep", "kind": "file"})
    await client.post(
        "/api/bookmarks",
        json={"target": "vault", "path": "agent/pages/bookmarks", "label": "Bookmarks Ingest", "kind": "folder"},
    )
    # A fresh store instance (e.g. after a server restart) sees the same data.
    store = BookmarkStore(http_config)
    data = await store.list()
    assert data["files"] == [{"path": "a/b/c.md", "label": "Deep", "kind": "file"}]
    assert data["vault"] == [{"path": "agent/pages/bookmarks", "label": "Bookmarks Ingest", "kind": "folder"}]


@pytest.mark.asyncio
async def test_bookmarks_store_ignores_corrupt_file(http_config):
    _bookmark_file(http_config).parent.mkdir(parents=True, exist_ok=True)
    _bookmark_file(http_config).write_text("{not json", encoding="utf-8")
    store = BookmarkStore(http_config)
    assert await store.list() == {"files": [], "vault": []}
