#!/usr/bin/env python3
"""Scheduled DecafClaw data backup (#1646).

Backs up the agent data that is **not** reproducible: conversations and their
sidecars, agent-authored content, admin config/prompts/skills, and operational
state. Git checkouts, the virtualenv, the derived embedding index, and
append-only telemetry/logs are excluded.

Runs standalone (systemd oneshot or by hand), takes a ``flock`` so a scheduled
run and a pre-deploy run cannot collide, and defers while a turn is active
until a bounded number of deferrals, then proceeds anyway.

Usage::

    python3 scripts/backup.py \\
        --data-home ~/.decafclaw/decafclaw \\
        --repo-dir ~/decafclaw \\
        --backup-dir /mnt/decafclaw-backups

Outputs ``decafclaw-YYYYMMDD-HHMMSS.tar.gz`` plus a ``.json`` manifest in the
backup directory. Retention keeps the 5 newest archives and never deletes the
archive just written. Exit status is 0 for success, a deferral, or a lock
skip; 1 for a failure.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import sys
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger("decafclaw-backup")

# Paths relative to the data home. Missing entries are skipped.
INCLUDE_PATHS = (
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

# Paths that must never appear in an archive, even if an include directory
# grows to contain them. Kept as prefixes; a trailing name matches the exact
# path and any ``-wal``/``-shm`` siblings via the explicit entries.
EXCLUDE_PATHS = (
    "workspace/embeddings.db",
    "workspace/embeddings.db-wal",
    "workspace/embeddings.db-shm",
    "workspace/telemetry",
    "workspace/audit.jsonl",
    "workspace/tool_usage.jsonl",
    "backups",
)

ARCHIVE_GLOB = "decafclaw-*.tar.gz"
MANIFEST_GLOB = "decafclaw-*.json"
DEFAULT_RETENTION = 5
DEFAULT_MAX_DEFERRALS = 4


@dataclass
class BackupConfig:
    data_home: Path
    repo_dir: Path
    backup_dir: Path
    retention: int = DEFAULT_RETENTION
    max_deferrals: int = DEFAULT_MAX_DEFERRALS
    health_url: str = "http://127.0.0.1:18880/health"
    force: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(now: datetime) -> str:
    return now.strftime("%Y%m%d-%H%M%S")


def _iso(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_health(url: str, timeout: float = 5.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except OSError:
            return None
    except (OSError, ValueError):
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _excluded(rel_path: str) -> bool:
    for prefix in EXCLUDE_PATHS:
        if rel_path == prefix or rel_path.startswith(prefix + "/"):
            return True
    return False


def _iter_files(root: Path, base_rel: str):
    """Yield files under ``root``, skipping excluded paths.

    Exclusion checks use the path relative to the data home (``base_rel``),
    because the exclude list is expressed in those terms.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames if not _excluded(f"{base_rel}/{(base / d).relative_to(root).as_posix()}")
        )
        for name in sorted(filenames):
            path = base / name
            if _excluded(f"{base_rel}/{path.relative_to(root).as_posix()}"):
                continue
            yield path


def _add_path(tar: tarfile.TarFile, source: Path, arcname: str) -> int:
    """Add a file or directory tree to the archive; return the file count."""
    if source.is_file():
        tar.add(source, arcname=arcname, recursive=False)
        return 1
    count = 0
    tar.add(source, arcname=arcname, recursive=False)
    for path in _iter_files(source, arcname):
        tar.add(path, arcname=f"{arcname}/{path.relative_to(source).as_posix()}", recursive=False)
        count += 1
    return count


def _add_env(tar: tarfile.TarFile, repo_dir: Path) -> int:
    env_path = repo_dir / ".env"
    if not env_path.is_file():
        return 0
    info = tar.gettarinfo(str(env_path), arcname="env")
    info.mode = 0o600
    with env_path.open("rb") as fh:
        tar.addfile(info, fh)
    return 1


def _config_sha(data_home: Path) -> str:
    config = data_home / "config.json"
    try:
        return hashlib.sha256(config.read_bytes()).hexdigest()
    except OSError:
        return ""


def _read_deferrals(path: Path) -> int:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return int(raw.get("count") or 0)


