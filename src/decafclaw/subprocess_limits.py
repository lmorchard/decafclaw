"""Resource caps for child processes via transient systemd scopes.

Two families of child process can outgrow the host and take the daemon down
with them:

- Heavy coding subagents (the opencode and Claude Code CLIs) run as direct
  children of the daemon and can exhaust host memory, letting the kernel OOM
  killer select the daemon itself.
- Arbitrary shell commands started by an agent (the ``shell`` tool and the
  background job runner) are even less predictable: a build, an LSP index, a
  runaway script, or a fork bomb.

On hosts where the user systemd instance delegates the relevant controllers,
each child is wrapped in a transient scope (``systemd-run --user --scope``)
with cgroup limits, so the kernel OOM-kills the child instead of the daemon
and a CPU hog cannot starve the daemon or SSH.

When a cap cannot be applied -- ``systemd-run`` is missing (e.g. macOS
development) or the controllers are not delegated -- the child runs uncapped
and a single warning is logged per process lifetime.  The daemon never
refuses to run a child because a cap is unavailable.

The prefix is a drop-in command prefix: in ``--scope`` mode systemd-run
creates the scope, the user manager moves systemd-run's own PID into the
scope's cgroup, and systemd-run then ``execvpe()``s the command in its own
process.  The command is therefore the direct child we wait for: its exit
status (including negative signal codes such as ``-9`` for SIGKILL) and its
stdout/stderr pass straight through to the caller.  Because the command
replaces systemd-run in its own PID and is its own process-group leader, the
existing ``start_new_session=True`` + ``killpg`` timeout path keeps working.

**MemorySwapMax matters.** A cgroup's ``memory.swap.max`` defaults to ``max``,
so ``MemoryMax`` alone lets a runaway spill into swap instead of dying -- on a
host that was given swap precisely because it was short on RAM, that just
moves the thrash.  Any cap that sets ``MemoryMax`` therefore also sets
``MemorySwapMax`` unless the caller overrides it.
"""

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

# Process-lifetime cache: the delegation probe result and whether we have
# already warned about an unappliable cap.
_delegation_cache: set[str] | None = None
_warned = False


def _user_manager_controllers_path() -> Path:
    """Path of the user manager cgroup's cgroup.controllers file (cgroup v2)."""
    uid = os.getuid()
    return Path("/sys/fs/cgroup/user.slice") / f"user-{uid}.slice" / f"user@{uid}.service" / "cgroup.controllers"


def _delegated_controllers() -> set[str]:
    """Return the controllers delegated to the user systemd instance.

    Reads the user@<uid>.service cgroup's cgroup.controllers file; any read
    failure (non-Linux, cgroup v1, unusual layout) counts as none delegated.
    The result is cached for the process lifetime.
    """
    global _delegation_cache
    if _delegation_cache is None:
        try:
            _delegation_cache = set(_user_manager_controllers_path().read_text().split())
        except OSError:
            _delegation_cache = set()
    return _delegation_cache


def _controller_delegated(name: str) -> bool:
    """Return True if the user systemd instance delegates controller ``name``."""
    return name in _delegated_controllers()


def _memory_controller_delegated() -> bool:
    """Return True if the user systemd instance has the memory controller delegated."""
    return _controller_delegated("memory")


def reset_caches() -> None:
    """Clear the process-lifetime probe and warning caches.

    Exists for tests and for configuration reloads: a host may gain controller
    delegation while the daemon is running.
    """
    global _delegation_cache, _warned
    _delegation_cache = None
    _warned = False


def _warn_once(memory_max: str, memory_high: str, label: str, reason: str) -> None:
    """Log at most one warning per process lifetime about an unappliable cap."""
    global _warned
    if _warned:
        return
    _warned = True
    who = f" for {label}" if label else ""
    log.warning(
        "Resource cap requested%s (memory_max=%s, memory_high=%s) but %s; child runs without a resource cap.",
        who,
        memory_max or "-",
        memory_high or "-",
        reason,
    )


def memory_cap_prefix(
    memory_max: str,
    memory_high: str,
    label: str = "",
    *,
    memory_swap_max: str = "",
) -> list[str]:
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

    ``memory_swap_max`` is passed through as ``MemorySwapMax`` when set. When
    ``memory_max`` is set and ``memory_swap_max`` is not, ``MemorySwapMax=0``
    is applied so a capped child cannot escape its cap by swapping.
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
    if memory_swap_max:
        prefix += ["-p", f"MemorySwapMax={memory_swap_max}"]
    elif memory_max:
        # memory.swap.max defaults to "max": without this a capped child spills
        # into swap instead of dying, which is the exact failure the cap exists
        # to prevent on a host that only has swap as a shock absorber.
        prefix += ["-p", "MemorySwapMax=0"]
    prefix.append("--")
    return prefix


