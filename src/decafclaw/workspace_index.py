"""Workspace file index with background refresh and file-backed persistence.

Maintains an in-memory cache of workspace filenames that is:
1. Instantly readable (< 0.1 ms) without blocking HTTP requests
2. Persisted to disk (survives restarts)
3. Refreshed asynchronously in the background at server startup, automatically every 30 minutes,
   and whenever files are created or deleted.

Queries always return the current cached state immediately, with refreshes
happening in background tasks.
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

# Index TTL in seconds - 30 minutes
_INDEX_TTL_SECONDS = 1800.0

# In-memory state - initialized on first access
_workspace_index: dict[str, list[str]] | None = None
_index_timestamp: float = 0.0
_refresh_task: asyncio.Task | None = None
_periodic_task: asyncio.Task | None = None
_refresh_lock = asyncio.Lock()
_needs_another_refresh: bool = False
_last_config: "Config | None" = None
_main_loop: asyncio.AbstractEventLoop | None = None


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
    global _workspace_index, _index_timestamp, _needs_another_refresh

    async with _refresh_lock:
        try:
            _needs_another_refresh = False
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

        if _needs_another_refresh:
            _needs_another_refresh = False
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_refresh_workspace_index(config))
            except RuntimeError:
                pass


def trigger_workspace_index_refresh(config: "Config") -> asyncio.Task | None:
    """Trigger a background refresh task if one is not already running."""
    global _refresh_task, _last_config, _main_loop, _needs_another_refresh
    _last_config = config
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        pass

    if _refresh_task is not None and not _refresh_task.done():
        log.debug("Workspace index refresh already in progress, marking for follow-up")
        _needs_another_refresh = True
        return _refresh_task

    try:
        loop = asyncio.get_running_loop()
        _refresh_task = loop.create_task(_refresh_workspace_index(config))
        log.debug("Triggered background workspace index refresh")
    except RuntimeError:
        if _main_loop and _main_loop.is_running():
            _main_loop.call_soon_threadsafe(
                lambda: trigger_workspace_index_refresh(config)
            )
        else:
            log.warning("Cannot trigger workspace index refresh: no running event loop")

    return _refresh_task


def start_workspace_index_loop(config: "Config") -> asyncio.Task:
    """Start periodic background workspace index refresh loop (every 30 mins).

    Triggers an immediate scan on startup and schedules periodic refreshes
    every 30 minutes. Returns the loop task.
    """
    global _periodic_task, _main_loop, _last_config
    _last_config = config
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        pass

    if _periodic_task is not None and not _periodic_task.done():
        return _periodic_task

    async def _loop():
        trigger_workspace_index_refresh(config)
        while True:
            await asyncio.sleep(_INDEX_TTL_SECONDS)
            trigger_workspace_index_refresh(config)

    _periodic_task = asyncio.create_task(_loop())
    return _periodic_task


async def get_workspace_files(config: "Config") -> list[str]:
    """Get workspace files from in-memory cache, triggering background refresh if stale.

    Returns instantly from cache regardless of TTL state. If cache is missing or
    expired (TTL > 30 mins or invalidated), triggers a background refresh task.

    Special case: On first access with no disk cache, waits for initial scan to
    complete (otherwise would return empty list on first call).
    """
    global _workspace_index, _index_timestamp, _last_config, _main_loop
    _last_config = config
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        pass

    # First access: try loading from disk
    first_access = _workspace_index is None
    loaded = None
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

    # Check if refresh is needed (TTL expired or invalidated)
    age = time.time() - _index_timestamp
    needs_refresh = age > _INDEX_TTL_SECONDS or _index_timestamp == 0.0

    # Trigger refresh if needed
    if needs_refresh:
        task = trigger_workspace_index_refresh(config)

        # On first access with no disk cache, wait for initial scan
        if first_access and not loaded and task and not task.done():
            await task

    if _workspace_index is None:
        return []
    return _workspace_index["files"]


def invalidate_workspace_file_cache(config: "Config | None" = None) -> None:
    """Invalidate the workspace file cache and trigger an immediate background refresh.

    Sets the timestamp to 0 so cache is marked stale and triggers a background
    refresh task immediately if not already running.
    """
    global _index_timestamp, _last_config
    _index_timestamp = 0.0
    cfg = config or _last_config
    log.debug("Workspace file cache invalidated")
    if cfg is not None:
        trigger_workspace_index_refresh(cfg)


def make_workspace_index_subscriber(config: "Config"):
    """EventBus subscriber: invalidates workspace index cache on `vault_changed` events."""
    async def handle(event: dict) -> None:
        try:
            if event.get("type") == "vault_changed":
                invalidate_workspace_file_cache(config)
        except Exception as exc:  # fail-open
            log.debug("workspace_index subscriber error: %s", exc)

    return handle

