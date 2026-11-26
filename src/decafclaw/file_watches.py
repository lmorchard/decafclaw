"""First-class one-shot file watches (watch_file) for event-driven wake turns (#1560).

Complements timer-based scheduling (:mod:`decafclaw.scheduled_wakes`): a file
watch wakes the agent when an external event actually happens instead of when
the agent guesses it will.

Lifecycle:
  * ``watch_file`` arms a one-shot watch on a workspace path. The baseline
    file signature is persisted with the watch so a change that lands while
    the server is down still fires on the next startup scan.
  * A per-watch asyncio task polls the file signature at
    ``config.file_watch.poll_interval_sec`` (default 1s). Polling is a stat
    call, not a read — no new runtime dependency, and it works on every
    platform.
  * The first matching event, or the timeout, disarms the watch. A detected
    event enqueues a ``TurnKind.WAKE`` turn through ``ConversationManager``.

Sidecar state lives outside the agent-writable workspace at
``data/{agent_id}/file_watches/{conv_id}.json``, mirroring
``scheduled_wakes.json`` so workspace tools cannot forge watches.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .conversation_paths import file_watches_path
from .scheduled_wakes import compose_wake_context_setup

if TYPE_CHECKING:
    from .conversation_manager import ConversationManager

log = logging.getLogger(__name__)

# Events a watch can match. ``modify`` fires when an existing file's signature
# changes, ``create`` when the path appears, ``delete`` when it disappears, and
# ``any`` on the first of those.
WATCH_EVENTS = ("modify", "create", "delete", "any")

# Fire kinds reported in the wake prompt/metadata (``any`` resolves to one).
_FIRE_VERB = {
    "created": "created",
    "modified": "modified",
    "deleted": "deleted",
}

_MAX_TIMEOUT_SECONDS = 30 * 86400


@dataclass
class FileWatch:
    """A pending one-shot file watch."""

    watch_id: str
    conv_id: str
    path: str  # resolved absolute path
    event: str
    reason: str
    timeout_seconds: int
    created_at: str  # ISO-8601 UTC
    expires_at: str  # ISO-8601 UTC
    expires_at_ts: float  # Unix timestamp
    baseline_exists: bool = False
    baseline_mtime_ns: int | None = None
    baseline_size: int | None = None
    baseline_inode: int | None = None
    user_id: str = ""
    channel_id: str = ""
    thread_id: str = ""
    transport: str = ""


def load_file_watches(config, conv_id: str) -> list[FileWatch]:
    """Load pending watches from the conversation's sidecar file.

    Fail-open: missing, empty, or corrupt files return an empty list.
    """
    path = file_watches_path(config, conv_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("watches", [])
        return [FileWatch(**item) for item in entries if isinstance(item, dict)]
    except Exception as exc:
        log.warning("Failed to load file watches for conv %s: %s", conv_id, exc)
        return []


def save_file_watches(config, conv_id: str, watches: list[FileWatch]) -> None:
    """Save watches atomically to the conversation's sidecar file."""
    path = file_watches_path(config, conv_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"watches": [asdict(w) for w in watches]}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("Failed to save file watches for conv %s: %s", conv_id, exc)
        raise


def _stat_signature(path: str) -> tuple[bool, int | None, int | None, int | None]:
    """Current (exists, mtime_ns, size, inode) for ``path``.

    Only a missing file reports ``exists=False``. Other ``OSError`` values
    (permissions, transient I/O) propagate so a poller can skip that tick
    instead of misreading the failure as a delete.
    """
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return False, None, None, None
    return True, st.st_mtime_ns, st.st_size, st.st_ino


def display_watch_path(config, path) -> str:
    """Workspace-relative path when possible, else the absolute path."""
    target = Path(path)
    try:
        return str(target.relative_to(config.workspace_path.resolve()))
    except ValueError:
        return str(target)


def observed_event(
    watch: FileWatch, exists: bool, mtime_ns: int | None, size: int | None, inode: int | None
) -> str | None:
    """Return ``created``/``modified``/``deleted`` when the watch matches, else None.

    A match is reported regardless of the watch's ``event`` filter; the caller
    decides whether that fire kind is wanted. Signature comparison uses
    mtime/size/inode, so an atomic replace (new inode) counts as a modify even
    when the content size is unchanged.
    """
    if not watch.baseline_exists and exists:
        return "created"
    if watch.baseline_exists and not exists:
        return "deleted"
    if watch.baseline_exists and exists:
        changed = mtime_ns != watch.baseline_mtime_ns or size != watch.baseline_size or inode != watch.baseline_inode
        if changed:
            return "modified"
    return None


