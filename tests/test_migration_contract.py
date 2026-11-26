"""Shared contract for the two conversation migrations (#1912).

Each migration keeps its own layout-specific tests; these cover dry-run,
idempotence, pre-existing targets, and missing/empty roots.
"""

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _REPO / "scripts" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SESSIONS = _load("migrate_sessions", "migrate_sessions.py")
_SIDECARS = _load("migrate_sidecars_to_dirs", "migrate_sidecars_to_dirs.py")


@dataclass(frozen=True)
class MigrationCase:
    name: str
    migrate: Callable
    seed: Callable
    dry_run_count: int
    move_count: int
    assert_untouched: Callable
    assert_moved: Callable
    seed_preexisting: Callable
    assert_preexisting: Callable


def _seed_sessions(base):
    root = base / "conversations"
    root.mkdir()
    conv = root / "abc"
    conv.mkdir()
    (conv / "archive.jsonl").write_text("archive-body")


def _assert_sessions_untouched(base):
    source = base / "conversations" / "abc" / "archive.jsonl"
    assert source.read_text() == "archive-body"
    assert not (base / "sessions" / "abc").exists()


def _assert_sessions_moved(base):
    assert (base / "sessions" / "abc" / "archive.jsonl").read_text() == "archive-body"
    assert not (base / "conversations" / "abc").exists()


def _seed_sessions_preexisting(base):
    source = base / "conversations" / "abc"
    source.mkdir(parents=True)
    (source / "archive.jsonl").write_text("stale-source")
    target = base / "sessions" / "abc"
    target.mkdir(parents=True)
    (target / "archive.jsonl").write_text("migrated-target")


def _assert_sessions_preexisting(base):
    assert (base / "sessions" / "abc" / "archive.jsonl").read_text() == "migrated-target"
    assert (base / "conversations" / "abc" / "archive.jsonl").read_text() == "stale-source"


def _seed_sidecars(base):
    root = base / "conversations"
    root.mkdir()
    (root / "abc.jsonl").write_text("archive-body")


def _assert_sidecars_untouched(base):
    root = base / "conversations"
    assert (root / "abc.jsonl").read_text() == "archive-body"
    assert not (root / "abc").exists()


def _assert_sidecars_moved(base):
    root = base / "conversations"
    assert (root / "abc" / "archive.jsonl").read_text() == "archive-body"
    assert not (root / "abc.jsonl").exists()


def _seed_sidecars_preexisting(base):
    root = base / "conversations"
    target = root / "abc"
    target.mkdir(parents=True)
    (target / "archive.jsonl").write_text("existing-target")
    (root / "abc.jsonl").write_text("stale-flat")


def _assert_sidecars_preexisting(base):
    root = base / "conversations"
    assert (root / "abc" / "archive.jsonl").read_text() == "existing-target"
    assert (root / "abc.jsonl").read_text() == "stale-flat"


CASES = [
    MigrationCase(
        name="sessions",
        migrate=lambda root, dry_run: _SESSIONS.migrate_sessions(root, dry_run=dry_run),
        seed=_seed_sessions,
        dry_run_count=1,
        move_count=1,
        assert_untouched=_assert_sessions_untouched,
        assert_moved=_assert_sessions_moved,
        seed_preexisting=_seed_sessions_preexisting,
        assert_preexisting=_assert_sessions_preexisting,
    ),
    MigrationCase(
        name="sidecars",
        migrate=lambda root, dry_run: _SIDECARS.migrate_sidecars(root, dry_run=dry_run),
        seed=_seed_sidecars,
        dry_run_count=1,
        move_count=1,
        assert_untouched=_assert_sidecars_untouched,
        assert_moved=_assert_sidecars_moved,
        seed_preexisting=_seed_sidecars_preexisting,
        assert_preexisting=_assert_sidecars_preexisting,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_dry_run_moves_nothing(case, tmp_path):
    case.seed(tmp_path)
    assert case.migrate(tmp_path / "conversations", dry_run=True) == case.dry_run_count
    case.assert_untouched(tmp_path)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_idempotent_second_run_moves_nothing(case, tmp_path):
    case.seed(tmp_path)
    assert case.migrate(tmp_path / "conversations", dry_run=False) == case.move_count
    case.assert_moved(tmp_path)
    assert case.migrate(tmp_path / "conversations", dry_run=False) == 0


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_preexisting_target_skipped_not_overwritten(case, tmp_path):
    case.seed_preexisting(tmp_path)
    assert case.migrate(tmp_path / "conversations", dry_run=False) == 0
    case.assert_preexisting(tmp_path)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_missing_dir_returns_zero(case, tmp_path):
    assert case.migrate(tmp_path / "conversations", dry_run=False) == 0


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_empty_dir_returns_zero(case, tmp_path):
    (tmp_path / "conversations").mkdir()
    assert case.migrate(tmp_path / "conversations", dry_run=False) == 0
