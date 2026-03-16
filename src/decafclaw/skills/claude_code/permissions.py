"""Permission bridge — routes Claude Code tool approvals through DecafClaw's confirmation flow."""

import fnmatch
import json
import logging
from pathlib import Path

from claude_code_sdk import PermissionResultAllow, PermissionResultDeny

log = logging.getLogger(__name__)

# Tools that are always auto-approved (read-only, no side effects)
AUTO_APPROVE_TOOLS = frozenset({
    "Read", "Glob", "Grep", "WebSearch", "WebFetch",
})


def _allowlist_path(config) -> Path:
    """Path to the Claude Code allow patterns file (admin-managed)."""
    return config.agent_path / "claude_code_allow_patterns.json"


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
        log.warning(f"Could not read Claude Code allow patterns: {e}")
        return []


def save_allowlist_entry(config, pattern: str) -> None:
    """Add a pattern to the allowlist."""
    path = _allowlist_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    patterns = load_allowlist(config)
    if pattern not in patterns:
        patterns.append(pattern)
        path.write_text(json.dumps(patterns, indent=2) + "\n")
        log.info(f"Added Claude Code allow pattern: {pattern}")


def matches_allowlist(tool_name: str, patterns: list[str]) -> bool:
    """Check if a tool name matches any allow pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(tool_name, pattern):
            return True
    return False


def make_permission_handler(ctx, config):
    """Create a can_use_tool callback that bridges to DecafClaw's confirmation flow.

    Returns an async function compatible with ClaudeCodeOptions.can_use_tool.
    Signature: async (tool_name, tool_input, context) -> PermissionResultAllow | PermissionResultDeny
    """
    from decafclaw.tools.confirmation import request_confirmation

    async def can_use_tool(tool_name: str, tool_input: dict, tool_context) -> PermissionResultAllow | PermissionResultDeny:
        log.info(f"Claude Code permission check: {tool_name}")

        # Auto-approve read-only tools
        if tool_name in AUTO_APPROVE_TOOLS:
            log.info(f"Claude Code auto-approved (read-only): {tool_name}")
            return PermissionResultAllow()

        # Check allowlist
        patterns = load_allowlist(config)
        if matches_allowlist(tool_name, patterns):
            log.info(f"Claude Code auto-approved (allowlist): {tool_name}")
            return PermissionResultAllow()

        log.info(f"Claude Code requesting confirmation for: {tool_name}")

        # TODO: Route through Mattermost confirmation flow once the async
        # bridge timing issue with the SDK control protocol is resolved.
        # The request_confirmation() call works in-process, but the SDK's
        # control protocol disconnects ("Stream closed") before the
        # Mattermost reaction poll can complete (~2s polling interval
        # vs SDK's tight timeout).
        #
        # Future fix options:
        # 1. Find SDK timeout config for control protocol responses
        # 2. Use a faster confirmation mechanism (not reaction polling)
        # 3. Pre-approve patterns and only confirm novel commands
        #
        # For now, auto-approve and log.
        log.warning(f"Claude Code auto-approving {tool_name} (confirmation bridge WIP)")
        await ctx.publish(
            "tool_status", tool="claude_code",
            message=f"Auto-approved: {tool_name}",
        )
        return PermissionResultAllow()

    return can_use_tool