def resource_cap_prefix(
    *,
    memory_max: str = "",
    memory_high: str = "",
    memory_swap_max: str = "",
    cpu_weight: int | None = None,
    tasks_max: int | None = None,
    label: str = "",
) -> list[str]:
    """Return a command prefix that runs a child under a transient capped scope.

    Generalizes :func:`memory_cap_prefix` to the limits an arbitrary shell
    command needs. Applies, for whichever limits are set:

    - ``MemoryMax`` / ``MemoryHigh`` / ``MemorySwapMax`` (memory controller)
    - ``CPUWeight`` (cpu controller) -- relative share, so a CPU hog cannot
      starve the daemon or SSH. systemd's own default is 100.
    - ``TasksMax`` (pids controller) -- a fork bomb dies at this many tasks
      instead of exhausting the host's pid space.

    Returns an empty list (child runs uncapped) when every limit is unset, or
    when the requested limits cannot be applied on this host. Missing
    delegation of a *requested* controller, or a missing ``systemd-run``,
    logs one warning per process lifetime. Limits are orthogonal: asking for
    ``tasks_max`` on a host that delegates ``pids`` but not ``memory`` still
    yields a prefix carrying just ``TasksMax``.

    Callers with an explicit off switch must not call this at all, so that an
    intentional disable logs no "cap unavailable" warning.
    """
    has_memory = bool(memory_max or memory_high)
    has_cpu = cpu_weight is not None
    has_tasks = tasks_max is not None
    if not (has_memory or has_cpu or has_tasks):
        return []

    if shutil.which("systemd-run") is None:
        _warn_once(memory_max, memory_high, label, "systemd-run is not on PATH")
        return []

    delegated = _delegated_controllers()

    # Drop limits whose controller is not delegated, rather than refusing the
    # whole prefix: one applicable cap is still worth having.
    dropped: list[str] = []
    if has_memory and "memory" not in delegated:
        dropped.append("memory")
        has_memory = False
    if has_cpu and "cpu" not in delegated:
        dropped.append("cpu")
        has_cpu = False
    if has_tasks and "pids" not in delegated:
        dropped.append("pids")
        has_tasks = False

    if not (has_memory or has_cpu or has_tasks):
        _warn_once(
            memory_max,
            memory_high,
            label,
            f"the {', '.join(dropped)} controller(s) are not delegated to the user systemd instance "
            "(see docs/deployment.md, 'Subagent Memory Limits')",
        )
        return []

    if dropped:
        # Still capped on the controllers we do have; note the gap once.
        log.warning(
            "Resource cap for %s: the %s controller(s) are not delegated to the user systemd "
            "instance; applying the remaining limits only (see docs/deployment.md, "
            "'Subagent Memory Limits').",
            label or "child",
            ", ".join(dropped),
        )

    prefix = ["systemd-run", "--user", "--scope", "--quiet"]
    if has_memory:
        if memory_max:
            prefix += ["-p", f"MemoryMax={memory_max}"]
        if memory_high:
            prefix += ["-p", f"MemoryHigh={memory_high}"]
        if memory_swap_max:
            prefix += ["-p", f"MemorySwapMax={memory_swap_max}"]
        elif memory_max:
            # See memory_cap_prefix: the scope default is "max", which would let
            # the child swap instead of being capped.
            prefix += ["-p", "MemorySwapMax=0"]
    # Narrow on the value itself, not just the has_cpu flag: the flag encodes
    # this check but a type checker cannot see through it.
    if cpu_weight is not None:
        prefix += ["-p", f"CPUWeight={int(cpu_weight)}"]
    if tasks_max is not None:
        prefix += ["-p", f"TasksMax={int(tasks_max)}"]
    prefix.append("--")
    return prefix


def describe_kill(returncode: int | None, label: str = "command") -> str | None:
    """Return a human-readable note when a child died to a cgroup limit.

    ``-9`` / ``137`` is a direct OOM kill. ``-24`` / ``152`` is SIGXCPU.

    ``-15`` / ``143`` is reported as a *possible* cap kill rather than a
    certain one, because that is what it is. When the kernel OOM-kills a
    process *inside* a capped scope, the victim is often a grandchild (``sh``
    runs the command), and systemd's default ``OOMPolicy=stop`` then SIGTERMs
    whatever is left in the scope -- so the process the caller waits on dies to
    SIGTERM, not SIGKILL. Only the kernel log distinguishes that from someone
    else killing the command, so the note says what to check instead of
    asserting a cause. Callers that already know the command timed out return
    their own message before reaching this.

    Returns None for anything else so callers keep their own messaging.
    """
    if returncode in (-9, 137):
        return f"{label} was killed by the kernel: it exceeded its memory cap (OOM-killed in its cgroup)."
    if returncode in (-24, 152):
        return f"{label} was killed by the kernel: it exceeded its CPU time limit."
    if returncode in (-15, 143):
        return (
            f"{label} was terminated by SIGTERM. If it was not stopped deliberately, it may have "
            "been killed for exceeding its memory cap — when the OOM victim is a child of the "
            "shell rather than the shell itself, systemd stops the whole scope. Check "
            "`journalctl --user` for an oom-kill in the run-*.scope."
        )
    return None
