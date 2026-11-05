"""Watch the agent config files for changes and react (#149).

`decafclaw` loads its agent config from ``data/{agent_id}/config.json`` once at
startup. Issue #149 asks for a hybrid reload: watch the config files, apply the
runtime-tunable sections in place, and restart the process for the sections
that need a full reload (HTTP server settings, Mattermost credentials, MCP
servers).

This module owns the watch loop. It polls a content-hash signature of the
config files, reloads with ``load_config()`` on a detected change, diffs the
fresh config against the live one, and either applies the safe sections onto
the live config or signals a restart. The restart-required key set is shared
with the ``make dev`` watchfiles filter (``_dev_filter.DevFilter``), which
restarts the supervised dev server on exactly the same changes.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import fields as dataclass_fields
from pathlib import Path

from .config import Config, load_config

log = logging.getLogger(__name__)

WATCH_INTERVAL_SEC = 1.0

# Grace delay before the watcher stops the process for a restart-required
# change. Gives a supervisor (make dev's watchfiles) time to notice the same
# file change and restart us first; a bare `make run` just exits slightly
# later. Injectable so tests can shrink it.
RESTART_GRACE_SEC = 2.0

# Config sections that require a process restart to apply (#149). Consumed by
# _dev_filter.DevFilter, which must draw the same line for supervised restarts.
RESTART_REQUIRED_CONFIG_KEYS = frozenset({"http", "mattermost"})

# mcp_servers.json is a separate file, loaded outside the Config dataclass by
# mcp_client.init_mcp at startup — any change to it requires a restart too.
MCP_SERVERS_FILENAME = "mcp_servers.json"
MCP_SERVERS_KEY = "mcp_servers"

# All restart-classified inputs, for the watcher's classification step.
UNSAFE_TOP_LEVEL_KEYS = frozenset(RESTART_REQUIRED_CONFIG_KEYS | {MCP_SERVERS_KEY})


def watched_field_names() -> list[str]:
    """Config fields that come from config.json.

    Runtime-only fields (marked ``metadata={"runtime_only": True}``) are
    excluded: a fresh load always differs from the live values there, and
    applying them would clobber in-memory state like the assembled system
    prompt or discovered skills.
    """
    return [f.name for f in dataclass_fields(Config) if not f.metadata.get("runtime_only", False)]


def _file_signature(path: Path) -> tuple | None:
    """Cheap content hash of a file; None when missing/unreadable."""
    try:
        data = path.read_bytes()
        return (len(data), hashlib.sha256(data).hexdigest())
    except OSError:
        return None


def _file_parses(path: Path) -> bool:
    """True when the file exists and parses as JSON."""
    try:
        json.loads(path.read_text())
        return True
    except (json.JSONDecodeError, OSError):
        return False


class ConfigFileClassifier:
    """Classify config-file edits as restart-required or not.

    Shared by the ``make dev`` watchfiles filter so the supervised dev server
    restarts on exactly the changes the in-process watcher would restart for.
    Diffs raw JSON sections — no Config construction, no env coupling; the
    in-process watcher's Config-level diff stays authoritative.
    """

    def __init__(self) -> None:
        self._last: dict[str, dict] = {}

    def is_restart_required(self, path: Path) -> bool:
        """True when the edit at ``path`` touches a restart-required section."""
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            # Torn/partial write: skip it (the in-process watcher does too).
            return False
        except OSError:
            # Missing or unreadable: treat as a change worth restarting for.
            return True
        if not isinstance(data, dict):
            return False
        name = path.name
        last = self._last.get(name)
        self._last[name] = data
        if last is None:
            # First sighting after dev-server start: no known delta.
            return False
        if name == MCP_SERVERS_FILENAME:
            return True
        return any(data.get(key) != last.get(key) for key in RESTART_REQUIRED_CONFIG_KEYS)


async def config_change_watcher(
    app_ctx,
    shutdown_event: asyncio.Event,
    interval: float = WATCH_INTERVAL_SEC,
    restart_grace_sec: float = RESTART_GRACE_SEC,
) -> None:
    """Watch the agent config files; apply safe changes, signal restart for the rest."""
    config = app_ctx.config
    config_path = Path(config.agent_path) / "config.json"
    mcp_path = Path(config.agent_path) / MCP_SERVERS_FILENAME
    field_names = watched_field_names()

    last_config_sig = _file_signature(config_path)
    last_mcp_sig = _file_signature(mcp_path)

    while not shutdown_event.is_set():
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise

        if shutdown_event.is_set():
            break

        config_sig = _file_signature(config_path)
        mcp_sig = _file_signature(mcp_path)
        config_changed = config_sig != last_config_sig
        mcp_changed = mcp_sig != last_mcp_sig
        if not config_changed and not mcp_changed:
            continue

        # A torn or partial write can leave unparseable JSON, and load_config
        # would silently fall back to dataclass defaults — never apply those.
        # Skip the poll and keep the old signature so the completed write is
        # still seen as a change on a later poll.
        if config_changed and config_sig is not None and not _file_parses(config_path):
            log.warning("config.json changed but does not parse; skipping reload")
            config_changed = False
        if mcp_changed and mcp_sig is not None and not _file_parses(mcp_path):
            log.warning("mcp_servers.json changed but does not parse; skipping reload")
            mcp_changed = False
        if not config_changed and not mcp_changed:
            continue
        last_config_sig, last_mcp_sig = config_sig, mcp_sig

        new_config = load_config()
        changed = [name for name in field_names if getattr(new_config, name) != getattr(config, name)]
        if mcp_changed:
            changed.append(MCP_SERVERS_KEY)
        if not changed:
            continue

        unsafe_changed = [key for key in changed if key in UNSAFE_TOP_LEVEL_KEYS]
        if unsafe_changed:
            # A restart-required section changed: announce it and stop the
            # process after a short grace so a supervisor can restart us.
            log.info("Config change requires restart: %s", ", ".join(unsafe_changed))
            await app_ctx.event_bus.publish(
                {"type": "config_changed", "requires_restart": True, "keys": unsafe_changed}
            )
            try:
                await asyncio.sleep(restart_grace_sec)
            except asyncio.CancelledError:
                raise
            shutdown_event.set()
            return

        # Safe sections changed: apply them onto the live config so the next
        # agent turn picks them up without a process restart.
        for name in changed:
            setattr(config, name, getattr(new_config, name))
        if "env" in changed:
            config.apply_env()
        await app_ctx.event_bus.publish({"type": "config_changed", "requires_restart": False, "keys": changed})
        log.info("Config change applied in place: %s", ", ".join(changed))
