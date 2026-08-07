"""Tests for vault REST API endpoints."""

import os
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
    assert "Hello world." in data["body"]


@pytest.mark.asyncio
async def test_vault_read_splits_frontmatter(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Split.md").write_text(
        "---\nimportance: 0.7\ntags:\n- a\n---\n# Split\n\nBody text.\n"
    )
    resp = await client.get("/api/vault/agent/pages/Split")
    assert resp.status_code == 200
    data = resp.json()
    assert data["frontmatter"] == {"importance": 0.7, "tags": ["a"]}
    assert data["body"] == "# Split\n\nBody text.\n"
    assert "---" not in data["body"]
    assert data["frontmatter_raw"] == "importance: 0.7\ntags:\n- a"
    assert "frontmatter_error" not in data
    assert "content" not in data


@pytest.mark.asyncio
async def test_vault_read_no_frontmatter(client, http_config):
    """frontmatter_raw is "" — not null — when there is no block."""
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Plain.md").write_text("# Plain\n\nJust body.\n")
    resp = await client.get("/api/vault/agent/pages/Plain")
    data = resp.json()
    assert data["frontmatter"] == {}
    assert data["frontmatter_raw"] == ""
    assert data["body"] == "# Plain\n\nJust body.\n"
    assert "frontmatter_error" not in data


@pytest.mark.asyncio
async def test_vault_read_malformed_frontmatter(client, http_config):
    """Malformed YAML surfaces as an error plus the raw block, not silence.

    frontmatter_raw is present on well-formed pages too, so the raw editor
    can be seeded with real bytes rather than a re-serialized dict.
    """
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Bad.md").write_text("---\nthis: is: not: valid\n---\nBody.\n")
    resp = await client.get("/api/vault/agent/pages/Bad")
    assert resp.status_code == 200
    data = resp.json()
    assert data["frontmatter"] == {}
    assert data["frontmatter_raw"] == "this: is: not: valid"
    assert data["frontmatter_error"]
    assert data["body"] == "Body.\n"


@pytest.mark.asyncio
async def test_vault_read_date_frontmatter_serializes(client, http_config):
    """An unquoted ISO date must not 500 the read.

    PyYAML resolves `date: 2026-06-22` to a `datetime.date`, which
    `json.dumps` rejects — so a single such line took down the whole page
    view with a 500 rather than degrading. Dates come back as ISO strings.
    """
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Dated.md").write_text(
        "---\ndate: 2026-06-22\nweek: 2026-W26\ntags:\n- blog\n---\nBody.\n"
    )
    resp = await client.get("/api/vault/agent/pages/Dated")
    assert resp.status_code == 200
    data = resp.json()
    assert data["frontmatter"] == {
        "date": "2026-06-22",
        # Not a YAML timestamp, so it was always a plain string.
        "week": "2026-W26",
        "tags": ["blog"],
    }
    assert data["body"] == "Body.\n"
    # The raw block is still the real bytes, unquoted, for the raw editor.
    assert "date: 2026-06-22" in data["frontmatter_raw"]


@pytest.mark.asyncio
async def test_vault_read_nested_date_serializes(client, http_config):
    """Dates nested under a mapping/sequence are coerced too, not just top level."""
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Nested.md").write_text(
        "---\nreview:\n  due: 2026-07-01\nseen:\n- 2026-01-01\n---\nBody.\n"
    )
    resp = await client.get("/api/vault/agent/pages/Nested")
    assert resp.status_code == 200
    assert resp.json()["frontmatter"] == {
        "review": {"due": "2026-07-01"},
        "seen": ["2026-01-01"],
    }