def _event_matches(watch_event: str, fire_kind: str) -> bool:
    if watch_event == "any":
        # ``any`` covers the first real change, not just create/delete.
        return True
    return {"created": "create", "modified": "modify", "deleted": "delete"}[fire_kind] == watch_event


class FileWatchManager:
    """Coordinates in-memory pollers and persistent sidecars for file watches."""

    def __init__(self, config, manager: ConversationManager | None = None) -> None:
        self.config = config
        self.manager = manager
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._shutting_down = False

    @property
    def _cfg(self):
        return self.config.file_watch

    def _poll_interval(self) -> float:
        return max(0.05, float(self._cfg.poll_interval_sec))

    async def watch(
        self,
        conv_id: str,
        path: str,
        *,
        event: str = "modify",
        timeout_seconds: int = 3600,
        reason: str = "",
        user_id: str = "",
        channel_id: str = "",
        thread_id: str = "",
        transport: str = "",
    ) -> FileWatch:
        """Arm a one-shot watch on a resolved absolute path."""
        if self._shutting_down:
            raise RuntimeError("FileWatchManager is shutting down")
        if not self._cfg.enabled:
            raise ValueError("file watches are disabled (config file_watch.enabled)")
        if event not in WATCH_EVENTS:
            raise ValueError(f"event must be one of {', '.join(WATCH_EVENTS)} (got {event!r})")
        if timeout_seconds < 1:
            raise ValueError(f"timeout must be >= 1 second (got {timeout_seconds})")
        if timeout_seconds > self._cfg.max_timeout_sec:
            raise ValueError(f"timeout exceeds maximum allowed ({self._cfg.max_timeout_sec}s)")
        if timeout_seconds > _MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout exceeds maximum allowed ({_MAX_TIMEOUT_SECONDS}s)")

        target = str(Path(path).expanduser().resolve())
        try:
            exists, mtime_ns, size, inode = _stat_signature(target)
        except OSError as exc:
            raise ValueError(f"cannot stat path {target}: {exc}") from exc
        if event in ("modify", "delete") and not exists:
            raise ValueError(f"path does not exist: {target}")
        if event == "create" and exists:
            raise ValueError(f"path already exists; use event='modify' instead: {target}")

        now = datetime.now(timezone.utc)
        fire_ts = now.timestamp() + timeout_seconds
        watch = FileWatch(
            watch_id=f"watch_{uuid.uuid4().hex[:8]}",
            conv_id=conv_id,
            path=target,
            event=event,
            reason=reason.strip(),
            timeout_seconds=timeout_seconds,
            created_at=now.isoformat(),
            expires_at=datetime.fromtimestamp(fire_ts, tz=timezone.utc).isoformat(),
            expires_at_ts=fire_ts,
            baseline_exists=exists,
            baseline_mtime_ns=mtime_ns,
            baseline_size=size,
            baseline_inode=inode,
            user_id=user_id,
            channel_id=channel_id,
            thread_id=thread_id,
            transport=transport,
        )

        async with self._lock:
            watches = load_file_watches(self.config, conv_id)
            if len(watches) >= self._cfg.max_watches_per_conversation:
                raise ValueError(
                    f"too many active file watches for this conversation (max {self._cfg.max_watches_per_conversation})"
                )
            watches.append(watch)
            save_file_watches(self.config, conv_id, watches)
            self._start_task(watch)

        log.info(
            "Armed file watch %s for conv %s on %s (%s, expires %s)",
            watch.watch_id,
            conv_id[:8],
            target,
            event,
            watch.expires_at,
        )
        return watch

    async def cancel(self, conv_id: str, watch_id: str) -> bool:
        """Cancel a pending watch. Returns True if found and cancelled."""
        async with self._lock:
            watches = load_file_watches(self.config, conv_id)
            if not any(w.watch_id == watch_id for w in watches):
                return False
            self._drop_entry(conv_id, watch_id)
            task = self._tasks.pop(watch_id, None)
            if task and not task.done():
                task.cancel()
            log.info("Cancelled file watch %s for conv %s", watch_id, conv_id[:8])
            return True

    async def cancel_all_for_conversation(self, conv_id: str) -> None:
        """Cancel all pending in-memory pollers for a conversation (e.g. on deletion)."""
        async with self._lock:
            for w in load_file_watches(self.config, conv_id):
                task = self._tasks.pop(w.watch_id, None)
                if task and not task.done():
                    task.cancel()

    def list_watches(self, conv_id: str) -> list[FileWatch]:
        """List pending watches for a conversation, sorted by expiry."""
        return sorted(load_file_watches(self.config, conv_id), key=lambda w: w.expires_at_ts)

    # -- internals -----------------------------------------------------------

    def _start_task(self, watch: FileWatch) -> None:
        self._tasks[watch.watch_id] = asyncio.create_task(self._run_watch(watch))

    def _drop_entry(self, conv_id: str, watch_id: str) -> None:
        """Remove one sidecar entry. Caller holds ``self._lock``."""
        watches = load_file_watches(self.config, conv_id)
        remaining = [w for w in watches if w.watch_id != watch_id]
        if len(remaining) != len(watches):
            save_file_watches(self.config, conv_id, remaining)

    def _check_triggered(self, watch: FileWatch) -> str | None:
        try:
            exists, mtime_ns, size, inode = _stat_signature(watch.path)
        except OSError as exc:
            log.debug("File watch %s stat failed; retrying next poll: %s", watch.watch_id, exc)
            return None
        fire_kind = observed_event(watch, exists, mtime_ns, size, inode)
        if fire_kind is None or not _event_matches(watch.event, fire_kind):
            return None
        return fire_kind

    async def _run_watch(self, watch: FileWatch) -> None:
        """Poll until the watch fires or expires, then disarm."""
        interval = self._poll_interval()
        try:
            while True:
                remaining = watch.expires_at_ts - time.time()
                if remaining <= 0:
                    await self._expire(watch)
                    return
                await asyncio.sleep(min(interval, remaining))
                if self._shutting_down:
                    return
                fire_kind = self._check_triggered(watch)
                if fire_kind is not None:
                    await self._fire(watch, fire_kind)
                    return
        except asyncio.CancelledError:
            log.debug("File watch poller cancelled for %s", watch.watch_id)
            raise
        except Exception as exc:
            log.warning("Error running file watch %s: %s", watch.watch_id, exc)
            # Do not leave a sidecar entry claiming a watch that no in-memory
            # poller backs. A dropped watch is visible to watch_list as gone.
            async with self._lock:
                self._tasks.pop(watch.watch_id, None)
                if not self._shutting_down:
                    try:
                        self._drop_entry(watch.conv_id, watch.watch_id)
                    except OSError as exc2:
                        log.warning("Failed to drop failed file watch %s: %s", watch.watch_id, exc2)

    async def _expire(self, watch: FileWatch) -> None:
        """Disarm an expired watch; optionally fire a timeout notice."""
        async with self._lock:
            self._tasks.pop(watch.watch_id, None)
            self._drop_entry(watch.conv_id, watch.watch_id)
        log.info("File watch %s expired for conv %s (%s)", watch.watch_id, watch.conv_id[:8], watch.path)
        if self._cfg.notify_on_timeout and not self._shutting_down and self.manager is not None:
            await self._enqueue_wake(
                watch,
                prompt=(
                    f"File watch expired without a matching event: {self._display_path(watch.path)} "
                    f"({watch.event}, {watch.timeout_seconds}s)."
                    + (f" Reason: {watch.reason}." if watch.reason else "")
                    + " Respond to the user only if they need to know; otherwise reply with BACKGROUND_WAKE_OK "
                    "to remain silent."
                ),
                metadata={
                    "watch_id": watch.watch_id,
                    "path": self._display_path(watch.path),
                    "event": "timeout",
                    "channel_id": watch.channel_id,
                    "thread_id": watch.thread_id,
                    "transport": watch.transport,
                },
            )

    async def _fire(self, watch: FileWatch, fire_kind: str) -> None:
        """Disarm on the first matching event and enqueue a wake turn."""
        async with self._lock:
            self._tasks.pop(watch.watch_id, None)
            # Drop the sidecar entry first: a restart must not re-fire an event
            # that already produced a wake (one-shot semantics).
            self._drop_entry(watch.conv_id, watch.watch_id)

        if self._shutting_down or self.manager is None:
            log.debug("No manager attached to FileWatchManager; watch %s event dropped", watch.watch_id)
            return

        display = self._display_path(watch.path)
        verb = _FIRE_VERB[fire_kind]
        prompt = (
            f"File {verb}: {display}."
            + (f" Reason: {watch.reason}." if watch.reason else "")
            + " Review the change and respond to the user, or reply with BACKGROUND_WAKE_OK to remain silent "
            "if nothing requires attention."
        )
        await self._enqueue_wake(
            watch,
            prompt=prompt,
            metadata={
                "watch_id": watch.watch_id,
                "path": display,
                "event": fire_kind,
                "channel_id": watch.channel_id,
                "thread_id": watch.thread_id,
                "transport": watch.transport,
            },
        )
        log.info(
            "File watch %s fired (%s) for conv %s: %s",
            watch.watch_id,
            fire_kind,
            watch.conv_id[:8],
            watch.path,
        )

    async def _enqueue_wake(self, watch: FileWatch, *, prompt: str, metadata: dict[str, Any]) -> None:
        from .conversation_manager import TurnKind

        manager = self.manager
        if manager is None:
            log.debug("No manager attached to FileWatchManager; watch %s wake dropped", watch.watch_id)
            return
        context_setup = compose_wake_context_setup(
            manager,
            watch.conv_id,
            channel_id=watch.channel_id,
            thread_id=watch.thread_id,
            transport=watch.transport,
        )
        try:
            future = await manager.enqueue_turn(
                conv_id=watch.conv_id,
                kind=TurnKind.WAKE,
                prompt=prompt,
                metadata=metadata,
                user_id=watch.user_id,
                context_setup=context_setup,
            )
        except Exception as exc:
            log.warning("Failed to enqueue wake for file watch %s: %s", watch.watch_id, exc)
            return
        if future.rate_limited is True:
            log.info("File watch %s wake rate-limited; event dropped (one-shot)", watch.watch_id)

    def _display_path(self, path: str) -> str:
        return display_watch_path(self.config, path)

    # -- startup -------------------------------------------------------------

    async def startup_scan_conversation(self, conv_id: str) -> int:
        """Resume pending watches for a conversation after a restart."""
        watches = load_file_watches(self.config, conv_id)
        if not watches:
            return 0

        resumed = 0
        now_ts = time.time()
        for watch in watches:
            if watch.watch_id in self._tasks:
                continue
            if watch.expires_at_ts <= now_ts:
                # Expired while offline. _expire drops the entry (and fires a
                # timeout notice when configured).
                await self._expire(watch)
                continue
            fire_kind = self._check_triggered(watch)
            if fire_kind is not None:
                # The event landed while the server was down: fire immediately
                # from the persisted baseline instead of waiting for another one.
                await self._fire(watch, fire_kind)
                continue
            async with self._lock:
                self._start_task(watch)
            resumed += 1
            log.info(
                "Startup scan: resumed file watch %s for conv %s (expires in %.1fs)",
                watch.watch_id,
                conv_id[:8],
                max(0.0, watch.expires_at_ts - now_ts),
            )
        return resumed

    async def startup_scan(self) -> int:
        """Scan all conversation sidecars under data/{agent_id}/file_watches/."""
        watches_dir = (self.config.agent_path / "file_watches").resolve()
        if not watches_dir.exists():
            return 0

        recovered = 0
        try:
            files = sorted(watches_dir.glob("*.json"))
        except OSError as exc:
            log.warning("Failed to list file_watches dir for scan: %s", exc)
            return 0

        for file in files:
            recovered += await self.startup_scan_conversation(file.stem)

        if recovered:
            log.info("Startup scan: resumed %d file watch timer(s)", recovered)
        return recovered

    async def shutdown(self) -> None:
        """Cancel all in-flight pollers on shutdown without altering sidecar state."""
        self._shutting_down = True
        async with self._lock:
            tasks = list(self._tasks.values())
            for t in tasks:
                if not t.done():
                    t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._tasks.clear()
        log.info("File watch manager shutdown complete")
