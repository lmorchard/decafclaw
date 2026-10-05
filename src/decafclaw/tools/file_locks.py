"""Per-file concurrency guards for workspace mutations.

Serializes concurrent mutations targeting the same canonical file within a
process, preventing lost updates from interleaved read-modify-write cycles
while allowing mutations on different files to proceed in parallel.

Note: These guards coordinate participating tools within this process.
They do not coordinate arbitrary shell commands, external editors, or
separate processes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)


def get_canonical_path(path: Path | str) -> Path:
    """Return the resolved canonical Path for a file or directory.

    For existing paths, this resolves all symlinks. For paths that do not
    exist yet, it resolves existing parent directories and normalizes the path
    so that aliases to the same target map to the identical canonical key.
    """
    p = Path(path)
    try:
        return p.resolve()
    except (RuntimeError, OSError):
        return p.absolute()


class _LockEntry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ref_count = 0


class FileLockManager:
    """Thread-safe manager for per-file locks."""

    def __init__(self) -> None:
        self._locks: dict[str, _LockEntry] = {}
        self._registry_lock = threading.Lock()

    def _acquire_entry(self, key: str) -> _LockEntry:
        with self._registry_lock:
            entry = self._locks.get(key)
            if entry is None:
                entry = _LockEntry()
                self._locks[key] = entry
            entry.ref_count += 1
            return entry

    def _release_entry(self, key: str, entry: _LockEntry) -> None:
        with self._registry_lock:
            entry.ref_count -= 1
            if entry.ref_count <= 0:
                self._locks.pop(key, None)

    @contextlib.contextmanager
    def _lock_context(self, paths: Sequence[Path | str]):
        """Context manager acquiring locks for paths in sorted canonical order."""
        if not paths:
            yield
            return

        # Canonicalize, deduplicate, and sort keys to guarantee deadlock-free acquisition
        keys = sorted({str(get_canonical_path(p)) for p in paths})
        acquired_entries: list[tuple[str, _LockEntry]] = []

        try:
            for key in keys:
                entry = self._acquire_entry(key)
                try:
                    entry.lock.acquire()
                except BaseException:
                    self._release_entry(key, entry)
                    raise
                acquired_entries.append((key, entry))
            yield
        finally:
            # Release in reverse order of acquisition
            for key, entry in reversed(acquired_entries):
                try:
                    entry.lock.release()
                finally:
                    self._release_entry(key, entry)

    def lock(self, *paths: Path | str) -> _LockContext:
        """Return a context manager (supporting both 'with' and 'async with')."""
        return _LockContext(self, paths)

    def is_locked(self, path: Path | str) -> bool:
        """Check if a canonical path currently has an active held lock."""
        key = str(get_canonical_path(path))
        with self._registry_lock:
            entry = self._locks.get(key)
            if entry is None:
                return False
            return entry.lock.locked()

    def active_lock_count(self) -> int:
        """Return count of registered lock entries (for testing/diagnostics)."""
        with self._registry_lock:
            return len(self._locks)


class _LockContext:
    """Context manager supporting both synchronous 'with' and asynchronous 'async with'."""

    def __init__(self, manager: FileLockManager, paths: Sequence[Path | str]) -> None:
        self._manager = manager
        self._paths = paths
        self._cm = manager._lock_context(paths)

    def __enter__(self):
        return self._cm.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._cm.__exit__(exc_type, exc_val, exc_tb)

    async def __aenter__(self):
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, self._cm.__enter__)
        try:
            return await asyncio.shield(fut)
        except asyncio.CancelledError:
            # If awaiting task was cancelled, ensure the background thread completes
            # acquisition and immediately releases the lock to prevent leaks.
            await fut
            await loop.run_in_executor(None, self._cm.__exit__, None, None, None)
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._cm.__exit__, exc_type, exc_val, exc_tb)


_DEFAULT_MANAGER = FileLockManager()


def file_lock(*paths: Path | str) -> _LockContext:
    """Acquire per-file lock(s) across one or more paths in canonical order."""
    return _DEFAULT_MANAGER.lock(*paths)


def is_file_locked(path: Path | str) -> bool:
    """Check if a canonical path currently has an active held lock."""
    return _DEFAULT_MANAGER.is_locked(path)
