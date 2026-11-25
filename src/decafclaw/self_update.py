"""Human-only self-update command handler (#981, #1649).

``/update`` previews incoming commits and, on confirm, starts the external
deploy unit (``decafclaw-deploy.service``). It no longer runs ``git pull``,
``uv sync``, or ``systemctl restart`` inline: the deploy runner owns the stop,
backup, pull, sync, start, verify, and rollback path, and the timer uses the
same unit.

This module is human-only: it MUST NEVER be imported by agent tools or skills.
A test enforces this boundary.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .restart_wake import RestartWake, delete_restart_wake, save_restart_wake

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
    config: Any | None = None,
    wake: RestartWake | None = None,
) -> UpdateResult:
    """Run the guarded self-update workflow.

    1. Check for active turns across all conversations. If any are busy, refuse.
    2. Check working tree cleanliness (modified/staged tracked files). If dirty, abort.
    3. Fetch and resolve the deploy candidate: ``origin/production`` when it
       exists, else the current branch's upstream.
    4. Check the candidate is a fast-forward of HEAD. If diverged, abort.
    5. Inspect incoming commits. If none, stop ("Already up to date").
    6. If unconfirmed, show incoming commits and prompt for `/update confirm`.
    7. If confirmed: persist the post-restart wake (when given) and run
       ``systemctl --user start --no-block decafclaw-deploy.service``. The
       external runner owns the actual deploy and health gate.
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

    # 4. Fetch latest and resolve the deploy candidate. The runner deploys
    # origin/production; a checkout that has not switched to it yet still gets
    # a useful preview from its own upstream.
    ret, stdout, stderr = await runner(["git", "fetch"], target_repo, None)
    if ret != 0:
        return UpdateResult(
            ok=False,
            message=f"Cannot update: `git fetch` failed:\n{stderr.strip()}",
        )
    ret, _, _ = await runner(["git", "rev-parse", "--verify", "--quiet", "origin/production"], target_repo, None)
    candidate_ref = "origin/production" if ret == 0 else "@{u}"
    ret, stdout, stderr = await runner(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", candidate_ref], target_repo, None
    )
    if ret != 0:
        return UpdateResult(
            ok=False,
            message="Cannot update: no deploy branch (origin/production) or upstream tracking branch.",
        )

    # 5. Check if fast-forwardable
    ret, stdout, stderr = await runner(["git", "merge-base", "--is-ancestor", "HEAD", candidate_ref], target_repo, None)
    if ret != 0:
        return UpdateResult(
            ok=False,
            message=f"Cannot update: HEAD has diverged from {candidate_ref} and cannot be fast-forwarded.",
        )

    # 6. Check incoming commits
    ret, stdout, stderr = await runner(["git", "log", f"HEAD..{candidate_ref}", "--oneline"], target_repo, None)
    if ret != 0:
        return UpdateResult(
            ok=False,
            message=f"Cannot update: failed to check incoming commits:\n{stderr.strip()}",
        )

    incoming = stdout.strip()
    if not incoming:
        return UpdateResult(
            ok=True,
            message=f"DecafClaw is already up to date with {candidate_ref}.",
        )

    # If not confirmed, display incoming commits and ask for confirmation
    if not confirmed:
        return UpdateResult(
            ok=True,
            message=(
                f"Incoming commits from {candidate_ref}:\n{incoming}\n\n"
                "To start the deploy now, run `/update confirm`. The automated deploy "
                "timer will pick these up on its own within one interval."
            ),
        )

    # 7. Start the external deploy unit. --no-block keeps the HTTP request or
    # Mattermost handler from hanging through the stop window (#1512's bug
    # class). The unit owns stop, backup, pull, sync, start, verify, rollback.
    resolved_config = config
    if resolved_config is None and restart_coordinator is not None:
        resolved_config = getattr(restart_coordinator, "config", None)
    if resolved_config is None:
        resolved_config = getattr(manager, "config", None)
    if wake is not None and resolved_config is not None:
        save_restart_wake(resolved_config, wake)

    ret, stdout, stderr = await runner(
        ["systemctl", "--user", "start", "--no-block", "decafclaw-deploy.service"], target_repo, None
    )
    if ret != 0:
        if wake is not None and resolved_config is not None:
            delete_restart_wake(resolved_config, wake.conv_id)
        return UpdateResult(
            ok=False,
            message=f"Cannot start the deploy service:\n{stderr.strip()}",
        )

    return UpdateResult(
        ok=True,
        message=(
            "Deploy started. The service will stop briefly, apply the update, verify "
            "health, and report the result here."
        ),
        restarted=True,
    )
