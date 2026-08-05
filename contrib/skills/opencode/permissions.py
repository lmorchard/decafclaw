"""Permission bridge — handles upfront confirmation for OpenCode tool use."""

import fnmatch
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def _allowlist_path(config) -> Path:
    """Path to the OpenCode allow patterns file (admin-managed)."""
    return config.agent_path / "opencode_allow_patterns.json"


def load_allowlist(config) -> list[str]:
    """Load allow patterns from disk. Returns [] if missing or corrupt."""
    path = _allowlist_path(config)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        return data.get("patterns", [])
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Could not read OpenCode allow patterns: {e}")
        return []


def save_allowlist_entry(config, pattern: str) -> None:
    """Add a pattern to the allowlist."""
    path = _allowlist_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    patterns = load_allowlist(config)
    if pattern not in patterns:
        patterns.append(pattern)
        path.write_text(json.dumps(patterns, indent=2) + "\n")
        log.info(f"Added OpenCode allow pattern: {pattern}")


def matches_allowlist(tool_name: str, patterns: list[str]) -> bool:
    """Check if a tool name matches any allow pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(tool_name, pattern):
            return True
    return False
