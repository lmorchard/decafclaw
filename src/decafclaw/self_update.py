"""Human-only self-update command handler (#981).

Guarded update wrapping git pull, uv sync, and systemctl restart.
This module is human-only: it MUST NEVER be imported by agent tools or skills.
A test enforces this boundary.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)


@dataclass
class UpdateResult:
    ok: bool
    message: str
    restarted: bool = False


async def _default_cmd_runner(
    cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    """Execute a subprocess command asynchronously and return (exit_code, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


def find_repo_dir(start_path: Path | None = None) -> Path | None:
    """Find the root of the DecafClaw git repository from start_path."""
    current = (start_path or Path(__file__)).resolve()
    for parent in [current] + list(current.parents):
        if (parent / ".git").exists():
            return parent
    return None


def get_busy_conversations(manager: Any, index: Any | None = None) -> list[str]:
    """Return names/IDs of all conversations with an active turn."""
    if not manager:
        return []
    busy = []
    conversations = getattr(manager, "_conversations", {})
    for cid, state in conversations.items():
        is_busy = getattr(state, "busy", False)
        agent_task = getattr(state, "agent_task", None)
        has_task = agent_task is not None and not agent_task.done()
        if is_busy or has_task:
            title = None
            if index and hasattr(index, "get"):
                conv = index.get(cid)
                if conv and getattr(conv, "title", None):
                    title = conv.title
            name = f"'{title}' ({cid[:8]})" if title else cid[:8]
            busy.append(name)
    return busy


async def perform_self_update(
    *,
    confirmed: bool = False,
    manager: Any | None = None,
    index: Any | None = None,
    restart_coordinator: Any | None = None,
    repo_dir: Path | None = None,
    cmd_runner: Callable[[list[str], Path | None, dict[str, str] | None], Awaitable[tuple[int, str, str]]]
    | None = None,
) -> UpdateResult:
    """Run the guarded self-update workflow.

    1. Check for active turns across all conversations. If any are busy, refuse.
    2. Check working tree cleanliness (modified/staged tracked files). If dirty, abort.
    3. Check upstream tracking branch and fetch.
    4. Check if current branch can fast-forward to upstream. If diverged, abort.
    5. Inspect incoming commits (HEAD..@{u}). If none, stop ("Already up to date").
    6. If unconfirmed, show incoming commits and prompt for `/update confirm`.
    7. If confirmed:
       a. git pull --ff-only
       b. uv sync
       c. systemctl --user restart --no-block decafclaw
    """
    runner = cmd_runner or _default_cmd_runner

    # 1. Check for active turns
    busy_convs = get_busy_conversations(manager, index)
    if busy_convs:
        return UpdateResult(
            ok=False,
            message=f"Cannot update: active turn(s) in conversation(s): {', '.join(busy_convs)}.\nPlease wait for in-flight turns to complete before updating.",
        )

    # 2. Locate repository directory
    target_repo = repo_dir or find_repo_dir()
    if target_repo is None:
        return UpdateResult(
            ok=False,
            message="Cannot update: unable to locate DecafClaw git repository.",
        )

    # 3. Check working tree cleanliness (modified or staged tracked files)
    ret, stdout, stderr = await runner(["git", "status", "--porcelain", "-uno"], target_repo, None)
    if ret != 0:
        return UpdateResult(
            ok=False,
            message=f"Cannot update: git status check failed:\n{stderr.strip()}",
        )
    if stdout.strip():
        return UpdateResult(
            ok=False,
            message=f"Cannot update: working tree is dirty (modified or staged files present):\n{stdout.strip()}",
        )

    # 4. Check upstream tracking branch
    ret, stdout, stderr = await runner(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], target_repo, None
    )
    if ret != 0:
        return UpdateResult(
            ok=False,
            message="Cannot update: current branch has no upstream tracking branch configured.",
        )

    # 5. Fetch latest
    ret, stdout, stderr = await runner(["git", "fetch"], target_repo, None)
    if ret != 0:
        return UpdateResult(
            ok=False,
            message=f"Cannot update: `git fetch` failed:\n{stderr.strip()}",
        )

    # 6. Check if fast-forwardable
    ret, stdout, stderr = await runner(["git", "merge-base", "--is-ancestor", "HEAD", "@{u}"], target_repo, None)
    if ret != 0:
        return UpdateResult(
            ok=False,
            message="Cannot update: current branch has diverged from upstream and cannot be fast-forwarded.",
        )

    # 7. Check incoming commits
    ret, stdout, stderr = await runner(["git", "log", "HEAD..@{u}", "--oneline"], target_repo, None)
    if ret != 0:
        return UpdateResult(
            ok=False,
            message=f"Cannot update: failed to check incoming commits:\n{stderr.strip()}",
        )

    incoming = stdout.strip()
    if not incoming:
        return UpdateResult(
            ok=True,
            message="DecafClaw is already up to date.",
        )

    # If not confirmed, display incoming commits and ask for confirmation
    if not confirmed:
        return UpdateResult(
            ok=True,
            message=(
                f"Incoming commits:\n{incoming}\n\nTo apply this update and restart DecafClaw, run `/update confirm`."
            ),
        )

    # 8. Pull with --ff-only
    ret, stdout, stderr = await runner(["git", "pull", "--ff-only"], target_repo, None)
    if ret != 0:
        return UpdateResult(
            ok=False,
            message=f"Cannot update: `git pull --ff-only` failed:\n{stderr.strip()}",
        )

    # 9. uv sync
    env = os.environ.copy()
    home = str(Path.home())
    env["PATH"] = f"{home}/.local/bin:{env.get('PATH', '')}"

    ret, stdout, stderr = await runner(["uv", "sync"], target_repo, env)
    if ret != 0:
        err_detail = stderr.strip() or stdout.strip()
        return UpdateResult(
            ok=False,
            message=f"Update failed during `uv sync` (exit code {ret}):\n{err_detail}\n\nService was NOT restarted.",
        )

    # 10. Restart service
    if restart_coordinator is not None:
        await restart_coordinator.request_restart(reason="Self-update completed successfully", on_idle=False)
        return UpdateResult(
            ok=True,
            message="Update applied successfully. Restarting DecafClaw service...",
            restarted=True,
        )

    ret, stdout, stderr = await runner(["systemctl", "--user", "restart", "--no-block", "decafclaw"], target_repo, None)
    if ret != 0:
        return UpdateResult(
            ok=False,
            message=f"Update applied successfully, but service restart failed:\n{stderr.strip()}",
        )

    return UpdateResult(
        ok=True,
        message="Update applied successfully. Restarting DecafClaw service...",
        restarted=True,
    )