@pytest.mark.asyncio
async def test_vault_write_date_frontmatter_serializes(client, http_config):
    """The write response echoes frontmatter, so it had the same 500.

    The date must survive in the file unquoted — coercion is a JSON-boundary
    concern, so it must not leak back into what gets written to disk.
    """
    path = http_config.vault_agent_pages_dir / "DatedWrite.md"
    path.write_text("---\ndate: 2026-06-22\n---\nOld body.\n")

    resp = await client.put(
        "/api/vault/agent/pages/DatedWrite",
        json={"frontmatter": {"summary": "A summary."}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["frontmatter"] == {"date": "2026-06-22", "summary": "A summary."}
    assert "date: 2026-06-22" in path.read_text()


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
async def test_vault_write_body_only_preserves_frontmatter(client, http_config):
    """A body-only PUT must leave the frontmatter block byte-identical.

    Regression test: the web UI had no frontmatter awareness, so Milkdown
    parsed the YAML into markdown nodes and serialized the mangled result
    back over the file on save.
    """
    path = http_config.vault_agent_pages_dir / "Fm.md"
    original_block = (
        "---\n"
        "importance: 0.7\n"
        "tags:\n"
        "- 0din\n"
        "---\n"
    )
    path.write_text(original_block + "# 0din\n\nOld body.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Fm",
        json={"content": "# 0din\n\nNew body.\n"},
    )
    assert resp.status_code == 200

    text = path.read_text()
    assert text.startswith(original_block)
    assert text == original_block + "# 0din\n\nNew body.\n"


@pytest.mark.asyncio
async def test_vault_write_body_only_preserves_malformed_frontmatter(
    client, http_config,
):
    """Malformed YAML must survive a body write untouched.

    parse_frontmatter returns ({}, body) on YAMLError, so reserializing via
    serialize_frontmatter would silently delete the block entirely.
    """
    path = http_config.vault_agent_pages_dir / "Broken.md"
    original_block = "---\nthis: is: not: valid: yaml\n---\n"
    path.write_text(original_block + "Body.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Broken",
        json={"content": "New body.\n"},
    )
    assert resp.status_code == 200
    assert path.read_text() == original_block + "New body.\n"


@pytest.mark.asyncio
async def test_vault_write_body_only_preserves_key_order_and_comments(
    client, http_config,
):
    """Hand-authored formatting must survive a body write.

    yaml.dump defaults to sort_keys=True and drops comments, so this is what
    catches a regression back to reserializing on the body path.
    """
    path = http_config.vault_agent_pages_dir / "Hand.md"
    original_block = (
        "---\n"
        "# why this matters\n"
        "tags:\n"
        "- zeta\n"
        "importance: 0.4\n"
        "---\n"
    )
    path.write_text(original_block + "Body.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Hand",
        json={"content": "Edited.\n"},
    )
    assert resp.status_code == 200
    assert path.read_text() == original_block + "Edited.\n"


@pytest.mark.asyncio
async def test_vault_write_frontmatter_patch_merges(client, http_config):
    path = http_config.vault_agent_pages_dir / "Patch.md"
    path.write_text("---\nimportance: 0.4\ntags:\n- keep\n---\nBody.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Patch",
        json={"frontmatter": {"summary": "A summary."}},
    )
    assert resp.status_code == 200
    assert resp.json()["frontmatter"]["summary"] == "A summary."
    assert resp.json()["frontmatter_raw"] == "importance: 0.4\nsummary: A summary.\ntags:\n- keep"

    from decafclaw.frontmatter import parse_frontmatter
    meta, body = parse_frontmatter(path.read_text())
    assert meta == {
        "importance": 0.4,
        "tags": ["keep"],
        "summary": "A summary.",
    }
    assert body == "Body.\n"


@pytest.mark.asyncio
async def test_vault_write_frontmatter_patch_coerces(client, http_config):
    """importance 1.7 -> 1.0 proves merge_frontmatter is really in the path."""
    path = http_config.vault_agent_pages_dir / "Coerce.md"
    path.write_text("---\nimportance: 0.4\n---\nBody.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Coerce",
        json={"frontmatter": {"importance": 1.7, "tags": "solo"}},
    )
    assert resp.status_code == 200
    data = resp.json()["frontmatter"]
    assert data["importance"] == 1.0
    assert data["tags"] == ["solo"]


@pytest.mark.asyncio
async def test_vault_write_frontmatter_null_removes_key(client, http_config):
    """A patch cannot delete by omission, so null means remove.

    merge_frontmatter would otherwise write a literal `tags: null`.
    """
    path = http_config.vault_agent_pages_dir / "Del.md"
    path.write_text("---\nimportance: 0.4\ntags:\n- gone\n---\nBody.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Del",
        json={"frontmatter": {"tags": None}},
    )
    assert resp.status_code == 200
    assert "tags" not in resp.json()["frontmatter"]
    text = path.read_text()
    assert "tags" not in text
    assert "null" not in text
    assert "importance: 0.4" in text


@pytest.mark.asyncio
async def test_vault_write_frontmatter_patch_leaves_body_alone(
    client, http_config,
):
    path = http_config.vault_agent_pages_dir / "BodySafe.md"
    path.write_text("---\nimportance: 0.4\n---\n# Head\n\nExact body.\n")
    resp = await client.put(
        "/api/vault/agent/pages/BodySafe",
        json={"frontmatter": {"summary": "S"}},
    )
    assert resp.status_code == 200
    assert path.read_text().endswith("# Head\n\nExact body.\n")


@pytest.mark.asyncio
async def test_vault_write_frontmatter_patch_on_malformed_is_rejected(
    client, http_config,
):
    """Merging into an unparseable block would silently discard it."""
    path = http_config.vault_agent_pages_dir / "BadPatch.md"
    original = "---\nthis: is: not: valid\n---\nBody.\n"
    path.write_text(original)

    resp = await client.put(
        "/api/vault/agent/pages/BadPatch",
        json={"frontmatter": {"summary": "S"}},
    )
    assert resp.status_code == 400
    assert "malformed" in resp.json()["error"]
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_vault_write_frontmatter_and_body_one_write(client, http_config):
    path = http_config.vault_agent_pages_dir / "Both.md"
    path.write_text("---\nimportance: 0.4\n---\nOld.\n")
    resp = await client.put(
        "/api/vault/agent/pages/Both",
        json={"frontmatter": {"summary": "S"}, "body": "New.\n"},
    )
    assert resp.status_code == 200
    text = path.read_text()
    assert "summary: S" in text
    assert text.endswith("New.\n")


@pytest.mark.asyncio
async def test_vault_write_frontmatter_stale_modified_conflicts(
    client, http_config,
):
    """A merge against a stale read would resurrect a just-deleted key."""
    path = http_config.vault_agent_pages_dir / "Stale.md"
    path.write_text("---\nimportance: 0.4\n---\nBody.\n")
    resp = await client.put(
        "/api/vault/agent/pages/Stale",
        json={"frontmatter": {"summary": "S"}, "modified": 1.0},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_vault_write_requires_a_payload(client, http_config):
    resp = await client.put("/api/vault/agent/pages/Nothing", json={})
    assert resp.status_code == 400


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


# -- vault_recent --------------------------------------------------------------


@pytest.mark.asyncio
async def test_vault_recent_empty(client):
    resp = await client.get("/api/vault/recent")
    assert resp.status_code == 200
    assert resp.json()["pages"] == []


@pytest.mark.asyncio
async def test_vault_recent_sorted_by_mtime(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    pages_dir.mkdir(parents=True, exist_ok=True)
    old_page = pages_dir / "Old.md"
    new_page = pages_dir / "New.md"
    old_page.write_text("# Old")
    new_page.write_text("# New")
    os.utime(old_page, (1_700_000_000, 1_700_000_000))
    os.utime(new_page, (1_700_000_100, 1_700_000_100))

    resp = await client.get("/api/vault/recent")
    assert resp.status_code == 200
    pages = resp.json()["pages"]
    assert len(pages) >= 2
    titles = [p["title"] for p in pages]
    assert titles.index("New") < titles.index("Old")


@pytest.mark.asyncio
async def test_vault_recent_respects_limit(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    pages_dir.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (pages_dir / f"Page{i}.md").write_text(f"# Page {i}")

    resp = await client.get("/api/vault/recent?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()["pages"]) == 2


@pytest.mark.asyncio
async def test_vault_recent_includes_subfolders(client, http_config):
    sub = http_config.vault_agent_pages_dir / "people"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "Deep.md").write_text("# Deep")

    resp = await client.get("/api/vault/recent")
    pages = resp.json()["pages"]
    deep = [p for p in pages if p["title"] == "Deep"]
    assert len(deep) == 1
    assert "people" in deep[0]["folder"]


@pytest.mark.asyncio
async def test_vault_recent_excludes_user_pages(client, http_config):
    """Pages outside the agent dir should not appear in recent changes."""
    vault = http_config.vault_root
    (vault / "UserNote.md").write_text("# My personal note")
    pages_dir = http_config.vault_agent_pages_dir
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "AgentPage.md").write_text("# Agent page")

    resp = await client.get("/api/vault/recent")
    pages = resp.json()["pages"]
    titles = [p["title"] for p in pages]
    assert "AgentPage" in titles
    assert "UserNote" not in titles


# -- vault_tags ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_vault_tags_empty(client):
    resp = await client.get("/api/vault/tags")
    assert resp.status_code == 200
    assert resp.json()["tags"] == []


@pytest.mark.asyncio
async def test_vault_tags_sorted_by_count_desc(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Rust.md").write_text("---\ntags: [Rust]\n---\nabout rust")
    (pages_dir / "Async.md").write_text("body mentions #Rust and #async")

    resp = await client.get("/api/vault/tags")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tags"] == [
        {
            "tag": "Rust",
            "count": 2,
            "pages": ["agent/pages/Async.md", "agent/pages/Rust.md"],
        },
        {"tag": "async", "count": 1, "pages": ["agent/pages/Async.md"]},
    ]


@pytest.mark.asyncio
async def test_vault_list_includes_summary(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "WithFm.md").write_text(
        "---\nsummary: A short summary.\n---\n# Body\n"
    )
    (pages_dir / "NoFm.md").write_text("# Body only\n")
    resp = await client.get("/api/vault?folder=agent/pages")
    assert resp.status_code == 200
    pages = {p["title"]: p for p in resp.json()["pages"]}
    assert pages["WithFm"]["summary"] == "A short summary."
    assert pages["NoFm"]["summary"] == ""


@pytest.mark.asyncio
async def test_vault_list_summary_survives_malformed_frontmatter(
    client, http_config,
):
    """A broken page must not break the whole listing."""
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Bad.md").write_text("---\nthis: is: not: valid\n---\nBody.\n")
    resp = await client.get("/api/vault?folder=agent/pages")
    assert resp.status_code == 200
    pages = {p["title"]: p for p in resp.json()["pages"]}
    assert pages["Bad"]["summary"] == ""


@pytest.mark.asyncio
async def test_vault_list_summary_survives_undecodable_page(client, http_config):
    """Invalid UTF-8 raises UnicodeDecodeError, not OSError — catch both."""
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Binary.md").write_bytes(b"---\nsummary: x\n---\n\xff\xfe\n")
    resp = await client.get("/api/vault?folder=agent/pages")
    assert resp.status_code == 200
    pages = {p["title"]: p for p in resp.json()["pages"]}
    assert pages["Binary"]["summary"] == ""


@pytest.mark.asyncio
async def test_vault_recent_includes_summary(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Recent.md").write_text("---\nsummary: Recent one.\n---\nB\n")
    resp = await client.get("/api/vault/recent")
    assert resp.status_code == 200
    pages = {p["title"]: p for p in resp.json()["pages"]}
    assert pages["Recent"]["summary"] == "Recent one."


# -- vault_changed event publishing -------------------------------------------
#
# These tests verify the REST handlers publish `vault_changed` events on the
# success path so the WebSocket forwarder can broadcast them to clients.


def _vault_changed_events(bus) -> list[dict]:
    """Subscribe a list-collector to `bus` and return the list.

    Use BEFORE issuing the request so events emitted during the call land
    in the returned list.
    """
    captured: list[dict] = []

    async def _capture(event: dict) -> None:
        if event.get("type") == "vault_changed":
            captured.append(event)

    bus.subscribe(_capture)
    return captured


@pytest.mark.asyncio
async def test_rest_vault_create_publishes_vault_changed(client, bus):
    events = _vault_changed_events(bus)
    resp = await client.post(
        "/api/vault",
        json={"name": "agent/pages/EventCreate"},
    )
    assert resp.status_code == 200
    assert len(events) == 1
    assert events[0]["kind"] == "create"
    assert events[0]["path"] == "agent/pages/EventCreate.md"


@pytest.mark.asyncio
async def test_rest_vault_create_folder_publishes_vault_changed(client, bus):
    events = _vault_changed_events(bus)
    resp = await client.post(
        "/api/vault/folders",
        json={"folder": "agent/pages/event-folder"},
    )
    assert resp.status_code == 200
    assert len(events) == 1
    assert events[0]["kind"] == "create"
    assert events[0]["path"] == "agent/pages/event-folder"


@pytest.mark.asyncio
async def test_rest_vault_write_new_page_publishes_create(client, bus):
    events = _vault_changed_events(bus)
    resp = await client.put(
        "/api/vault/agent/pages/EventWrite",
        json={"content": "# New"},
    )
    assert resp.status_code == 200
    assert len(events) == 1
    assert events[0]["kind"] == "create"
    assert events[0]["path"] == "agent/pages/EventWrite.md"


@pytest.mark.asyncio
async def test_rest_vault_write_existing_page_publishes_update(
    client, http_config, bus,
):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "EventUpdate.md").write_text("# Old")
    events = _vault_changed_events(bus)
    resp = await client.put(
        "/api/vault/agent/pages/EventUpdate",
        json={"content": "# New"},
    )
    assert resp.status_code == 200
    assert len(events) == 1
    assert events[0]["kind"] == "update"
    assert events[0]["path"] == "agent/pages/EventUpdate.md"


@pytest.mark.asyncio
async def test_rest_vault_rename_publishes_vault_changed(
    client, http_config, bus,
):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "RenameMe.md").write_text("# Old")
    events = _vault_changed_events(bus)
    resp = await client.put(
        "/api/vault/agent/pages/RenameMe",
        json={"rename_to": "agent/pages/Renamed"},
    )
    assert resp.status_code == 200
    # Only the rename publish should fire — the write branch is bypassed.
    assert len(events) == 1
    assert events[0]["kind"] == "rename"
    assert events[0]["path"] == "agent/pages/Renamed.md"


@pytest.mark.asyncio
async def test_rest_vault_delete_publishes_vault_changed(
    client, http_config, bus,
):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "DeleteMe.md").write_text("# Bye")
    events = _vault_changed_events(bus)
    resp = await client.delete("/api/vault/agent/pages/DeleteMe")
    assert resp.status_code == 200
    assert len(events) == 1
    assert events[0]["kind"] == "delete"
    assert events[0]["path"] == "agent/pages/DeleteMe.md"


@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_replaces(client, http_config):
    """Replace, not merge: a key absent from the submission is gone.

    This is the test that distinguishes the two paths — it fails if the raw
    field is wired to merge_frontmatter.
    """
    path = http_config.vault_agent_pages_dir / "Raw.md"
    path.write_text("---\nimportance: 0.4\ntags:\n- gone\n---\nBody.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Raw",
        json={"frontmatter_raw": "summary: Only this.\n"},
    )
    assert resp.status_code == 200
    assert resp.json()["frontmatter"] == {"summary": "Only this."}
    text = path.read_text()
    assert "tags" not in text
    assert "importance" not in text
    assert text.endswith("Body.\n")


@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_preserves_user_text(
    client, http_config,
):
    """Stored verbatim, so hand-written comments and key order survive."""
    path = http_config.vault_agent_pages_dir / "RawVerbatim.md"
    path.write_text("---\nimportance: 0.4\n---\nBody.\n")

    resp = await client.put(
        "/api/vault/agent/pages/RawVerbatim",
        json={"frontmatter_raw": "# a note\nzeta: 1\nalpha: 2\n"},
    )
    assert resp.status_code == 200
    assert path.read_text() == (
        "---\n# a note\nzeta: 1\nalpha: 2\n---\nBody.\n"
    )


@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_empty_removes_block(
    client, http_config,
):
    path = http_config.vault_agent_pages_dir / "RawEmpty.md"
    path.write_text("---\nimportance: 0.4\n---\nBody.\n")
    resp = await client.put(
        "/api/vault/agent/pages/RawEmpty",
        json={"frontmatter_raw": "   \n"},
    )
    assert resp.status_code == 200
    assert path.read_text() == "Body.\n"
    assert resp.json()["frontmatter"] == {}


@pytest.mark.asyncio
async def test_vault_write_frontmatter_patch_emptying_removes_block(
    client, http_config,
):
    """Nulling every key drops the block entirely, not `{}` or bare delimiters.

    _dump_frontmatter returns None for an empty dict and join_frontmatter then
    omits the delimiters. Asserted so the behavior is intentional rather than
    incidental.
    """
    path = http_config.vault_agent_pages_dir / "PatchEmpty.md"
    path.write_text("---\nimportance: 0.4\ntags:\n- a\n---\nBody.\n")
    resp = await client.put(
        "/api/vault/agent/pages/PatchEmpty",
        json={"frontmatter": {"importance": None, "tags": None}},
    )
    assert resp.status_code == 200
    assert resp.json()["frontmatter"] == {}
    assert resp.json()["frontmatter_raw"] == ""
    assert path.read_text() == "Body.\n"


@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_malformed_is_rejected(
    client, http_config,
):
    path = http_config.vault_agent_pages_dir / "RawBad.md"
    original = "---\nimportance: 0.4\n---\nBody.\n"
    path.write_text(original)
    resp = await client.put(
        "/api/vault/agent/pages/RawBad",
        json={"frontmatter_raw": "this: is: not: valid\n"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_non_mapping_is_rejected(
    client, http_config,
):
    path = http_config.vault_agent_pages_dir / "RawList.md"
    original = "---\nimportance: 0.4\n---\nBody.\n"
    path.write_text(original)
    resp = await client.put(
        "/api/vault/agent/pages/RawList",
        json={"frontmatter_raw": "- just\n- a list\n"},
    )
    assert resp.status_code == 400
    assert "mapping" in resp.json()["error"]
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_with_delimiter_is_rejected(
    client, http_config,
):
    """A bare `---` line inside the block would split the file in two."""
    path = http_config.vault_agent_pages_dir / "RawDelim.md"
    original = "---\nimportance: 0.4\n---\nBody.\n"
    path.write_text(original)
    resp = await client.put(
        "/api/vault/agent/pages/RawDelim",
        json={"frontmatter_raw": "a: 1\n---\nb: 2\n"},
    )
    assert resp.status_code == 400
    assert "---" in resp.json()["error"]
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_vault_write_frontmatter_both_shapes_rejected(
    client, http_config,
):
    """Patch and replace cannot be reconciled in one write."""
    path = http_config.vault_agent_pages_dir / "RawBoth.md"
    original = "---\nimportance: 0.4\n---\nBody.\n"
    path.write_text(original)
    resp = await client.put(
        "/api/vault/agent/pages/RawBoth",
        json={"frontmatter": {"summary": "S"}, "frontmatter_raw": "a: 1\n"},
    )
    assert resp.status_code == 400
    assert "mutually exclusive" in resp.json()["error"]
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_vault_write_empty_payload_names_every_accepted_key(
    client, http_config,
):
    """A payload with no writable key must name all three shapes, not just body."""
    path = http_config.vault_agent_pages_dir / "Empty.md"
    original = "---\nimportance: 0.4\n---\nBody.\n"
    path.write_text(original)
    resp = await client.put("/api/vault/agent/pages/Empty", json={})
    assert resp.status_code == 400
    error = resp.json()["error"]
    for key in ("body", "content", "frontmatter", "frontmatter_raw"):
        assert key in error, f"400 message does not mention {key!r}: {error!r}"
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_vault_rename_rejects_combined_write_payloads(client, http_config):
    """Every write key must collide with rename_to, not just `content`.

    A payload the endpoint accepts but silently discards is worse than a 400.
    """
    path = http_config.vault_agent_pages_dir / "Combo.md"
    path.write_text("---\nimportance: 0.4\n---\nBody.\n")
    for key, value in [
        ("content", "x"),
        ("body", "x"),
        ("frontmatter", {"summary": "s"}),
        ("frontmatter_raw", "a: 1\n"),
    ]:
        resp = await client.put(
            "/api/vault/agent/pages/Combo",
            json={"rename_to": "agent/pages/Renamed", key: value},
        )
        assert resp.status_code == 400, f"{key} did not collide with rename_to"
        assert key in resp.json()["error"]
    assert path.exists(), "no rename should have happened"


@pytest.mark.asyncio
async def test_vault_write_patch_preserves_pre_existing_bare_key(
    client, http_config,
):
    """A typed patch must only delete the keys *it* nulled.

    A bare `aliases:` (very common in Obsidian, and what any empty scalar
    parses to) reads back as `{"aliases": None}`. Stripping every null from
    the merged dict would delete it on any unrelated patch — the user drags
    the importance slider and silently loses their aliases.
    """
    path = http_config.vault_agent_pages_dir / "BareKey.md"
    path.write_text(
        "---\nimportance: 0.4\naliases:\nsummary: S\n---\nBody.\n",
    )

    resp = await client.put(
        "/api/vault/agent/pages/BareKey",
        json={"frontmatter": {"importance": 0.7}},
    )
    assert resp.status_code == 200
    assert "aliases" in resp.json()["frontmatter"]
    text = path.read_text()
    assert "aliases" in text
    assert "importance: 0.7" in text
    assert "summary: S" in text


@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_allows_indented_delimiter(
    client, http_config,
):
    """Only a column-0 `---` terminates the block, so an indented one is fine.

    PyYAML emits exactly this when folding a multi-line value, so the typed
    path can produce a block the raw editor used to refuse to save back.
    """
    path = http_config.vault_agent_pages_dir / "RawIndented.md"
    path.write_text("---\nimportance: 0.4\n---\nBody.\n")

    resp = await client.put(
        "/api/vault/agent/pages/RawIndented",
        json={"frontmatter_raw": "summary: |\n  line one\n  ---\n  line two\n"},
    )
    assert resp.status_code == 200
    # The stored block is `fm_raw.strip("\n")`, so the literal scalar loses
    # its trailing newline — the `---` line surviving intact is the point.
    assert resp.json()["frontmatter"]["summary"] == "line one\n---\nline two"
    # The indented `---` must not have split the file on the way back out.
    assert path.read_text().endswith("Body.\n")
    get_resp = await client.get("/api/vault/agent/pages/RawIndented")
    assert get_resp.json()["body"] == "Body.\n"
    assert get_resp.json()["frontmatter"]["summary"] == "line one\n---\nline two"


@pytest.mark.asyncio
async def test_vault_write_body_only_reports_frontmatter_error(
    client, http_config,
):
    """A body write over malformed YAML succeeds by splicing it back verbatim.

    That leaves `frontmatter: {}` with an unparseable block on disk, so the
    response has to say so — otherwise `{}` is ambiguous between "empty" and
    "we couldn't read it".
    """
    path = http_config.vault_agent_pages_dir / "BodyOverBad.md"
    path.write_text("---\nthis: is: not: valid\n---\nOld.\n")

    resp = await client.put(
        "/api/vault/agent/pages/BodyOverBad",
        json={"body": "New.\n"},
    )
    assert resp.status_code == 200
    assert resp.json()["frontmatter"] == {}
    assert resp.json()["frontmatter_error"]
    assert path.read_text() == "---\nthis: is: not: valid\n---\nNew.\n"


@pytest.mark.asyncio
async def test_vault_write_frontmatter_patch_reports_no_error(client, http_config):
    """The metadata paths validate first, so they never leave a parse error."""
    path = http_config.vault_agent_pages_dir / "PatchNoErr.md"
    path.write_text("---\nimportance: 0.4\n---\nBody.\n")
    resp = await client.put(
        "/api/vault/agent/pages/PatchNoErr",
        json={"frontmatter": {"summary": "S"}},
    )
    assert resp.status_code == 200
    assert resp.json()["frontmatter_error"] == ""


@pytest.mark.asyncio
async def test_vault_write_bad_body_type_names_the_field_sent(
    client, http_config,
):
    """`content` is an alias for `body`; the error must name what was sent."""
    resp = await client.put(
        "/api/vault/agent/pages/BadType",
        json={"content": 123},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "content must be a string"

    resp = await client.put(
        "/api/vault/agent/pages/BadType",
        json={"body": 123},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "body must be a string"


def test_resolve_frontmatter_helper():
    from decafclaw.http_server import _resolve_frontmatter
    new_raw, err = _resolve_frontmatter("title: Test", {"title": "Test"}, None, None, None)
    assert new_raw == "title: Test"
    assert err is None

    new_raw, err = _resolve_frontmatter("title: Test", {"title": "Test"}, None, "title: New\n", None)
    assert new_raw == "title: New"
    assert err is None

    new_raw, err = _resolve_frontmatter("title: Test", {"title": "Test"}, None, "title: Test\n---\n", None)
    assert new_raw is None
    assert err["status_code"] == 400

    new_raw, err = _resolve_frontmatter("title: Test\nimportance: 0.5", {"title": "Test", "importance": 0.5}, None, None, {"importance": 0.8})
    assert new_raw is not None
    assert "importance: 0.8" in new_raw
    assert err is None

