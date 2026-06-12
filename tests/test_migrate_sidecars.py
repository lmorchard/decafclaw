"""Tests for the flat-sidecar → per-conversation-dir migration script.

scripts/ is not a package, so we load the module by path via
importlib.util.spec_from_file_location (mirroring tests/test_message_types.py)
and exercise the config-free core function migrate_sidecars directly against
a tmp_path conversations dir."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts/migrate_sidecars_to_dirs.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("migrate_sidecars_to_dirs", _SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()
migrate_sidecars = _MODULE.migrate_sidecars


def test_basic_sidecars_moved(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    (conv / "abc.jsonl").write_text("archive-body")
    (conv / "abc.notes.md").write_text("notes-body")
    (conv / "abc.context.json").write_text("{\"ctx\": 1}")

    moved = migrate_sidecars(conv, dry_run=False)

    assert moved == 3
    assert (conv / "abc" / "archive.jsonl").read_text() == "archive-body"
    assert (conv / "abc" / "notes.md").read_text() == "notes-body"
    assert (conv / "abc" / "context.json").read_text() == "{\"ctx\": 1}"
    # Originals gone.
    assert not (conv / "abc.jsonl").exists()
    assert not (conv / "abc.notes.md").exists()
    assert not (conv / "abc.context.json").exists()


def test_compacted_ordering(tmp_path):
    """A conv with BOTH {id}.jsonl and {id}.compacted.jsonl must land both
    correctly: .compacted.jsonl → compacted.jsonl (conv_id 'abc', not
    'abc.compacted'), .jsonl → archive.jsonl."""
    conv = tmp_path / "conversations"
    conv.mkdir()
    (conv / "abc.jsonl").write_text("archive-body")
    (conv / "abc.compacted.jsonl").write_text("compacted-body")

    moved = migrate_sidecars(conv, dry_run=False)

    assert moved == 2
    assert (conv / "abc" / "compacted.jsonl").read_text() == "compacted-body"
    assert (conv / "abc" / "archive.jsonl").read_text() == "archive-body"
    # No mis-mapped target: conv_id is 'abc', never 'abc.compacted'.
    assert not (conv / "abc.compacted").exists()
    assert not (conv / "abc.compacted.jsonl").exists()
    assert not (conv / "abc.jsonl").exists()


def test_idempotent_second_run_moves_nothing(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    (conv / "abc.jsonl").write_text("archive-body")

    first = migrate_sidecars(conv, dry_run=False)
    second = migrate_sidecars(conv, dry_run=False)

    assert first == 1
    assert second == 0
    assert (conv / "abc" / "archive.jsonl").read_text() == "archive-body"


def test_preexisting_target_skipped_not_overwritten(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    # Seed the target dir with DIFFERENT content; a stale flat file must not
    # clobber it.
    (conv / "abc").mkdir()
    (conv / "abc" / "archive.jsonl").write_text("existing-target")
    (conv / "abc.jsonl").write_text("stale-flat")

    moved = migrate_sidecars(conv, dry_run=False)

    assert moved == 0
    # Target untouched, flat file left in place.
    assert (conv / "abc" / "archive.jsonl").read_text() == "existing-target"
    assert (conv / "abc.jsonl").read_text() == "stale-flat"


def test_dry_run_moves_nothing(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    (conv / "abc.jsonl").write_text("archive-body")
    (conv / "abc.notes.md").write_text("notes-body")

    moved = migrate_sidecars(conv, dry_run=True)

    assert moved == 2
    # Flat files still present, no dir created.
    assert (conv / "abc.jsonl").exists()
    assert (conv / "abc.notes.md").exists()
    assert not (conv / "abc").exists()


def test_non_sidecars_and_existing_dirs_untouched(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    (conv / "README.txt").write_text("readme")
    (conv / "server.log").write_text("log")
    # An existing conversation dir with non-sidecar contents.
    existing = conv / "xyz"
    existing.mkdir()
    (existing / "workflow.json").write_text("wf")
    (existing / "uploads").mkdir()
    (existing / "uploads" / "x").write_text("upload")
    # One real flat sidecar to confirm migration still runs.
    (conv / "abc.jsonl").write_text("archive-body")

    moved = migrate_sidecars(conv, dry_run=False)

    assert moved == 1
    # Non-sidecar files untouched.
    assert (conv / "README.txt").read_text() == "readme"
    assert (conv / "server.log").read_text() == "log"
    # Existing dir contents untouched.
    assert (existing / "workflow.json").read_text() == "wf"
    assert (existing / "uploads" / "x").read_text() == "upload"
    # Real sidecar moved.
    assert (conv / "abc" / "archive.jsonl").read_text() == "archive-body"


def test_missing_dir_returns_zero(tmp_path):
    missing = tmp_path / "conversations"  # never created
    assert migrate_sidecars(missing, dry_run=False) == 0


def test_empty_dir_returns_zero(tmp_path):
    conv = tmp_path / "conversations"
    conv.mkdir()
    assert migrate_sidecars(conv, dry_run=False) == 0
