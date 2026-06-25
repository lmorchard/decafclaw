from pathlib import Path
from types import SimpleNamespace

from decafclaw.conversation_paths import (
    conversation_dir,
    conversations_root,
    delete_conversation_files,
    iter_conversation_archives,
    sidecar_path,
)


def _cfg(tmp_path):
    return SimpleNamespace(workspace_path=tmp_path)


# --- conversation_dir -------------------------------------------------------


def test_conversation_dir_returns_id_subdir(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    assert conversation_dir(cfg, "abc") == root / "abc"


def test_conversation_dir_create_makes_dir(tmp_path):
    cfg = _cfg(tmp_path)
    d = conversation_dir(cfg, "abc", create=True)
    assert d.is_dir()
    assert d == conversations_root(cfg) / "abc"


def test_conversation_dir_without_create_does_not_make_dir(tmp_path):
    cfg = _cfg(tmp_path)
    d = conversation_dir(cfg, "abc")
    assert not d.exists()


# --- sandboxing -------------------------------------------------------------


def test_conversation_dir_traversal_stays_under_root(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    d = conversation_dir(cfg, "../../etc")
    assert d.resolve().is_relative_to(root)


def test_conversation_dir_empty_id_resolves_to_invalid(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    d = conversation_dir(cfg, "")
    assert d == root / "_invalid"


def test_conversation_dir_dotdot_only_resolves_to_invalid(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    d = conversation_dir(cfg, "..")
    assert d == root / "_invalid"


# --- sidecar_path -----------------------------------------------------------


def test_sidecar_path_returns_dir_path(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    p = sidecar_path(cfg, "abc", "notes.md")
    assert p == root / "abc" / "notes.md"


def test_sidecar_path_returns_dir_path_when_file_exists(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    convdir.mkdir(parents=True)
    new = convdir / "notes.md"
    new.write_text("hi")
    p = sidecar_path(cfg, "abc", "notes.md")
    assert p == new


# --- iter_conversation_archives --------------------------------------------


def test_iter_archives_empty_when_root_missing(tmp_path):
    cfg = _cfg(tmp_path)
    assert list(iter_conversation_archives(cfg)) == []


def test_iter_archives_fails_open_on_oserror(tmp_path, monkeypatch):
    # A transient FS error while listing must not propagate — this helper
    # feeds startup recovery, search, and UI listing.
    cfg = _cfg(tmp_path)
    conversations_root(cfg).mkdir(parents=True)

    def _boom(self):
        raise OSError("simulated listing failure")

    monkeypatch.setattr(Path, "iterdir", _boom)
    assert list(iter_conversation_archives(cfg)) == []


def test_iter_archives_yields_dir_layout(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    convdir.mkdir(parents=True)
    archive = convdir / "archive.jsonl"
    archive.write_text("{}")
    assert list(iter_conversation_archives(cfg)) == [("abc", archive)]


def test_iter_archives_skips_compacted(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    convdir.mkdir(parents=True)
    (convdir / "compacted.jsonl").write_text("{}")
    assert list(iter_conversation_archives(cfg)) == []


def test_iter_archives_skips_dir_without_archive(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    convdir.mkdir(parents=True)
    (convdir / "notes.md").write_text("hi")
    assert list(iter_conversation_archives(cfg)) == []


# --- delete_conversation_files ---------------------------------------------


def test_delete_removes_dir_with_nested_files(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    uploads = convdir / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "file").write_text("data")
    (convdir / "workflow.json").write_text("{}")
    (convdir / "archive.jsonl").write_text("{}")
    delete_conversation_files(cfg, "abc")
    assert not convdir.exists()


def test_delete_also_removes_leftover_flat_sidecars(tmp_path):
    # Defense for a partially-migrated / migration-skipped instance: delete
    # must still purge pre-#576 flat sidecars so "deleted" history doesn't
    # linger on disk, even though the runtime no longer reads them.
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    root.mkdir(parents=True)
    flat_archive = root / "abc.jsonl"
    flat_notes = root / "abc.notes.md"
    flat_archive.write_text("{}")
    flat_notes.write_text("- note")
    delete_conversation_files(cfg, "abc")
    assert not flat_archive.exists()
    assert not flat_notes.exists()


def test_delete_is_noop_when_nothing_exists(tmp_path):
    cfg = _cfg(tmp_path)
    # Should not raise.
    delete_conversation_files(cfg, "abc")
