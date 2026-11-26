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


def test_stray_files_untouched(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    (conv / "README.txt").write_text("readme")

    moved = migrate_sessions(conv, dry_run=False)

    assert moved == 0
    assert (conv / "README.txt").read_text() == "readme"
