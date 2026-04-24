"""Tests for web/workspace_paths.py permission + kind helpers."""

from decafclaw.web.workspace_paths import (
    detect_kind,
    is_readonly,
    is_secret,
    resolve_safe,
)


def test_resolve_safe_allows_paths_under_root(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.txt").write_text("x")
    assert resolve_safe(tmp_path, "sub/f.txt") == (tmp_path / "sub" / "f.txt").resolve()


def test_resolve_safe_blocks_parent_escape(tmp_path):
    assert resolve_safe(tmp_path, "../etc/passwd") is None


def test_resolve_safe_blocks_absolute(tmp_path):
    assert resolve_safe(tmp_path, "/etc/passwd") is None


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
