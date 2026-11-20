"""Tests for web/workspace_paths.py permission + kind helpers."""

import pytest

from decafclaw.web.workspace_paths import (
    PathContainmentError,
    detect_kind,
    is_readonly,
    is_secret,
    resolve_contained,
)


def test_resolve_contained_allows_paths_under_root(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.txt").write_text("x")
    assert resolve_contained(tmp_path, "sub/f.txt", label="workspace") == (tmp_path / "sub" / "f.txt").resolve()


def test_resolve_contained_allows_root_itself(tmp_path):
    assert resolve_contained(tmp_path, "", label="workspace") == tmp_path.resolve()


def test_resolve_contained_blocks_parent_escape(tmp_path):
    with pytest.raises(PathContainmentError, match="outside workspace"):
        resolve_contained(tmp_path, "../etc/passwd", label="workspace")


def test_resolve_contained_blocks_absolute(tmp_path):
    with pytest.raises(PathContainmentError, match="outside workspace"):
        resolve_contained(tmp_path, "/etc/passwd", label="workspace")


def test_resolve_contained_blocks_dotdot_traversal(tmp_path):
    (tmp_path / "notes").mkdir()
    with pytest.raises(PathContainmentError):
        resolve_contained(tmp_path, "notes/../../etc/passwd", label="workspace")


def test_resolve_contained_blocks_symlink_escape(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    evil = tmp_path / "elsewhere"
    evil.mkdir()
    (evil / "secret.txt").write_text("shh")
    (root / "link").symlink_to(evil, target_is_directory=True)
    with pytest.raises(PathContainmentError):
        resolve_contained(root, "link/secret.txt", label="workspace")


def test_resolve_contained_blocks_sibling_prefix(tmp_path):
    """A sibling directory sharing the root's name prefix must not resolve.

    String-prefix containment (``str.startswith``) accepts ``workspace-evil``
    when the root is ``workspace``; ``relative_to`` does not (#1293).
    """
    root = tmp_path / "workspace"
    root.mkdir()
    evil = tmp_path / "workspace-evil"
    evil.mkdir()
    (evil / "secret.txt").write_text("shh")
    with pytest.raises(PathContainmentError):
        resolve_contained(root, "../workspace-evil/secret.txt", label="workspace")


def test_resolve_contained_error_names_label_and_path(tmp_path):
    with pytest.raises(PathContainmentError) as excinfo:
        resolve_contained(tmp_path, "../outside.txt", label="the workspace")
    assert str(excinfo.value) == "path '../outside.txt' is outside the workspace"


def test_is_secret_env_file():
    assert is_secret("config/.env") is True


def test_is_secret_credentials_in_name():
    assert is_secret("some_credentials.json") is True


def test_is_secret_key_file():
    assert is_secret("ssh/id_rsa.key") is True


def test_is_secret_regular_file():
    assert is_secret("notes/draft.md") is False


def test_is_readonly_jsonl_archive():
    assert is_readonly("conversations/abc123.jsonl") is True


def test_is_readonly_db_file():
    assert is_readonly("embeddings.db") is True
    assert is_readonly("foo/bar.db-wal") is True


def test_is_readonly_schedule_state():
    assert is_readonly(".schedule_last_run/task.txt") is True


def test_is_readonly_regular_file():
    assert is_readonly("skills/foo/SKILL.md") is False


def test_detect_kind_text_extension(tmp_path):
    p = tmp_path / "x.py"
    p.write_bytes(b"\x00garbage")  # extension wins over sniff
    assert detect_kind(p) == "text"


def test_detect_kind_image_extension(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(b"PNG")
    assert detect_kind(p) == "image"


def test_detect_kind_unknown_text_sniff(tmp_path):
    p = tmp_path / "README"
    p.write_text("hello, world")
    assert detect_kind(p) == "text"


def test_detect_kind_unknown_binary_sniff(tmp_path):
    p = tmp_path / "blob"
    p.write_bytes(b"abc\x00def")
    assert detect_kind(p) == "binary"
