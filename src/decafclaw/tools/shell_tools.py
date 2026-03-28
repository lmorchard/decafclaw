"""Shell tool with confirmation — requires user approval before execution."""

import fnmatch
import json
import logging
import subprocess
from pathlib import Path

from ..media import ToolResult
from .confirmation import request_confirmation

log = logging.getLogger(__name__)


def _allow_patterns_path(config) -> Path:
    """Path to the shell allow patterns file (outside workspace, admin-managed)."""
    return config.agent_path / "shell_allow_patterns.json"


def _load_allow_patterns(config) -> list[str]:
    """Load shell allow patterns from disk. Returns [] if missing or corrupt."""
    path = _allow_patterns_path(config)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        return data.get("patterns", [])
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Could not read shell allow patterns: {e}")
        return []


def _save_allow_pattern(config, pattern: str) -> None:
    """Add a pattern to the allow list. Called by host-side confirmation handler."""
    path = _allow_patterns_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    patterns = _load_allow_patterns(config)
    if pattern not in patterns:
        patterns.append(pattern)
        path.write_text(json.dumps(patterns, indent=2) + "\n")
        log.info(f"Added shell allow pattern: {pattern}")


def _command_matches_pattern(command: str, patterns: list[str]) -> bool:
    """Check if a command matches any allow pattern (glob-style)."""
    for pattern in patterns:
        if fnmatch.fnmatch(command, pattern):
            return True
    return False


# Shell metacharacters that could chain additional commands
_SHELL_CHAIN_TOKENS = (";", "&&", "||", "|", "`", "$(", "\n")


def _has_shell_metacharacters(command: str) -> bool:
    """Check if a command contains shell chaining/injection tokens."""
    return any(tok in command for tok in _SHELL_CHAIN_TOKENS)


def _suggest_pattern(command: str) -> str:
    """Generate a suggested allow pattern from a command.

    Heuristic: keep the executable and script/subcommand path, wildcard the args.
    """
    parts = command.strip().split()
    if not parts:
        return command

    # For commands like "python script.py --args", keep "python script.py *"
    # For commands like "git status", keep "git status"
    # For commands like "make test", keep "make test"

    exe = parts[0]

    # If second part looks like a file path or subcommand, keep it
    if len(parts) >= 2:
        second = parts[1]
        # Keep the second part if it looks like a path or known subcommand
        if "/" in second or "." in second:
            # Executable + script path + wildcard
            if len(parts) > 2:
                return f"{exe} {second} *"
            return f"{exe} {second}"
        # For commands like "git status", "make test" — keep as-is if short
        if len(parts) <= 2:
            return command
        # For "git diff HEAD~1" etc — keep subcommand, wildcard rest
        return f"{exe} {second} *"

    return command


async def tool_shell(ctx, command: str) -> str | ToolResult:
    """Run a shell command after user confirmation."""
    log.info(f"[tool:shell] requesting confirmation for: {command}")

    # Admin heartbeat turns auto-approve shell commands (admin-authored prompts)
    is_heartbeat = ctx.user_id == "heartbeat-admin"
    if is_heartbeat:
        log.info(f"[tool:shell] auto-approved for heartbeat: {command}")
        return _execute_command(ctx, command)

    # Command pre-approved tools bypass confirmation (blanket shell approval)
    if "shell" in ctx.tools.preapproved:
        log.info(f"[tool:shell] pre-approved by command: {command}")
        return _execute_command(ctx, command)

    # Scoped shell patterns from skill frontmatter (e.g. shell($SKILL_DIR/fetch.sh))
    # Reject commands with shell chaining/metacharacters to prevent bypass
    if ctx.tools.preapproved_shell_patterns and not _has_shell_metacharacters(
        command
    ) and _command_matches_pattern(command, ctx.tools.preapproved_shell_patterns):
        log.info(f"[tool:shell] pre-approved by scoped pattern: {command}")
        return _execute_command(ctx, command)

    # Check allow patterns
    patterns = _load_allow_patterns(ctx.config)
    if _command_matches_pattern(command, patterns):
        log.info(f"[tool:shell] auto-approved by pattern: {command}")
        return _execute_command(ctx, command)

    # Generate suggested pattern for the confirmation message
    suggested_pattern = _suggest_pattern(command)

    result = await request_confirmation(
        ctx, tool_name="shell", command=command,
        message=f"Shell command: `{command}`",
        suggested_pattern=suggested_pattern,
    )

    if not result.get("approved"):
        log.info(f"[tool:shell] command denied: {command}")
        return ToolResult(text="[error: shell command was denied by user]")

    # If user chose to add the pattern, save it
    if result.get("add_pattern"):
        _save_allow_pattern(ctx.config, suggested_pattern)

    return _execute_command(ctx, command)


def _execute_command(ctx, command: str) -> str | ToolResult:
    """Execute a shell command and return the output."""
    log.info(f"[tool:shell] executing command: {command}")
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30,
            cwd=str(ctx.config.workspace_path),
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return ToolResult(text="[error: command timed out after 30 seconds]")


async def tool_shell_patterns(ctx, action: str = "list", pattern: str = "") -> str | ToolResult:
    """Manage shell command allow patterns."""
    log.info(f"[tool:shell_patterns] action={action} pattern={pattern}")

    if action == "list":
        patterns = _load_allow_patterns(ctx.config)
        if not patterns:
            return "No shell allow patterns configured."
        lines = ["**Shell allow patterns:**\n"]
        for p in patterns:
            lines.append(f"- `{p}`")
        return "\n".join(lines)

    elif action == "add" and pattern:
        # This requires confirmation — we're modifying admin config
        result = await request_confirmation(
            ctx, tool_name="shell_patterns",
            command=f"Add shell allow pattern: {pattern}",
            message=f"Add shell allow pattern: `{pattern}`",
        )

        if not result.get("approved"):
            return ToolResult(text="[error: denied]")

        _save_allow_pattern(ctx.config, pattern)
        return f"Added shell allow pattern: `{pattern}`"

    elif action == "remove" and pattern:
        patterns = _load_allow_patterns(ctx.config)
        if pattern not in patterns:
            return f"Pattern `{pattern}` not found."
        patterns.remove(pattern)
        path = _allow_patterns_path(ctx.config)
        path.write_text(json.dumps(patterns, indent=2) + "\n")
        return f"Removed shell allow pattern: `{pattern}`"

    return ToolResult(text="[error: invalid action. Use 'list', 'add', or 'remove'.]")


SHELL_TOOLS = {
    "shell": tool_shell,
    "shell_patterns": tool_shell_patterns,
}

SHELL_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "shell_patterns",
            "description": (
                "Manage shell command allow patterns. Patterns auto-approve matching "
                "shell commands without confirmation. Use 'list' to see current patterns, "
                "'add' to add a new pattern (requires confirmation), 'remove' to remove one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "add", "remove"],
                        "description": "Action to perform (default: list)",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (for add/remove). Example: 'python scripts/*.py *'",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": (
                "Run a shell command. REQUIRES USER CONFIRMATION before execution "
                "unless the command matches an admin-configured allow pattern. "
                "The command runs in the workspace directory. Use for tasks that "
                "need system interaction: checking disk space, running scripts, "
                "installing packages, etc. The user will see the command and must "
                "approve it before it runs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                },
                "required": ["command"],
            },
        },
    },
]
