"""Native SSH tools (#1750).

`ssh_exec` and `ssh_copy` replace ad-hoc `shell("ssh ...")` calls with
non-interactive, host-resolved operations. Hosts must be aliases in
``~/.ssh/config``; raw IPs and unknown aliases require an explicit
confirmation before the model can retry with ``allow_unlisted_host=True``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from ..media import EndTurnConfirm, ToolResult
from ..ssh_rules import decide as ssh_rule_decision

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

_SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=5")


def _ssh_config_hosts(path: Path | None = None) -> set[str]:
    """Return concrete Host aliases from an OpenSSH config file."""
    config_path = path or (Path.home() / ".ssh" / "config")
    hosts: set[str] = set()
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return hosts
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = shlex.split(stripped, comments=True)
        if len(parts) < 2 or parts[0].lower() != "host":
            continue
        for alias in parts[1:]:
            if any(ch in alias for ch in "*?[]!"):
                continue
            hosts.add(alias)
    return hosts


def _is_raw_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _host_allowed(host: str, hosts: set[str] | None = None) -> bool:
    return host in (hosts if hosts is not None else _ssh_config_hosts()) and not _is_raw_ip(host)


def _confirmation(host: str) -> EndTurnConfirm:
    return EndTurnConfirm(
        message=(
            f"**Unlisted SSH host `{host}`**\n\n"
            f"`{host}` is not a concrete alias in `~/.ssh/config`. "
            "Add it there, or approve to allow this host for this call and "
            "retry with `allow_unlisted_host=True`."
        ),
        approve_label="Allow this host",
        deny_label="Cancel",
    )


async def _run_process(args: list[str], timeout: float) -> tuple[int | None, str, str, bool]:
    """Run a subprocess with a bounded wait; return (code, stdout, stderr, timed_out)."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return process.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace"), False
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return None, "", f"timed out after {timeout}s", True


async def tool_ssh_exec(
    ctx: "Context",
    host: str,
    command: str,
    timeout: float = 30,
    allow_unlisted_host: bool = False,
) -> ToolResult:
    """Run a non-interactive command on an SSH host alias."""
    host = str(host or "").strip()
    command = str(command or "").strip()
    if not host:
        return ToolResult(text="[error: host is required]")
    if not command:
        return ToolResult(text="[error: command is required]")
    rule = ssh_rule_decision(ctx.config, "ssh_exec", host)
    if rule == "deny":
        return ToolResult(
            text=f"[error: host '{host}' is denied by the SSH rulebook]",
            data={"error": "ssh_rule_denied", "host": host},
        )
    if rule != "allow" and not allow_unlisted_host and not _host_allowed(host):
        return ToolResult(
            text=(
                f"[error: host '{host}' is not a concrete alias in ~/.ssh/config. "
                "Add it there or approve and retry with allow_unlisted_host=True.]"
            ),
            end_turn=_confirmation(host),
            data={"error": "unlisted_host", "host": host},
        )

    timeout = max(1.0, min(float(timeout), 300.0))
    args = ["ssh", *_SSH_OPTIONS, host, command]
    try:
        code, stdout, stderr, timed_out = await _run_process(args, timeout)
    except Exception as exc:  # noqa: BLE001 - spawn failure is a tool error
        return ToolResult(text=f"[error: ssh failed to start: {exc}]", data={"error": str(exc), "host": host})
    if timed_out:
        return ToolResult(
            text=f"[error: ssh command timed out after {timeout:g}s on {host}]",
            data={"host": host, "exit_code": None, "stdout": stdout, "stderr": stderr, "timed_out": True},
        )
    text = stdout
    if stderr:
        text = f"{text}\n[stderr]\n{stderr}" if text else stderr
    if code != 0:
        text = f"[error: ssh exited {code} on {host}]\n{text}" if text else f"[error: ssh exited {code} on {host}]"
    return ToolResult(
        text=text or "(no output)",
        data={"host": host, "exit_code": code, "stdout": stdout, "stderr": stderr},
    )


