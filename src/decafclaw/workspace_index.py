"""Workspace file index with background refresh and file-backed persistence.

Maintains an in-memory cache of workspace filenames that is:
1. Instantly readable (< 0.1 ms) without blocking HTTP requests
2. Persisted to disk (survives restarts)
3. Refreshed asynchronously in the background (never blocks queries)

The index is lazily initialized on first use and refreshed every 30 seconds
(or when explicitly invalidated). Queries always return the current cached
state immediately, with refreshes happening in background tasks.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

# Index TTL in seconds - queries older than this trigger a background refresh
_INDEX_TTL_SECONDS = 30.0

# In-memory state - initialized on first access
_workspace_index: dict[str, list[str]] | None = None
_index_timestamp: float = 0.0
_refresh_task: asyncio.Task | None = None
_refresh_lock = asyncio.Lock()


@dataclass
class WorkspaceIndex:
    """Persisted workspace index structure."""
    files: list[str]
    timestamp: float


def _get_index_path(config: "Config") -> Path:
    """Return the path to the workspace index file."""
    return config.agent_path / "workspace_index.json"


async def _scan_workspace_files(config: "Config") -> list[str]:
    """Scan workspace directory and return list of relative file paths.

    Runs synchronously via asyncio.to_thread to avoid blocking.
    """
    def _do_scan():
        workspace_root = config.workspace_path
        if not workspace_root.is_dir():
            return []

        files = []
        workspace_resolved = workspace_root.resolve()
        vault_resolved = None
        try:
            vault_resolved = config.vault_root.resolve()
        except Exception:
            pass

        # Directories to prune from walk
        prune_dirs = frozenset({
            "conversations",
            ".schedule_last_run",
            "attachments",
        })

        for dirpath, dirnames, filenames in os.walk(workspace_root):
            # Prune hidden, node_modules, and specific top-level dirs
            is_root = False
            try:
                is_root = Path(dirpath).resolve() == workspace_resolved
            except Exception:
                pass

            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".")
                and d != "node_modules"
                and (not is_root or d not in prune_dirs)
                and (not vault_resolved or (Path(dirpath) / d).resolve() != vault_resolved)
            ]

            # Skip dirs inside vault
            try:
                dirpath_path = Path(dirpath).resolve()
                if vault_resolved and (dirpath_path == vault_resolved or dirpath_path.is_relative_to(vault_resolved)):
                    continue
            except Exception:
                pass

            for fname in filenames:
                if fname.startswith("."):
                    continue
                fpath = Path(dirpath) / fname
                try:
                    resolved = fpath.resolve()
                    rel = resolved.relative_to(workspace_resolved)
                    files.append(rel.as_posix())
                except (OSError, ValueError):
                    continue

        return sorted(files)

    return await asyncio.to_thread(_do_scan)


async def _load_index_from_disk(config: "Config") -> WorkspaceIndex | None:
    """Load workspace index from disk, return None if not found or invalid."""
    index_path = _get_index_path(config)
    if not index_path.exists():
        return None

    try:
        def _do_load():
            with open(index_path) as f:
                data = json.load(f)
                return WorkspaceIndex(
                    files=data.get("files", []),
                    timestamp=data.get("timestamp", 0.0)
                )
        return await asyncio.to_thread(_do_load)
    except Exception as e:
        log.warning("Failed to load workspace index from %s: %s", index_path, e)
        return None


async def _save_index_to_disk(config: "Config", index: WorkspaceIndex) -> None:
    """Atomically save workspace index to disk via tmpfile + os.replace."""
    index_path = _get_index_path(config)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    def _do_save():
        tmp_path = index_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w") as f:
                json.dump(asdict(index), f)
            os.replace(tmp_path, index_path)
        except Exception as e:
            log.warning("Failed to save workspace index to %s: %s", index_path, e)
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    await asyncio.to_thread(_do_save)


async def _refresh_workspace_index(config: "Config") -> None:
    """Background task: rebuild the workspace index and save to disk.

    Never blocks queries - only updates the in-memory state when complete.
    """
    global _workspace_index, _index_timestamp

    async with _refresh_lock:
        try:
            log.debug("Starting workspace index refresh")
            files = await _scan_workspace_files(config)
            timestamp = time.time()

            # Update in-memory state
            _workspace_index = {"files": files}
            _index_timestamp = timestamp

            # Persist to disk
            index = WorkspaceIndex(files=files, timestamp=timestamp)
            await _save_index_to_disk(config, index)

            log.debug("Workspace index refreshed: %d files", len(files))
        except Exception as e:
            log.warning("Workspace index refresh failed: %s", e)


async def get_workspace_files(config: "Config") -> list[str]:
    """Get workspace files from in-memory cache, triggering background refresh if stale.

    Returns instantly from cache regardless of TTL state. If cache is missing or
    expired, fires a background refresh task (but still returns current state).

    Special case: On first access with no disk cache, waits for initial scan to
    complete (otherwise would always return empty on first call).
    """
    global _workspace_index, _index_timestamp, _refresh_task

    # First access: try loading from disk
    first_access = _workspace_index is None
    if first_access:
        loaded = await _load_index_from_disk(config)
        if loaded:
            _workspace_index = {"files": loaded.files}
            _index_timestamp = loaded.timestamp
            log.debug("Loaded workspace index from disk: %d files", len(loaded.files))
        else:
            # No disk cache - initialize empty and will trigger refresh below
            _workspace_index = {"files": []}
            _index_timestamp = 0.0

    # Check if refresh is needed (TTL expired)
    age = time.time() - _index_timestamp
    needs_refresh = age > _INDEX_TTL_SECONDS

    # Trigger refresh if needed
    if needs_refresh:
        if _refresh_task is None or _refresh_task.done():
            _refresh_task = asyncio.create_task(_refresh_workspace_index(config))
            log.debug("Triggered background workspace index refresh (age: %.1fs)", age)

            # On first access with no disk cache, wait for the initial scan
            # (otherwise we'd always return empty on first call)
            if first_access and not loaded:
                await _refresh_task

    # Return current cache (either from disk, or from just-completed initial scan)
    if _workspace_index is None:
        return []
    return _workspace_index["files"]


def invalidate_workspace_file_cache() -> None:
    """Invalidate the workspace file cache, forcing a refresh on next query.

    Sets the timestamp to 0 so the next get_workspace_files() call will
    trigger a background refresh. Queries still return the current cached
    state immediately.
    """
    global _index_timestamp
    _index_timestamp = 0.0
    log.debug("Workspace file cache invalidated")
