"""Tests for the session-scoped conversation migration script (#1112).

scripts/ is not a package, so we load the module by path via
importlib.util.spec_from_file_location (mirroring tests/test_message_types.py
and tests/test_migrate_sidecars.py) and exercise the config-free core
function migrate_sessions directly against a tmp_path conversations dir.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts/migrate_sessions.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("migrate_sessions", _SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()
migrate_sessions = _MODULE.migrate_sessions


def test_standalone_conv_moved_into_sessions_root(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    conv_dir = conv / "abc"
    conv_dir.mkdir()
    (conv_dir / "archive.jsonl").write_text("archive-body")

    moved = migrate_sessions(conv, dry_run=False)

    assert moved == 1
    assert (tmp_path / "sessions" / "abc" / "archive.jsonl").read_text() == "archive-body"
    assert not conv_dir.exists()


def test_child_conv_moved_under_parent_session(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    parent = "parent-abc"
    child = f"{parent}--child-xyz"
    child_dir = conv / child
    child_dir.mkdir()
    (child_dir / "archive.jsonl").write_text("child-body")

    moved = migrate_sessions(conv, dry_run=False)

    assert moved == 1
    assert (tmp_path / "sessions" / parent / child / "archive.jsonl").read_text() == "child-body"
    assert not child_dir.exists()
    assert not (conv / child).exists()


def test_idempotent_second_run_moves_nothing(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    conv_dir = conv / "abc"
    conv_dir.mkdir()
    (conv_dir / "archive.jsonl").write_text("archive-body")

    first = migrate_sessions(conv, dry_run=False)
    second = migrate_sessions(conv, dry_run=False)

    assert first == 1
    assert second == 0
    assert (tmp_path / "sessions" / "abc" / "archive.jsonl").read_text() == "archive-body"


def test_preexisting_target_skipped_not_overwritten(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    # A stale conversations dir must not clobber an already-migrated session dir.
    stale = conv / "abc"
    stale.mkdir()
    (stale / "archive.jsonl").write_text("stale-source")
    migrated = tmp_path / "sessions" / "abc"
    migrated.mkdir(parents=True)
    (migrated / "archive.jsonl").write_text("migrated-target")

    moved = migrate_sessions(conv, dry_run=False)

    assert moved == 0
    # Target untouched, source untouched.
    assert (migrated / "archive.jsonl").read_text() == "migrated-target"
    assert (stale / "archive.jsonl").read_text() == "stale-source"


def test_dry_run_moves_nothing(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    conv_dir = conv / "abc"
    conv_dir.mkdir()
    (conv_dir / "archive.jsonl").write_text("archive-body")

    moved = migrate_sessions(conv, dry_run=True)

    assert moved == 1
    # Source dir still in place, nothing created under sessions/.
    assert conv_dir.exists()
    assert (conv_dir / "archive.jsonl").read_text() == "archive-body"
    assert not (tmp_path / "sessions" / "abc").exists()


def test_missing_dir_returns_zero(tmp_path):
    missing = tmp_path / "conversations"  # never created
    assert migrate_sessions(missing, dry_run=False) == 0


def test_empty_dir_returns_zero(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    assert migrate_sessions(conv, dry_run=False) == 0


def test_stray_files_untouched(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    (conv / "README.txt").write_text("readme")

    moved = migrate_sessions(conv, dry_run=False)

    assert moved == 0
    assert (conv / "README.txt").read_text() == "readme"
