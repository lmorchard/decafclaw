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


def test_sidecar_path_new_when_neither_exists(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    p = sidecar_path(cfg, "abc", "notes.md", ".notes.md")
    assert p == root / "abc" / "notes.md"


def test_sidecar_path_legacy_when_only_flat_exists(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    root.mkdir(parents=True)
    legacy = root / "abc.notes.md"
    legacy.write_text("hi")
    p = sidecar_path(cfg, "abc", "notes.md", ".notes.md")
    assert p == legacy


def test_sidecar_path_new_when_only_new_exists(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    convdir.mkdir(parents=True)
    new = convdir / "notes.md"
    new.write_text("hi")
    p = sidecar_path(cfg, "abc", "notes.md", ".notes.md")
    assert p == new


def test_sidecar_path_new_wins_when_both_exist(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    convdir.mkdir(parents=True)
    new = convdir / "notes.md"
    new.write_text("new")
    legacy = root / "abc.notes.md"
    legacy.write_text("legacy")
    p = sidecar_path(cfg, "abc", "notes.md", ".notes.md")
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


def test_iter_archives_yields_flat_layout(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    root.mkdir(parents=True)
    flat = root / "abc.jsonl"
    flat.write_text("{}")
    assert list(iter_conversation_archives(cfg)) == [("abc", flat)]


def test_iter_archives_dir_wins_when_both_layouts(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    convdir.mkdir(parents=True)
    dir_archive = convdir / "archive.jsonl"
    dir_archive.write_text("{}")
    flat = root / "abc.jsonl"
    flat.write_text("{}")
    results = list(iter_conversation_archives(cfg))
    assert results == [("abc", dir_archive)]


def test_iter_archives_skips_compacted(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    root.mkdir(parents=True)
    (root / "abc.compacted.jsonl").write_text("{}")
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


def test_delete_removes_flat_legacy_files(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    root.mkdir(parents=True)
    notes = root / "abc.notes.md"
    notes.write_text("hi")
    archive = root / "abc.jsonl"
    archive.write_text("{}")
    delete_conversation_files(cfg, "abc")
    assert not notes.exists()
    assert not archive.exists()


def test_delete_removes_both_dir_and_legacy(tmp_path):
    cfg = _cfg(tmp_path)
    root = conversations_root(cfg)
    convdir = root / "abc"
    convdir.mkdir(parents=True)
    (convdir / "archive.jsonl").write_text("{}")
    legacy = root / "abc.notes.md"
    legacy.write_text("hi")
    delete_conversation_files(cfg, "abc")
    assert not convdir.exists()
    assert not legacy.exists()


def test_delete_is_noop_when_nothing_exists(tmp_path):
    cfg = _cfg(tmp_path)
    # Should not raise.
    delete_conversation_files(cfg, "abc")
