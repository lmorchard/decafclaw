"""Pre-execution security monitor for tool calls.

Evaluates shell commands before execution to classify them as ALLOW, BLOCK, or ASK.
"""

import logging
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)


class SecurityStatus(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    ASK = "ASK"


@dataclass(frozen=True)
class SecurityDecision:
    status: SecurityStatus
    reason: str = ""

    @property
    def is_blocked(self) -> bool:
        return self.status == SecurityStatus.BLOCK

    @property
    def is_allowed(self) -> bool:
        return self.status == SecurityStatus.ALLOW


# Known-dangerous command patterns (regexes)
DANGEROUS_PATTERNS = [
    (
        re.compile(r"\brm\s+.*-(?:[a-zA-Z]*r[a-zA-Z]*f|[a-zA-Z]*f[a-zA-Z]*r)\b"),
        "Dangerous recursive force deletion (rm -rf)",
    ),
    (
        re.compile(r"\brm\s+.*-(?:[a-zA-Z]*r|--recursive)\b"),
        "Dangerous recursive deletion (rm -r)",
    ),
    (
        re.compile(r"\bgit\s+push\s+.*(?:--force|-f)\b"),
        "Dangerous forced git push (git push --force)",
    ),
    (
        re.compile(r"\bcurl\s+.*(?:-X\s*POST|--request\s+POST|-XPOST)\b", re.IGNORECASE),
        "External HTTP POST request via curl",
    ),
    (
        re.compile(r"\bwget\s+.*--post-(?:data|file)\b", re.IGNORECASE),
        "External HTTP POST request via wget",
    ),
    (
        re.compile(r"\bchmod\b"),
        "System permission modification (chmod)",
    ),
    (
        re.compile(r"\bchown\b"),
        "System ownership modification (chown)",
    ),
    (
        re.compile(r"\b(?:kill|pkill|killall)\b"),
        "Process termination command (kill)",
    ),
]

# Standard system directories containing binaries or special devices that are allowed at index 0 or as devices
SYSTEM_EXEC_DIRS = {
    Path("/bin"),
    Path("/usr/bin"),
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
    Path("/usr/libexec"),
    Path("/sbin"),
    Path("/usr/sbin"),
    Path("/System"),
}

ALLOWED_DEVICES = {
    Path("/dev/null"),
    Path("/dev/stdout"),
    Path("/dev/stderr"),
    Path("/dev/zero"),
}


def evaluate_command(
    command: str,
    workspace_path: str | Path | None = None,
    is_autonomous: bool = False,
    is_child_agent: bool = False,
) -> SecurityDecision:
    """Evaluate a shell command and return a SecurityDecision."""
    if not command or not command.strip():
        return SecurityDecision(SecurityStatus.ALLOW, "Empty command")

    # Tier 1: Check known-dangerous command patterns
    for pattern, reason in DANGEROUS_PATTERNS:
        if pattern.search(command):
            log.warning(f"[security_monitor] BLOCKED dangerous pattern ({reason}): {command}")
            return SecurityDecision(SecurityStatus.BLOCK, f"Command contains blocked pattern: {reason}")

    # Tier 1: Check out-of-workspace file operations
    resolved_workspace = Path(workspace_path or Path.cwd()).resolve()

    try:
        tokens = shlex.split(command, posix=True)
    except Exception:
        # Fallback to simple whitespace split if shlex fails on unclosed quotes
        tokens = command.split()

    for idx, token in enumerate(tokens):
        # Ignore options/flags
        if token.startswith("-") and not token.startswith("-/") and not token.startswith("-~"):
            continue

        # Check if token represents a path
        if token.startswith("/") or token.startswith("~") or ".." in token:
            try:
                if token.startswith("~"):
                    token_path = Path(token).expanduser().resolve()
                elif token.startswith("/"):
                    token_path = Path(token).resolve()
                    # If absolute path does not exist on system (e.g. /skills/...), check if virtual relative path in workspace
                    if not token_path.exists() and not token_path.parent.exists():
                        rel_in_ws = (resolved_workspace / token.lstrip("/")).resolve()
                        rel_in_parent = (resolved_workspace.parent / token.lstrip("/")).resolve()
                        if rel_in_ws.is_relative_to(resolved_workspace) or rel_in_parent.is_relative_to(resolved_workspace.parent):
                            continue
                else:
                    token_path = (resolved_workspace / token).resolve()

                # Allowed devices
                if token_path in ALLOWED_DEVICES:
                    continue

                # If token is the executable command (idx 0), allow system binaries
                if idx == 0 and any(token_path.is_relative_to(sys_dir) for sys_dir in SYSTEM_EXEC_DIRS):
                    continue

                # Check if path is within workspace
                if not token_path.is_relative_to(resolved_workspace):
                    log.warning(
                        f"[security_monitor] BLOCKED out-of-workspace path: {token} "
                        f"(resolved: {token_path}, workspace: {resolved_workspace})"
                    )
                    return SecurityDecision(
                        SecurityStatus.BLOCK,
                        f"Attempted operation outside workspace path: {token}",
                    )
            except Exception as exc:
                log.debug(f"[security_monitor] Error evaluating path token {token!r}: {exc}")

    return SecurityDecision(SecurityStatus.ALLOW, "Command passed security monitor checks")
