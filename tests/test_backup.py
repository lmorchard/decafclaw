"""Tests for the scheduled data backup (#1646).

The backup scope is a contract: the include and exclude sets are asserted
exactly, the archive is checked for both, and the manifest, retention, idle
deferral, and lock behaviour are exercised against a temporary data home.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "backup.py"

_spec = importlib.util.spec_from_file_location("decafclaw_backup", _SCRIPT)
assert _spec and _spec.loader
backup = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = backup
_spec.loader.exec_module(backup)

FIXED = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _data_home(tmp_path: Path) -> Path:
    home = tmp_path / "data"
    for rel in backup.INCLUDE_PATHS:
        target = home / rel
        if rel.endswith(".json") or rel.endswith(".md"):
            _write(target)
        else:
            _write(target / "sample.txt")
    # Excluded artefacts, planted where an include directory could grow into them.
    _write(home / "workspace/embeddings.db", "sqlite")
    _write(home / "workspace/embeddings.db-wal", "wal")
    _write(home / "workspace/embeddings.db-shm", "shm")
    _write(home / "workspace/telemetry/retrieval.jsonl", "{}")
    _write(home / "workspace/audit.jsonl", "{}")
    _write(home / "workspace/tool_usage.jsonl", "{}")
    _write(home / "backups/old.tar.gz", "old")
    return home


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _write(repo / ".env", "SECRET=1\n")
    return repo


def _runner(tmp_path: Path, home: Path, repo: Path, *, health=None, clock=None, **overrides):
    values = {
        "data_home": home,
        "repo_dir": repo,
        "backup_dir": home / "backups",
        "retention": 5,
        "max_deferrals": 4,
        "health_url": "http://example.invalid/health",
        "force": False,
    }
    values.update(overrides)
    config = backup.BackupConfig(**values)
    return backup.BackupRunner(
        config,
        health=health or (lambda: {"busy": {"active": False}}),
        clock=clock or (lambda: FIXED),
    )


def test_scope_sets_are_exact():
    assert backup.INCLUDE_PATHS == (
        "workspace/sessions",
        "workspace/drafts",
        "workspace/todos",
        "workspace/notifications",
        "workspace/newsletter",
        "config.json",
        "USER.md",
        "CUSTOM.md",
        "skills",
        "approval_state",
        "schedules",
        "scheduled_wakes",
        "file_watches",
        "secrets",
        "web",
        "web_conversations.json",
        "workspace_index.json",
    )
    assert backup.EXCLUDE_PATHS == (
        "workspace/embeddings.db",
        "workspace/embeddings.db-wal",
        "workspace/embeddings.db-shm",
        "workspace/telemetry",
        "workspace/audit.jsonl",
        "workspace/tool_usage.jsonl",
        "backups",
    )


def test_archive_contains_includes_excludes_and_env(tmp_path):
    home = _data_home(tmp_path)
    repo = _repo(tmp_path)
    runner = _runner(tmp_path, home, repo)

    assert runner.run() == 0

    archives = sorted((home / "backups").glob("decafclaw-*.tar.gz"))
    assert len(archives) == 1
    archive = archives[0]
    assert archive.stat().st_mode & 0o777 == 0o600
    manifest = json.loads((archive.parent / archive.name.replace(".tar.gz", ".json")).read_text())
    assert manifest["status"] == "ok"
    assert manifest["file_count"] > 0
    assert manifest["size_bytes"] == archive.stat().st_size
    assert "workspace/sessions" in manifest["included"]
    assert "env" in manifest["included"]
    assert manifest["config_sha256"]

    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
        env = tar.getmember("env")
    assert env.mode == 0o600
    assert "workspace/sessions/sample.txt" in names
    assert "workspace/drafts/sample.txt" in names
    assert "config.json" in names
    assert "skills/sample.txt" in names
    assert "web_conversations.json" in names
    assert "workspace/embeddings.db" not in names
    assert "workspace/embeddings.db-wal" not in names
    assert "workspace/embeddings.db-shm" not in names
    assert "workspace/telemetry/retrieval.jsonl" not in names
    assert "workspace/audit.jsonl" not in names
    assert "workspace/tool_usage.jsonl" not in names
    assert "backups/old.tar.gz" not in names


def test_retention_keeps_newest_five_and_never_the_new_archive(tmp_path):
    home = _data_home(tmp_path)
    repo = _repo(tmp_path)
    backup_dir = home / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for index in range(7):
        path = backup_dir / f"decafclaw-2026010{index + 1}-000000.tar.gz"
        path.write_text("old")
        os.utime(path, (index + 1, index + 1))

    assert _runner(tmp_path, home, repo).run() == 0

    archives = sorted(backup_dir.glob("decafclaw-*.tar.gz"))
    assert len(archives) == 5
    new = backup_dir / "decafclaw-20261125-120000.tar.gz"
    assert new in archives
    assert all(path.stat().st_size > 3 for path in archives if path == new)


def test_retention_one_keeps_only_the_new_archive(tmp_path):
    home = _data_home(tmp_path)
    repo = _repo(tmp_path)
    backup_dir = home / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    old = backup_dir / "decafclaw-20260101-000000.tar.gz"
    old.write_text("old")

    assert _runner(tmp_path, home, repo, retention=1).run() == 0

    archives = list(backup_dir.glob("decafclaw-*.tar.gz"))
    assert [p.name for p in archives] == ["decafclaw-20261125-120000.tar.gz"]


def test_busy_turn_defers_then_proceeds_after_the_bound(tmp_path):
    home = _data_home(tmp_path)
    repo = _repo(tmp_path)
    times = iter(
        [
            datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 25, 12, 15, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 25, 12, 30, 0, tzinfo=timezone.utc),
        ]
    )
    health = lambda: {"busy": {"active": True, "conversations": ["c1"]}}  # noqa: E731
    runner = _runner(
        tmp_path,
        home,
        repo,
        health=health,
        clock=lambda: next(times),
        max_deferrals=3,
    )

    assert runner.run() == 0
    first = json.loads((home / "backups" / "decafclaw-20261125-120000.json").read_text())
    assert first["status"] == "deferred"
    assert first["deferrals"] == 1

    assert runner.run() == 0
    second = json.loads((home / "backups" / "decafclaw-20261125-121500.json").read_text())
    assert second["status"] == "deferred"
    assert second["deferrals"] == 2

    assert runner.run() == 0
    third = json.loads((home / "backups" / "decafclaw-20261125-123000.json").read_text())
    assert third["status"] == "ok"
    assert (home / "backups" / "decafclaw-20261125-123000.tar.gz").exists()
    assert json.loads((home / "backups" / ".deferrals.json").read_text())["count"] == 0


def test_unreachable_health_does_not_defer(tmp_path):
    home = _data_home(tmp_path)
    repo = _repo(tmp_path)
    runner = _runner(tmp_path, home, repo, health=lambda: None)

    assert runner.run() == 0
    manifest = json.loads((home / "backups" / "decafclaw-20261125-120000.json").read_text())
    assert manifest["status"] == "ok"


def test_force_skips_the_idle_check(tmp_path):
    home = _data_home(tmp_path)
    repo = _repo(tmp_path)
    runner = _runner(
        tmp_path,
        home,
        repo,
        health=lambda: {"busy": {"active": True}},
        force=True,
    )

    assert runner.run() == 0
    manifest = json.loads((home / "backups" / "decafclaw-20261125-120000.json").read_text())
    assert manifest["status"] == "ok"


def test_lock_prevents_concurrent_runs(tmp_path):
    home = _data_home(tmp_path)
    repo = _repo(tmp_path)
    backup_dir = home / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    held = (backup_dir / ".backup.lock").open("a+")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert _runner(tmp_path, home, repo).run() == 0
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()

    manifest = json.loads((backup_dir / "decafclaw-20261125-120000.json").read_text())
    assert manifest["status"] == "locked"
    assert not list(backup_dir.glob("decafclaw-*.tar.gz"))
