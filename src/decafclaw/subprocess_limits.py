"""Memory caps for heavy subagent child processes via transient systemd scopes.

Heavy coding subagents (the opencode and Claude Code CLIs) run as child
processes of the daemon and can exhaust host memory, letting the kernel OOM
killer select the daemon itself.  On hosts where the user systemd instance
delegates the memory controller, each subagent CLI is wrapped in a transient
scope (``systemd-run --user --scope``) with ``MemoryMax``/``MemoryHigh`` so
the kernel OOM-kills the subagent instead of the daemon.

When a cap cannot be applied -- ``systemd-run`` is missing (e.g. macOS
development) or the memory controller is not delegated -- the child runs
uncapped and a single warning is logged per process lifetime.  The daemon
never refuses to run the subagent.

The prefix is a drop-in command prefix: in ``--scope`` mode systemd-run
creates the scope, the user manager moves systemd-run's own PID into the
scope's cgroup, and systemd-run then ``execvpe()``s the command in its own
process.  The command is therefore the direct child we wait for: its exit
status (including negative signal codes such as ``-9`` for SIGKILL) and its
stdout/stderr pass straight through to the caller.
"""

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

# Process-lifetime cache: the delegation probe result and whether we have
# already warned about an unappliable cap.
_delegation_cache: bool | None = None
_warned = False


def _user_manager_controllers_path() -> Path:
    """Path of the user manager cgroup's cgroup.controllers file (cgroup v2)."""
    uid = os.getuid()
    return Path("/sys/fs/cgroup/user.slice") / f"user-{uid}.slice" / f"user@{uid}.service" / "cgroup.controllers"


def _memory_controller_delegated() -> bool:
    """Return True if the user systemd instance has the memory controller delegated.

    Reads the user@<uid>.service cgroup's cgroup.controllers file; any read
    failure (non-Linux, cgroup v1, unusual layout) counts as not delegated.
    The result is cached for the process lifetime.
    """
    global _delegation_cache
    if _delegation_cache is None:
        try:
            controllers = _user_manager_controllers_path().read_text().split()
        except OSError:
            controllers = []
        _delegation_cache = "memory" in controllers
    return _delegation_cache


def _warn_once(memory_max: str, memory_high: str, label: str, reason: str) -> None:
    """Log at most one warning per process lifetime about an unappliable cap."""
    global _warned
    if _warned:
        return
    _warned = True
    who = f" for {label}" if label else ""
    log.warning(
        "Memory cap requested%s (memory_max=%s, memory_high=%s) but %s; subagent runs without a memory cap.",
        who,
        memory_max or "-",
        memory_high or "-",
        reason,
    )


def memory_cap_prefix(memory_max: str, memory_high: str, label: str = "") -> list[str]:
    """Return the command prefix that runs a child under a systemd memory cap.

    Returns ``["systemd-run", "--user", "--scope", "--quiet", "-p",
    "MemoryMax=...", "-p", "MemoryHigh=...", "--"]`` with the configured
    limits, or an empty list when no cap applies:

    - both limits are empty (cap disabled for this skill), or
    - ``systemd-run`` is not on PATH, or
    - the user systemd instance does not delegate the memory controller.

    In the latter two cases a single warning is logged per process lifetime.
    Either limit may be empty on its own; the scope then gets only the
    non-empty one. Callers with an explicit disable (a skill's
    ``memory_cap_enabled`` switch) should not call this function at all, so
    no "cap unavailable" warning is logged: an explicit disable is not a
    missing capability.
    """
    if not memory_max and not memory_high:
        return []

    if shutil.which("systemd-run") is None:
        _warn_once(memory_max, memory_high, label, "systemd-run is not on PATH")
        return []

    if not _memory_controller_delegated():
        _warn_once(
            memory_max,
            memory_high,
            label,
            "the memory controller is not delegated to the user systemd instance "
            "(see docs/deployment.md, 'Subagent Memory Limits')",
        )
        return []

    prefix = ["systemd-run", "--user", "--scope", "--quiet"]
    if memory_max:
        prefix += ["-p", f"MemoryMax={memory_max}"]
    if memory_high:
        prefix += ["-p", f"MemoryHigh={memory_high}"]
    prefix.append("--")
    return prefix