async def tool_ssh_copy(
    ctx: "Context",
    host: str,
    local_path: str,
    remote_path: str,
    direction: str = "up",
    timeout: float = 60,
    allow_unlisted_host: bool = False,
) -> ToolResult:
    """Copy one file to or from an SSH host with scp."""
    host = str(host or "").strip()
    local_path = str(local_path or "").strip()
    remote_path = str(remote_path or "").strip()
    direction = str(direction or "up").strip().lower()
    if direction not in ("up", "down"):
        return ToolResult(text="[error: direction must be 'up' or 'down']")
    if not host or not local_path or not remote_path:
        return ToolResult(text="[error: host, local_path, and remote_path are required]")
    rule = ssh_rule_decision(ctx.config, "ssh_copy", host)
    if rule == "deny":
        return ToolResult(
            text=f"[error: host '{host}' is denied by the SSH rulebook]",
            data={"error": "ssh_rule_denied", "host": host},
        )
    if rule != "allow" and not allow_unlisted_host and not _host_allowed(host):
        return ToolResult(
            text=(
                f"[error: host '{host}' is not a concrete alias in ~/.ssh/config. "
                "Add it there or approve and retry with allow_unlisted_host=True.]"
            ),
            end_turn=_confirmation(host),
            data={"error": "unlisted_host", "host": host},
        )

    workspace = Path(ctx.config.workspace_path).resolve()
    local = Path(local_path).expanduser()
    if not local.is_absolute():
        local = workspace / local
    local = local.resolve()
    if not local.is_relative_to(workspace):
        return ToolResult(text="[error: local_path must be inside the workspace]")
    timeout = max(1.0, min(float(timeout), 300.0))
    source, target = (
        (str(local), f"{host}:{remote_path}") if direction == "up" else (f"{host}:{remote_path}", str(local))
    )
    args = ["scp", *_SSH_OPTIONS, source, target]
    try:
        code, stdout, stderr, timed_out = await _run_process(args, timeout)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(text=f"[error: scp failed to start: {exc}]", data={"error": str(exc), "host": host})
    if timed_out:
        return ToolResult(
            text=f"[error: scp timed out after {timeout:g}s on {host}]",
            data={"host": host, "exit_code": None, "stdout": stdout, "stderr": stderr, "timed_out": True},
        )
    text = stdout
    if stderr:
        text = f"{text}\n[stderr]\n{stderr}" if text else stderr
    if code != 0:
        text = f"[error: scp exited {code} on {host}]\n{text}" if text else f"[error: scp exited {code} on {host}]"
    return ToolResult(
        text=text or "Copied.",
        data={"host": host, "exit_code": code, "stdout": stdout, "stderr": stderr, "direction": direction},
    )


SSH_TOOLS = {
    "ssh_exec": tool_ssh_exec,
    "ssh_copy": tool_ssh_copy,
}

SSH_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "summary": "Run a command on an SSH host alias.",
        "function": {
            "name": "ssh_exec",
            "description": (
                "Run a non-interactive command on a host alias from ~/.ssh/config. "
                "Uses BatchMode=yes and ConnectTimeout=5. Unknown aliases and raw IPs "
                "require confirmation; after approval retry with allow_unlisted_host=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "SSH config Host alias"},
                    "command": {"type": "string", "description": "Remote command"},
                    "timeout": {"type": "number", "description": "Seconds before the command is killed (default 30)"},
                    "allow_unlisted_host": {
                        "type": "boolean",
                        "description": "Set only after confirming an unlisted host",
                    },
                },
                "required": ["host", "command"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "summary": "Copy a file to or from an SSH host alias.",
        "function": {
            "name": "ssh_copy",
            "description": (
                "Copy one file with scp between the workspace and a host alias from "
                "~/.ssh/config. local_path must stay inside the workspace. Unknown "
                "aliases and raw IPs require confirmation; retry with allow_unlisted_host=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "SSH config Host alias"},
                    "local_path": {"type": "string", "description": "Workspace-relative local file"},
                    "remote_path": {"type": "string", "description": "Remote file path"},
                    "direction": {"type": "string", "enum": ["up", "down"], "description": "Upload or download"},
                    "timeout": {"type": "number", "description": "Seconds before the transfer is killed (default 60)"},
                    "allow_unlisted_host": {
                        "type": "boolean",
                        "description": "Set only after confirming an unlisted host",
                    },
                },
                "required": ["host", "local_path", "remote_path"],
            },
        },
    },
]
