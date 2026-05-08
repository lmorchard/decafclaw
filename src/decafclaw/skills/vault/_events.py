"""EventBus publisher for vault mutations.

Vault tools and REST handlers call publish_vault_changed() after a successful
disk operation. The web UI's per-connection subscriber (in web/websocket.py)
fans the event out as a `vault_changed` WebSocket message to every connected
client. Mirrors the notification forwarder pattern.

Fail-open: a publish failure must never break the calling tool or handler.
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

VAULT_CHANGED_EVENT_TYPE = "vault_changed"

# Allowed `kind` values. Advisory only — clients always re-fetch regardless.
KIND_CREATE = "create"
KIND_UPDATE = "update"
KIND_DELETE = "delete"
KIND_RENAME = "rename"
KIND_MOVE = "move"
KIND_SECTION = "section"
KIND_JOURNAL = "journal"


async def publish_vault_changed(
    event_bus,
    config,
    kind: str,
    path: str | Path | None = None,
) -> None:
    """Publish a vault_changed event for the web UI to broadcast.

    Args:
        event_bus: The shared EventBus (ctx.event_bus or
            request.app.state.event_bus). May be None — in which case this
            is a no-op (used during tests or contexts without a bus).
        config: The runtime config; used to resolve `path` against
            `config.vault_root` for relative-path normalization.
        kind: One of KIND_CREATE/UPDATE/DELETE/RENAME/MOVE/SECTION/JOURNAL.
        path: The changed file's path (str, Path, or None). Absolute paths
            are normalized to vault-relative; None becomes "" in the
            payload (used for multi-file ops where there's no single path).

    Fail-open: any exception is logged at debug level and swallowed.
    """
    if event_bus is None:
        return
    rel = ""
    if path is not None:
        try:
            p = Path(path)
            if p.is_absolute():
                rel = str(p.resolve().relative_to(config.vault_root.resolve()))
            else:
                rel = str(p)
            rel = rel.replace("\\", "/")
        except (ValueError, OSError) as exc:
            log.debug("vault_changed path normalize failed for %r: %s", path, exc)
            rel = ""
    try:
        await event_bus.publish({
            "type": VAULT_CHANGED_EVENT_TYPE,
            "kind": kind,
            "path": rel,
        })
    except Exception as exc:
        log.debug("vault_changed publish failed: %s", exc)