def _write_deferrals(path: Path, count: int, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"count": count, "last_at": _iso(now)}) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_manifest(backup_dir: Path, base: str, manifest: dict) -> Path:
    path = backup_dir / f"{base}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _prune(backup_dir: Path, keep: int, current: str) -> list[str]:
    """Delete archives beyond ``keep``, never the one just written."""
    archives = sorted(
        (p for p in backup_dir.glob(ARCHIVE_GLOB) if p.name != current),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = []
    # ``keep`` counts the archive just written, so only keep-1 others survive.
    for path in archives[max(0, keep - 1) :]:
        try:
            path.unlink()
            removed.append(path.name)
        except OSError as exc:
            log.warning("Could not remove old backup %s: %s", path, exc)
    # Manifests are tiny; keep the newest 30 so deferred/failed outcomes stay visible.
    manifests = sorted(backup_dir.glob(MANIFEST_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in manifests[30:]:
        try:
            path.unlink()
        except OSError:
            pass
    return removed


class BackupRunner:
    def __init__(
        self,
        config: BackupConfig,
        *,
        health: Callable[[], dict | None] | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.config = config
        self._health = health or (lambda: _fetch_health(config.health_url))
        self._clock = clock

    def acquire_lock(self):
        self.config.backup_dir.mkdir(parents=True, exist_ok=True)
        handle = (self.config.backup_dir / ".backup.lock").open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        return handle

    def should_defer(self, now: datetime) -> tuple[bool, int, str]:
        if self.config.force:
            return False, 0, ""
        payload = self._health()
        if payload is None:
            return False, 0, "health unreachable; backing up anyway"
        busy = payload.get("busy") or {}
        if not busy.get("active"):
            return False, 0, ""
        state_path = self.config.backup_dir / ".deferrals.json"
        count = _read_deferrals(state_path) + 1
        _write_deferrals(state_path, count, now)
        if count >= self.config.max_deferrals:
            return False, count, f"still busy after {count} deferrals; backing up anyway"
        return True, count, f"conversation busy ({', '.join(busy.get('conversations') or []) or 'unknown'})"

    def run(self) -> int:
        now = self._clock()
        base = f"decafclaw-{_stamp(now)}"
        manifest = {
            "timestamp": _iso(now),
            "status": "failed",
            "archive": "",
            "size_bytes": 0,
            "file_count": 0,
            "included": [],
            "excluded": list(EXCLUDE_PATHS),
            "config_sha256": _config_sha(self.config.data_home),
            "deferred": False,
            "deferrals": 0,
            "error": "",
        }

        lock = self.acquire_lock()
        if lock is None:
            manifest["status"] = "locked"
            manifest["error"] = "another backup holds the lock"
            _write_manifest(self.config.backup_dir, base, manifest)
            log.info("Another backup is running; skipping.")
            return 0

        try:
            defer, deferrals, reason = self.should_defer(now)
            manifest["deferrals"] = deferrals
            if defer:
                manifest["status"] = "deferred"
                manifest["deferred"] = True
                manifest["error"] = reason
                _write_manifest(self.config.backup_dir, base, manifest)
                log.info("Backup deferred: %s", reason)
                return 0
            if reason:
                log.info("%s", reason)

            archive_path = self.config.backup_dir / f"{base}.tar.gz"
            partial = archive_path.parent / f"{archive_path.name}.partial"
            file_count = 0
            included = []
            try:
                with tarfile.open(partial, "w:gz") as tar:
                    for rel in INCLUDE_PATHS:
                        source = self.config.data_home / rel
                        if not source.exists():
                            continue
                        file_count += _add_path(tar, source, rel)
                        included.append(rel)
                    file_count += _add_env(tar, self.config.repo_dir)
                    if (self.config.repo_dir / ".env").is_file():
                        included.append("env")
                os.replace(partial, archive_path)
                os.chmod(archive_path, 0o600)
            except Exception:
                partial.unlink(missing_ok=True)
                raise

            manifest.update(
                status="ok",
                archive=archive_path.name,
                size_bytes=archive_path.stat().st_size,
                file_count=file_count,
                included=included,
            )
            _write_manifest(self.config.backup_dir, base, manifest)
            _write_deferrals(self.config.backup_dir / ".deferrals.json", 0, now)
            removed = _prune(self.config.backup_dir, self.config.retention, archive_path.name)
            log.info(
                "Backup written: %s (%s bytes, %s files)%s",
                archive_path,
                manifest["size_bytes"],
                file_count,
                f"; pruned {len(removed)} old archive(s)" if removed else "",
            )
            return 0
        except Exception as exc:  # noqa: BLE001 - report every failure in the manifest
            manifest["error"] = str(exc)
            _write_manifest(self.config.backup_dir, base, manifest)
            log.exception("Backup failed")
            return 1
        finally:
            lock.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    data_home_env = os.environ.get("DATA_HOME")
    default_home = (
        Path(data_home_env).expanduser() / "decafclaw" if data_home_env else Path.home() / ".decafclaw/decafclaw"
    )
    parser.add_argument("--data-home", default=str(default_home))
    parser.add_argument("--repo-dir", default=str(Path.home() / "decafclaw"))
    parser.add_argument("--backup-dir", default="")
    parser.add_argument("--retention", type=int, default=DEFAULT_RETENTION)
    parser.add_argument("--max-deferrals", type=int, default=DEFAULT_MAX_DEFERRALS)
    parser.add_argument("--health-url", default="http://127.0.0.1:18880/health")
    parser.add_argument("--force", action="store_true", help="skip the idle check")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    data_home = Path(args.data_home).expanduser()
    if not data_home.is_dir():
        log.error("Data home does not exist: %s", data_home)
        return 1
    backup_dir = Path(args.backup_dir).expanduser() if args.backup_dir else data_home / "backups"
    config = BackupConfig(
        data_home=data_home,
        repo_dir=Path(args.repo_dir).expanduser(),
        backup_dir=backup_dir,
        retention=max(1, args.retention),
        max_deferrals=max(1, args.max_deferrals),
        health_url=args.health_url,
        force=args.force,
    )
    return BackupRunner(config).run()


if __name__ == "__main__":
    sys.exit(main())
