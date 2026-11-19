"""Single-shot post-restart conversation wake (#1554).

``admin_restart(wake_on_restart=True)`` writes one record per calling
conversation before the daemon drains and exits. On the next startup, after
transports are listening, each record is atomically consumed and turned into a
``TurnKind.WAKE`` turn so the agent can verify the restart and report back.

Single-shot by design: the record is deleted before the wake turn is enqueued,
so a failing wake can never cause a restart/wake crash loop.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .conversation_paths import restart_wake_path

log = logging.getLogger(__name__)


@dataclass
class RestartWake:
    """One conversation's pending post-restart wake."""

    conv_id: str
    reason: str
    requested_at: str  # ISO-8601 UTC
    user_id: str = ""
    channel_id: str = ""
    thread_id: str = ""
    transport: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "RestartWake":
        """Build a record from JSON, rejecting entries that cannot be delivered."""
        conv_id = str(data.get("conv_id") or "")
        if not conv_id:
            raise ValueError("missing conv_id")
        return cls(
            conv_id=conv_id,
            reason=str(data.get("reason") or ""),
            requested_at=str(data.get("requested_at") or ""),
            user_id=str(data.get("user_id") or ""),
            channel_id=str(data.get("channel_id") or ""),
            thread_id=str(data.get("thread_id") or ""),
            transport=str(data.get("transport") or ""),
        )


def new_restart_wake(
    *,
    conv_id: str,
    reason: str,
    user_id: str = "",
    channel_id: str = "",
    thread_id: str = "",
    transport: str = "",
) -> RestartWake:
    """Create a record stamped with the current UTC time."""
    return RestartWake(
        conv_id=conv_id,
        reason=reason,
        requested_at=datetime.now(timezone.utc).isoformat(),
        user_id=user_id,
        channel_id=channel_id,
        thread_id=thread_id,
        transport=transport,
    )


def save_restart_wake(config, wake: RestartWake) -> Path:
    """Write a wake record atomically under ``data/{agent_id}/restart_wakes/``."""
    path = restart_wake_path(config, wake.conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(wake), indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


def delete_restart_wake(config, conv_id: str) -> None:
    """Remove a pending record (used when an on-idle restart is cancelled)."""
    path = restart_wake_path(config, conv_id)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Failed to remove restart wake record %s: %s", path, exc)


def consume_restart_wakes(config) -> list[RestartWake]:
    """Read and unlink every pending record, returning the deliverable ones.

    Each file is unlinked before its contents are used, so a corrupt record or
    a failing wake turn cannot replay on a later restart. Unreadable or
    non-removable files are skipped rather than retried forever.
    """
    wakes_dir = (config.agent_path / "restart_wakes").resolve()
    if not wakes_dir.exists():
        return []

    records: list[RestartWake] = []
    try:
        files = sorted(wakes_dir.glob("*.json"))
    except OSError as exc:
        log.warning("Failed to list restart_wakes dir for scan: %s", exc)
        return []

    for path in files:
        try:
            raw = path.read_text(encoding="utf-8")
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            # Leaving the file while also enqueuing would repeat the wake on
            # every startup; skip it instead of entering a wake loop.
            log.warning("Failed to consume restart wake record %s: %s", path, exc)
            continue

        try:
            records.append(RestartWake.from_dict(json.loads(raw)))
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("Discarding corrupt restart wake record %s: %s", path, exc)

    return records


def build_restart_wake_prompt(record: RestartWake, *, now: datetime | None = None) -> str:
    """Compose the wake prompt shown to the agent after a restart."""
    elapsed = 0.0
    try:
        requested = datetime.fromisoformat(record.requested_at)
        if requested.tzinfo is None:
            requested = requested.replace(tzinfo=timezone.utc)
        elapsed = max(0.0, ((now or datetime.now(timezone.utc)) - requested).total_seconds())
    except ValueError:
        pass
    reason = record.reason or "admin requested restart"
    return (
        f"Post-restart wake: DecafClaw is back online after restarting "
        f"(reason: {reason!r}; elapsed: {elapsed:.1f}s). "
        "Verify the restart succeeded — check health, MCP connections, or the "
        "change you made — then report the result to the user."
    )
