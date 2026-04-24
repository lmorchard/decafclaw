"""Tests for workspace REST API endpoints.

Shared fixtures (``http_config``, ``bus``, ``app``, ``client``) live in
``tests/web/conftest.py``.
"""

import os
from pathlib import Path

import pytest

# -- workspace_list ------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_list_empty(client):
    resp = await client.get("/api/workspace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["folder"] == ""
    assert data["folders"] == []
    assert data["files"] == []


@pytest.mark.asyncio
async def test_workspace_list_root_folders_and_files(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "skills").mkdir()
    (workspace / "notes").mkdir()
    (workspace / "zfile.md").write_text("z")
    (workspace / "afile.md").write_text("a")

    resp = await client.get("/api/workspace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["folder"] == ""
    folder_names = [f["name"] for f in data["folders"]]
    assert folder_names == ["notes", "skills"]  # alphabetical
    file_names = [f["name"] for f in data["files"]]
    assert file_names == ["afile.md", "zfile.md"]  # alphabetical


@pytest.mark.asyncio
async def test_workspace_list_folder_scope(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "skills").mkdir()
    (workspace / "skills" / "SKILL.md").write_text("# skill")
    (workspace / "skills" / "subdir").mkdir()

    resp = await client.get("/api/workspace?folder=skills")
    assert resp.status_code == 200
    data = resp.json()
    assert data["folder"] == "skills"
    folder_names = [f["name"] for f in data["folders"]]
    assert folder_names == ["subdir"]
    assert any(f["name"] == "SKILL.md" for f in data["files"])
    # Folder path is relative to workspace root
    skill_file = next(f for f in data["files"] if f["name"] == "SKILL.md")
    assert skill_file["path"] == "skills/SKILL.md"


@pytest.mark.asyncio
async def test_workspace_list_file_metadata(client, http_config):
    workspace: Path = http_config.workspace_path
    f = workspace / "note.md"
    f.write_text("hello")

    resp = await client.get("/api/workspace")
    data = resp.json()
    entry = next(x for x in data["files"] if x["name"] == "note.md")
    assert entry["path"] == "note.md"
    assert entry["size"] == 5
    assert isinstance(entry["modified"], float)
    assert entry["kind"] == "text"
    assert entry["readonly"] is False
    assert entry["secret"] is False


@pytest.mark.asyncio
async def test_workspace_list_secret_flag(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / ".env").write_text("SECRET=1")

    resp = await client.get("/api/workspace")
    data = resp.json()
    env = next(x for x in data["files"] if x["name"] == ".env")
    assert env["secret"] is True


@pytest.mark.asyncio
async def test_workspace_list_readonly_flag_jsonl(client, http_config):
    workspace: Path = http_config.workspace_path
    conv_dir = workspace / "conversations"
    conv_dir.mkdir()
    (conv_dir / "abc123.jsonl").write_text('{"role":"user"}\n')

    resp = await client.get("/api/workspace?folder=conversations")
    data = resp.json()
    entry = next(x for x in data["files"] if x["name"] == "abc123.jsonl")
    assert entry["readonly"] is True


@pytest.mark.asyncio
async def test_workspace_list_readonly_flag_db(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "embeddings.db").write_bytes(b"SQLite\x00")

    resp = await client.get("/api/workspace")
    data = resp.json()
    entry = next(x for x in data["files"] if x["name"] == "embeddings.db")
    assert entry["readonly"] is True


@pytest.mark.asyncio
async def test_workspace_list_includes_dotfiles(client, http_config):
    """Backend returns all entries — frontend decides what to hide."""
    workspace: Path = http_config.workspace_path
    (workspace / ".hidden").write_text("hi")
    (workspace / ".cache").mkdir()

    resp = await client.get("/api/workspace")
    data = resp.json()
    assert any(f["name"] == ".hidden" for f in data["files"])
    assert any(f["name"] == ".cache" for f in data["folders"])


@pytest.mark.asyncio
async def test_workspace_list_nonexistent_folder(client):
    resp = await client.get("/api/workspace?folder=does/not/exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_workspace_list_path_escape(client):
    resp = await client.get("/api/workspace?folder=../etc")
    assert resp.status_code == 404


# -- workspace_recent ----------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_recent_empty(client):
    resp = await client.get("/api/workspace/recent")
    assert resp.status_code == 200
    assert resp.json() == {"files": []}


@pytest.mark.asyncio
async def test_workspace_recent_sorted_by_mtime(client, http_config):
    workspace: Path = http_config.workspace_path
    old = workspace / "old.md"
    new = workspace / "new.md"
    old.write_text("o")
    new.write_text("n")
    os.utime(old, (1_700_000_000, 1_700_000_000))
    os.utime(new, (1_700_000_100, 1_700_000_100))

    resp = await client.get("/api/workspace/recent")
    assert resp.status_code == 200
    files = resp.json()["files"]
    names = [f["name"] for f in files]
    assert names.index("new.md") < names.index("old.md")


@pytest.mark.asyncio
async def test_workspace_recent_caps_at_50(client, http_config):
    workspace: Path = http_config.workspace_path
    for i in range(55):
        (workspace / f"f{i:02d}.txt").write_text(str(i))

    resp = await client.get("/api/workspace/recent")
    files = resp.json()["files"]
    assert len(files) == 50


@pytest.mark.asyncio
async def test_workspace_recent_flags_secret_and_readonly(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / ".env").write_text("X=1")
    (workspace / "embeddings.db").write_bytes(b"db")
    (workspace / "plain.md").write_text("plain")

    resp = await client.get("/api/workspace/recent")
    files = {f["name"]: f for f in resp.json()["files"]}
    assert files[".env"]["secret"] is True
    assert files["embeddings.db"]["readonly"] is True
    assert files["plain.md"]["secret"] is False
    assert files["plain.md"]["readonly"] is False


@pytest.mark.asyncio
async def test_workspace_recent_excludes_folders(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "subdir").mkdir()
    (workspace / "subdir" / "inside.md").write_text("hello")

    resp = await client.get("/api/workspace/recent")
    files = resp.json()["files"]
    # Only the file, not the directory
    assert all(not f["path"].endswith("subdir") for f in files)
    assert any(f["path"] == "subdir/inside.md" for f in files)


@pytest.mark.asyncio
async def test_workspace_recent_includes_dotfiles(client, http_config):
    """Backend returns all entries — frontend decides what to hide."""
    workspace: Path = http_config.workspace_path
    (workspace / ".hidden").write_text("h")

    resp = await client.get("/api/workspace/recent")
    names = [f["name"] for f in resp.json()["files"]]
    assert ".hidden" in names


# -- serve_workspace_file (raw bytes GET) --------------------------------------


@pytest.mark.asyncio
async def test_serve_workspace_file_secret_path_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / ".env").write_text("SECRET=shh")

    resp = await client.get("/api/workspace/.env")
    assert resp.status_code == 403
    assert resp.json() == {"error": "secret path"}


@pytest.mark.asyncio
async def test_serve_workspace_file_non_secret_still_serves_bytes(client, http_config):
    workspace: Path = http_config.workspace_path
    target = workspace / "note.md"
    target.write_text("hello world")

    resp = await client.get("/api/workspace/note.md")
    assert resp.status_code == 200
    assert resp.content == b"hello world"


# -- workspace_read_json (GET /api/workspace-file/{path}) ---------------------


@pytest.mark.asyncio
async def test_workspace_read_json_text_file(client, http_config):
    workspace: Path = http_config.workspace_path
    target = workspace / "note.md"
    target.write_text("hello editor")

    resp = await client.get("/api/workspace-file/note.md")
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "hello editor"
    assert isinstance(data["modified"], float)
    assert data["readonly"] is False


@pytest.mark.asyncio
async def test_workspace_read_json_readonly_path(client, http_config):
    workspace: Path = http_config.workspace_path
    conv_dir = workspace / "conversations"
    conv_dir.mkdir()
    target = conv_dir / "abc123.jsonl"
    target.write_text('{"role":"user"}\n')

    resp = await client.get("/api/workspace-file/conversations/abc123.jsonl")
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == '{"role":"user"}\n'
    assert data["readonly"] is True


@pytest.mark.asyncio
async def test_workspace_read_json_non_text_kind_returns_415(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    resp = await client.get("/api/workspace-file/pic.png")
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_workspace_read_json_secret_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / ".env").write_text("SECRET=shh")

    resp = await client.get("/api/workspace-file/.env")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_workspace_read_json_missing_returns_404(client, http_config):
    resp = await client.get("/api/workspace-file/missing.md")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_workspace_read_json_path_escape_returns_404(client, http_config):
    resp = await client.get("/api/workspace-file/../etc/passwd")
    assert resp.status_code == 404


# -- workspace_write (PUT /api/workspace/{path}) ------------------------------


@pytest.mark.asyncio
async def test_workspace_write_creates_new_file_with_parent_dirs(client, http_config):
    workspace: Path = http_config.workspace_path

    resp = await client.put(
        "/api/workspace/notes/sub/new.md",
        json={"content": "fresh"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["modified"], float)
    target = workspace / "notes" / "sub" / "new.md"
    assert target.is_file()
    assert target.read_text() == "fresh"


@pytest.mark.asyncio
async def test_workspace_write_overwrite_with_correct_mtime(client, http_config):
    workspace: Path = http_config.workspace_path
    target = workspace / "note.md"
    target.write_text("initial")
    old_mtime = target.stat().st_mtime

    resp = await client.put(
        "/api/workspace/note.md",
        json={"content": "updated", "modified": old_mtime},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert target.read_text() == "updated"
    assert data["modified"] >= old_mtime


@pytest.mark.asyncio
async def test_workspace_write_stale_mtime_returns_409(client, http_config):
    workspace: Path = http_config.workspace_path
    target = workspace / "note.md"
    target.write_text("initial")
    # Bump file mtime forward so any recent "modified" stamp is stale
    now = target.stat().st_mtime
    os.utime(target, (now + 100, now + 100))
    current = target.stat().st_mtime

    resp = await client.put(
        "/api/workspace/note.md",
        json={"content": "hacked", "modified": now - 50},
    )
    assert resp.status_code == 409
    data = resp.json()
    assert data["error"] == "conflict"
    assert data["modified"] == pytest.approx(current)
    # File content unchanged
    assert target.read_text() == "initial"


@pytest.mark.asyncio
async def test_workspace_write_readonly_path_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path
    conv_dir = workspace / "conversations"
    conv_dir.mkdir()
    target = conv_dir / "abc.jsonl"
    target.write_text("{}\n")

    resp = await client.put(
        "/api/workspace/conversations/abc.jsonl",
        json={"content": "nope"},
    )
    assert resp.status_code == 403
    assert target.read_text() == "{}\n"


@pytest.mark.asyncio
async def test_workspace_write_secret_path_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / ".env").write_text("SECRET=shh")

    resp = await client.put(
        "/api/workspace/.env",
        json={"content": "SECRET=leak"},
    )
    assert resp.status_code == 403
    assert (workspace / ".env").read_text() == "SECRET=shh"


@pytest.mark.asyncio
async def test_workspace_write_binary_kind_returns_415(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "pic.png").write_bytes(b"\x89PNG")

    resp = await client.put(
        "/api/workspace/pic.png",
        json={"content": "not really a png"},
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_workspace_write_path_escape_returns_404(client, http_config):
    resp = await client.put(
        "/api/workspace/../etc/passwd",
        json={"content": "pwn"},
    )
    assert resp.status_code == 404


# -- workspace_delete (DELETE /api/workspace/{path}) --------------------------


@pytest.mark.asyncio
async def test_workspace_delete_success_prunes_empty_parent(client, http_config):
    workspace: Path = http_config.workspace_path
    sub = workspace / "notes" / "sub"
    sub.mkdir(parents=True)
    target = sub / "x.md"
    target.write_text("x")

    resp = await client.delete("/api/workspace/notes/sub/x.md")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert not target.exists()
    assert not sub.exists()
    assert not (workspace / "notes").exists()


@pytest.mark.asyncio
async def test_workspace_delete_keeps_parent_with_siblings(client, http_config):
    workspace: Path = http_config.workspace_path
    sub = workspace / "notes"
    sub.mkdir()
    (sub / "keep.md").write_text("keep")
    (sub / "gone.md").write_text("gone")

    resp = await client.delete("/api/workspace/notes/gone.md")
    assert resp.status_code == 200
    assert not (sub / "gone.md").exists()
    assert (sub / "keep.md").exists()
    assert sub.is_dir()


@pytest.mark.asyncio
async def test_workspace_delete_readonly_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path
    conv_dir = workspace / "conversations"
    conv_dir.mkdir()
    target = conv_dir / "abc.jsonl"
    target.write_text("{}\n")

    resp = await client.delete("/api/workspace/conversations/abc.jsonl")
    assert resp.status_code == 403
    assert target.exists()


@pytest.mark.asyncio
async def test_workspace_delete_secret_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / ".env").write_text("X=1")

    resp = await client.delete("/api/workspace/.env")
    assert resp.status_code == 403
    assert (workspace / ".env").exists()


@pytest.mark.asyncio
async def test_workspace_delete_missing_returns_404(client, http_config):
    resp = await client.delete("/api/workspace/not-here.md")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_workspace_delete_path_escape_returns_404(client, http_config):
    resp = await client.delete("/api/workspace/../etc/passwd")
    assert resp.status_code == 404


# -- workspace rename (PUT /api/workspace/{path}?rename_to=...) --------------


@pytest.mark.asyncio
async def test_workspace_rename_within_same_folder(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "notes").mkdir()
    src = workspace / "notes" / "old.md"
    src.write_text("hello")

    resp = await client.put(
        "/api/workspace/notes/old.md?rename_to=notes/new.md",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["path"] == "notes/new.md"
    assert isinstance(data["modified"], float)
    assert not src.exists()
    assert (workspace / "notes" / "new.md").read_text() == "hello"


@pytest.mark.asyncio
async def test_workspace_rename_to_different_folder_autocreates(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "notes").mkdir()
    src = workspace / "notes" / "old.md"
    src.write_text("body")

    resp = await client.put(
        "/api/workspace/notes/old.md?rename_to=archive/2026/old.md",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "archive/2026/old.md"
    assert (workspace / "archive" / "2026" / "old.md").read_text() == "body"
    assert not src.exists()
    # Original empty parent pruned
    assert not (workspace / "notes").exists()


@pytest.mark.asyncio
async def test_workspace_rename_onto_existing_returns_409(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "a.md").write_text("a")
    (workspace / "b.md").write_text("b")

    resp = await client.put(
        "/api/workspace/a.md?rename_to=b.md",
    )
    assert resp.status_code == 409
    assert (workspace / "a.md").read_text() == "a"
    assert (workspace / "b.md").read_text() == "b"


@pytest.mark.asyncio
async def test_workspace_rename_secret_source_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / ".env").write_text("SECRET=1")

    resp = await client.put(
        "/api/workspace/.env?rename_to=notsecret.txt",
    )
    assert resp.status_code == 403
    assert (workspace / ".env").exists()


@pytest.mark.asyncio
async def test_workspace_rename_secret_dest_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "plain.txt").write_text("hi")

    resp = await client.put(
        "/api/workspace/plain.txt?rename_to=leaked.env",
    )
    assert resp.status_code == 403
    assert (workspace / "plain.txt").exists()
    assert not (workspace / "leaked.env").exists()


@pytest.mark.asyncio
async def test_workspace_rename_readonly_source_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "conversations").mkdir()
    src = workspace / "conversations" / "abc.jsonl"
    src.write_text("{}\n")

    resp = await client.put(
        "/api/workspace/conversations/abc.jsonl?rename_to=conversations/xyz.txt",
    )
    assert resp.status_code == 403
    assert src.exists()


@pytest.mark.asyncio
async def test_workspace_rename_readonly_dest_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "plain.md").write_text("hi")

    resp = await client.put(
        "/api/workspace/plain.md?rename_to=embeddings.db",
    )
    assert resp.status_code == 403
    assert (workspace / "plain.md").exists()
    assert not (workspace / "embeddings.db").exists()


@pytest.mark.asyncio
async def test_workspace_rename_missing_source_returns_404(client, http_config):
    resp = await client.put(
        "/api/workspace/nope.md?rename_to=also-nope.md",
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_workspace_rename_path_escape_dest_returns_404(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "a.md").write_text("a")

    resp = await client.put(
        "/api/workspace/a.md?rename_to=../etc/passwd",
    )
    assert resp.status_code == 404
    assert (workspace / "a.md").exists()


@pytest.mark.asyncio
async def test_workspace_rename_path_escape_source_returns_404(client, http_config):
    resp = await client.put(
        "/api/workspace/../etc/passwd?rename_to=safe.md",
    )
    assert resp.status_code == 404


# -- workspace_delete folder variants -----------------------------------------


@pytest.mark.asyncio
async def test_workspace_delete_empty_folder(client, http_config):
    workspace: Path = http_config.workspace_path
    sub = workspace / "projects" / "alpha"
    sub.mkdir(parents=True)

    resp = await client.delete("/api/workspace/projects/alpha")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert not sub.exists()
    # Empty parent pruned too
    assert not (workspace / "projects").exists()


@pytest.mark.asyncio
async def test_workspace_delete_non_empty_folder_returns_409(client, http_config):
    workspace: Path = http_config.workspace_path
    sub = workspace / "projects"
    sub.mkdir()
    (sub / "keep.md").write_text("keep")

    resp = await client.delete("/api/workspace/projects")
    assert resp.status_code == 409
    data = resp.json()
    assert "not empty" in data.get("error", "").lower()
    assert sub.is_dir()
    assert (sub / "keep.md").exists()


@pytest.mark.asyncio
async def test_workspace_delete_folder_under_readonly_subtree_returns_403(
    client, http_config
):
    workspace: Path = http_config.workspace_path
    sub = workspace / ".schedule_last_run" / "leftover"
    sub.mkdir(parents=True)

    resp = await client.delete("/api/workspace/.schedule_last_run/leftover")
    assert resp.status_code == 403
    assert sub.exists()


@pytest.mark.asyncio
async def test_workspace_delete_secret_named_folder_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path
    sub = workspace / "credentials"
    sub.mkdir()

    resp = await client.delete("/api/workspace/credentials")
    assert resp.status_code == 403
    assert sub.is_dir()


@pytest.mark.asyncio
async def test_workspace_delete_folder_missing_returns_404(client, http_config):
    resp = await client.delete("/api/workspace/no-such-thing")
    assert resp.status_code == 404


# -- workspace_create (POST /api/workspace) -----------------------------------


@pytest.mark.asyncio
async def test_workspace_create_file_with_content(client, http_config):
    workspace: Path = http_config.workspace_path

    resp = await client.post(
        "/api/workspace",
        json={"type": "file", "path": "notes/new.md", "content": "hello"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["path"] == "notes/new.md"
    assert isinstance(data["modified"], float)
    target = workspace / "notes" / "new.md"
    assert target.is_file()
    assert target.read_text() == "hello"


@pytest.mark.asyncio
async def test_workspace_create_file_without_content_is_empty(client, http_config):
    workspace: Path = http_config.workspace_path

    resp = await client.post(
        "/api/workspace",
        json={"type": "file", "path": "blank.md"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    target = workspace / "blank.md"
    assert target.is_file()
    assert target.read_text() == ""


@pytest.mark.asyncio
async def test_workspace_create_file_conflict_when_exists(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "existing.md").write_text("keep me")

    resp = await client.post(
        "/api/workspace",
        json={"type": "file", "path": "existing.md", "content": "overwrite"},
    )
    assert resp.status_code == 409
    assert (workspace / "existing.md").read_text() == "keep me"


@pytest.mark.asyncio
async def test_workspace_create_folder(client, http_config):
    workspace: Path = http_config.workspace_path

    resp = await client.post(
        "/api/workspace",
        json={"type": "folder", "path": "projects/alpha"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["path"] == "projects/alpha"
    assert (workspace / "projects" / "alpha").is_dir()


@pytest.mark.asyncio
async def test_workspace_create_folder_conflict_when_exists(client, http_config):
    workspace: Path = http_config.workspace_path
    (workspace / "dup").mkdir()

    resp = await client.post(
        "/api/workspace",
        json={"type": "folder", "path": "dup"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_workspace_create_file_secret_path_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path

    resp = await client.post(
        "/api/workspace",
        json={"type": "file", "path": ".env", "content": "SECRET=1"},
    )
    assert resp.status_code == 403
    assert not (workspace / ".env").exists()


@pytest.mark.asyncio
async def test_workspace_create_file_readonly_path_returns_403(client, http_config):
    workspace: Path = http_config.workspace_path

    resp = await client.post(
        "/api/workspace",
        json={
            "type": "file",
            "path": "conversations/abc.jsonl",
            "content": "{}\n",
        },
    )
    assert resp.status_code == 403
    assert not (workspace / "conversations" / "abc.jsonl").exists()


@pytest.mark.asyncio
async def test_workspace_create_folder_under_readonly_subtree_returns_403(
    client, http_config
):
    workspace: Path = http_config.workspace_path

    resp = await client.post(
        "/api/workspace",
        json={"type": "folder", "path": ".schedule_last_run/newdir"},
    )
    assert resp.status_code == 403
    assert not (workspace / ".schedule_last_run" / "newdir").exists()


@pytest.mark.asyncio
async def test_workspace_create_file_path_escape_returns_404(client, http_config):
    resp = await client.post(
        "/api/workspace",
        json={"type": "file", "path": "../etc/passwd", "content": "pwn"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_workspace_create_folder_path_escape_returns_404(client, http_config):
    resp = await client.post(
        "/api/workspace",
        json={"type": "folder", "path": "../etc/evil"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_workspace_create_invalid_type_returns_400(client, http_config):
    resp = await client.post(
        "/api/workspace",
        json={"type": "wrong", "path": "whatever.md"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_workspace_create_missing_path_returns_400(client, http_config):
    resp = await client.post(
        "/api/workspace",
        json={"type": "file"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_workspace_create_missing_type_returns_400(client, http_config):
    resp = await client.post(
        "/api/workspace",
        json={"path": "foo.md"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_workspace_create_malformed_json_returns_400(client, http_config):
    resp = await client.post(
        "/api/workspace",
        content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_workspace_recent_prunes_heavy_subtrees(client, http_config):
    """conversations/, .schedule_last_run/, attachments/ are skipped entirely."""
    workspace: Path = http_config.workspace_path
    # Files inside pruned subtrees should NOT appear
    (workspace / "conversations").mkdir()
    (workspace / "conversations" / "abc.jsonl").write_text("{}\n")
    (workspace / "attachments").mkdir()
    (workspace / "attachments" / "blob.bin").write_bytes(b"x")
    (workspace / ".schedule_last_run").mkdir()
    (workspace / ".schedule_last_run" / "task.txt").write_text("1")
    # Pruning should also apply to nested occurrences
    (workspace / "nested").mkdir()
    (workspace / "nested" / "attachments").mkdir()
    (workspace / "nested" / "attachments" / "ignored.bin").write_bytes(b"y")
    # A file that SHOULD appear, to make sure the endpoint is live
    (workspace / "visible.md").write_text("visible")

    resp = await client.get("/api/workspace/recent")
    assert resp.status_code == 200
    paths = {f["path"] for f in resp.json()["files"]}
    assert "visible.md" in paths
    assert "conversations/abc.jsonl" not in paths
    assert "attachments/blob.bin" not in paths
    assert ".schedule_last_run/task.txt" not in paths
    assert "nested/attachments/ignored.bin" not in paths


