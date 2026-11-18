"""Context sidecar persistence for the Context Inspector (#1491).

Writers are fail-open; readers return ``None`` for a missing or unreadable
file. ``read_last_request_sidecar`` reads the payload written by
``extensions.diagnostics_and_telemetry``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .conversation_paths import sidecar_path

log = logging.getLogger(__name__)


# -- Sidecar persistence ------------------------------------------------------


def _context_sidecar_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "context.json")


def write_context_sidecar(config, conv_id: str, diagnostics: dict) -> None:
    """Write context diagnostics to the sidecar file. Fail-open."""
    try:
        path = _context_sidecar_path(config, conv_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(diagnostics, indent=2, default=str))
    except Exception:
        log.warning("Failed to write context sidecar for %s", conv_id, exc_info=True)


def read_context_sidecar(config, conv_id: str) -> dict | None:
    """Read context diagnostics from the sidecar file. Returns None if missing."""
    try:
        path = _context_sidecar_path(config, conv_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())
    except Exception:
        log.warning("Failed to read context sidecar for %s", conv_id, exc_info=True)
        return None


def read_last_request_sidecar(config, conv_id: str) -> dict | None:
    """Read the last LLM request payload from the sidecar file. Returns None if missing.

    The payload is written by ``extensions.diagnostics_and_telemetry``; the
    reader stays here for the Context Inspector REST route.
    """
    if not conv_id:
        return None
    try:
        from .conversation_paths import last_request_path

        path = last_request_path(config, conv_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())
    except Exception:
        log.debug("Failed to read last request sidecar for %s", conv_id, exc_info=True)
        return None
