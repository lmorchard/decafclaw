"""Watch the agent config file for changes and react (#149).

`decafclaw` loads its agent config from ``data/{agent_id}/config.json`` once at
startup. Issue #149 asks for a hybrid reload: watch that file, apply the
runtime-tunable sections in place, and restart the process for the sections
that need a full reload (MCP servers, HTTP server settings, Mattermost
credentials).

This module owns the watch loop. It diffs the freshly loaded config against a
snapshot taken when the watcher started, classifies the changed sections, and
either applies the safe ones onto the live config or signals a restart for the
unsafe ones.
"""

import asyncio
import hashlib
import logging
from pathlib import Path

from .config import load_config

log = logging.getLogger(__name__)

WATCH_INTERVAL_SEC = 1.0

# Top-level config.json sections that are safe to hot-apply without a restart
# (#149: model config, effort levels, tool budgets, prompt files, skill catalog).
SAFE_TOP_LEVEL_KEYS = frozenset(
    {
        "agent",
        "background",
        "cleanup",
        "compaction",
        "embedding",
        "heartbeat",
        "importance",
        "llm",
        "modes",
        "notes",
        "pre_script",
        "relevance",
        "recent_journal",
        "reflections",
        "schedules",
        "telemetry",
        "vault",
        "vault_guide",
        "vault_retrieval",
        "workflow",
    }
)

# Sections that require a process restart to apply (#149).
UNSAFE_TOP_LEVEL_KEYS = frozenset(
    {
        "http",
        "mattermost",
    }
)

MCP_SERVERS_FILENAME = "mcp_servers.json"
ALL_TOP_LEVEL_KEYS = sorted(SAFE_TOP_LEVEL_KEYS | UNSAFE_TOP_LEVEL_KEYS)


def _file_signature(path: Path) -> tuple | None:
    """Cheap content hash of a file; None when missing/unreadable."""
    try:
        data = path.read_bytes()
        return (len(data), hashlib.sha256(data).hexdigest())
    except OSError:
        return None


async def config_change_watcher(app_ctx, shutdown_event, interval: float = WATCH_INTERVAL_SEC) -> None:
    """Watch the agent config file; apply safe changes, restart for unsafe ones."""
    config = app_ctx.config
    config_path = Path(config.agent_path) / "config.json"
    mcp_path = Path(config.agent_path) / MCP_SERVERS_FILENAME

    # Snapshot of the runtime-tunable sections at watcher start; the freshly
    # loaded config is diffed against this so a change means "this section moved".
    snapshot = {key: getattr(config, key, None) for key in ALL_TOP_LEVEL_KEYS}

    last_signature = _file_signature(config_path)
    last_mcp_signature = _file_signature(mcp_path)

    while not shutdown_event.is_set():
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise

        if shutdown_event.is_set():
            break

        signature = _file_signature(config_path)
        mcp_signature = _file_signature(mcp_path)
        if signature == last_signature and mcp_signature == last_mcp_signature:
            continue

        if signature != last_signature:
            last_signature = signature
        if mcp_signature != last_mcp_signature:
            last_mcp_signature = mcp_signature

        new_config = load_config()
        changed_keys = [key for key in ALL_TOP_LEVEL_KEYS if snapshot.get(key) != getattr(new_config, key, None)]

        if not changed_keys:
            continue

        unsafe_changed = [key for key in changed_keys if key in UNSAFE_TOP_LEVEL_KEYS]
        safe_changed = [key for key in changed_keys if key not in UNSAFE_TOP_LEVEL_KEYS]

        if unsafe_changed:
            # A restart-required section changed: stop the loop and let the
            # process restart rather than mutating live state we cannot safely swap.
            log.info("Config change requires restart: %s", ", ".join(unsafe_changed))
            await app_ctx.event_bus.publish(
                {"type": "config_changed", "requires_restart": True, "keys": unsafe_changed}
            )
            shutdown_event.set()
            return

        # Safe sections changed: apply them onto the live config object so the
        # next agent turn picks them up without a process restart.
        applied: list[str] = []
        if "llm" in safe_changed and new_config.llm != config.llm:
            config.llm = new_config.llm
            applied.append("llm")
        if "agent" in safe_changed and new_config.agent != config.agent:
            config.agent = new_config.agent
            applied.append("agent")
        if "compaction" in safe_changed and new_config.compaction != config.compaction:
            config.compaction = new_config.compaction
            applied.append("compaction")

        await app_ctx.event_bus.publish(
            {"type": "config_changed", "requires_restart": False, "keys": applied or safe_changed},
        )
        log.info("Config change applied in place: %s", ", ".join(applied or safe_changed))
