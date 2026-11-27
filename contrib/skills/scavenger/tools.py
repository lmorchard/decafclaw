"""
skills/scavenger/tools.py — Native DecafClaw tools for the LAN Scavenger Compute Fleet.
"""

import asyncio
import dataclasses
import importlib
import json
import os
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from decafclaw import canvas

_skill_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.abspath(os.path.join(_skill_dir, "..", "..", ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
if _skill_dir not in sys.path:
    sys.path.insert(0, _skill_dir)

try:
    from contrib.skills.scavenger.scavenger import (
        DEFAULT_FLEET,
        EnvReservation,
        HostConfig,
        HostStatus,
        container_limit_flags,
        env_arm_watch,
        env_create,
        env_destroy,
        env_exec,
        env_exec_bg,
        env_reap,
        env_sync,
        execute_job,
        find_lease,
        load_fleet_config,
        load_leases,
        parse_env_assignments,
        probe_host,
        reconcile_leases,
        resolve_recipes_dir,
        run_ssh,
        select_best_host,
        wake_host,
    )
except ImportError:
    from scavenger import (  # type: ignore[no-redef]
        DEFAULT_FLEET,
        EnvReservation,
        HostConfig,
        HostStatus,
        container_limit_flags,
        env_arm_watch,
        env_create,
        env_destroy,
        env_exec,
        env_exec_bg,
        env_reap,
        env_sync,
        execute_job,
        find_lease,
        load_fleet_config,
        load_leases,
        parse_env_assignments,
        probe_host,
        reconcile_leases,
        resolve_recipes_dir,
        run_ssh,
        select_best_host,
        wake_host,
    )

#: Network modes accepted by scavenger container tools.
NETWORK_MODES = ("none", "default")


def _resolve_local_path(ctx: Any, path: str) -> Optional[str]:
    """Resolve a workspace-relative path safely against the active workspace root."""
    if not path:
        return None
    if os.path.isabs(path) and os.path.exists(path):
        return path
    cfg = getattr(ctx, "config", None)
    if cfg and hasattr(cfg, "workspace_path") and cfg.workspace_path:
        c = os.path.join(str(cfg.workspace_path), path)
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(path)


async def _arm_watch_if_possible(ctx: Any, sentinel_rel: str, reason: str, timeout: int = 3600) -> bool:
    """Attempt to wire the local sentinel file into DecafClaw's core watch_file manager."""
    try:
        mgr = getattr(ctx, "manager", None)
        if mgr and hasattr(mgr, "file_watches") and getattr(ctx, "conv_id", None):
            cfg = getattr(ctx, "config", None)
            ws_path = getattr(cfg, "workspace_path", None) or os.getcwd()
            abs_target = os.path.abspath(os.path.join(str(ws_path), sentinel_rel))
            await mgr.file_watches.watch(
                conv_id=ctx.conv_id,
                path=abs_target,
                event="create",
                timeout_seconds=timeout,
                reason=reason or f"Scavenger watch fired on {sentinel_rel}",
            )
            return True
    except Exception:
        pass
    return False


def scavenger_probe(ctx: Any, host: str = "") -> str:
    """Probe scavenger fleet reachability, power state, and eligibility."""
    fleet = load_fleet_config()
    if not fleet:
        return "Error: No fleet configuration found. Check ~/.config/scavenger/fleet.json."

    reaped = env_reap(fleet)

    targets = [host] if host else list(fleet.keys())
    lines = [
        f"{'HOST':<14} {'ONLINE':<8} {'ARCH':<8} {'ENGINE':<8} {'AC':<6} {'STATUS':<10} {'NOTES'}",
        "-" * 75,
    ]
    for name in targets:
        cfg = fleet.get(name)
        if not cfg:
            lines.append(f"{name:<14} ERROR: Unknown host")
            continue
        st = probe_host(cfg, try_wake=False)
        ac_str = "Yes" if st.on_ac_power is True else ("No" if st.on_ac_power is False else "N/A")
        status_str = "ELIGIBLE" if st.eligible else "BUSY/OFF"
        lines.append(
            f"{st.name:<14} {str(st.online):<8} {st.arch:<8} {st.engine:<8} {ac_str:<6} {status_str:<10} {st.reason}"
        )

    if reaped:
        lines.append(f"Reaped {len(reaped)} expired environment(s): {', '.join(reaped)}")

    return "\n".join(lines)


def scavenger_wake(ctx: Any, host: str) -> str:
    """Send a Wake-on-LAN packet to a host and wait for it to become reachable."""
    fleet = load_fleet_config()
    cfg = fleet.get(host)
    if not cfg:
        return f"Error: Unknown host '{host}'"
    if not cfg.wol_mac:
        return f"Error: Host '{host}' has no wol_mac configured"

    return wake_host(cfg)["message"]


def scavenger_run(
    ctx: Any,
    command: str,
    repo_path: str = "",
    host: str = "",
    arch: str = "",
    image: str = "alpine:latest",
    mem: str = "16g",
    cpus: int = 8,
    network: str = "default",
) -> str:
    """Run a one-shot ephemeral container job on the best eligible scavenger host."""
    if network not in NETWORK_MODES:
        return f"Error: Invalid network '{network}'. Use 'none' (no network) or 'default'."

    fleet = load_fleet_config()
    if not fleet:
        return "Error: No fleet configuration found at ~/.config/scavenger/fleet.json."

    try:
        if host:
            target_host = fleet.get(host)
            if not target_host:
                return f"Error: Unknown host '{host}'"
            status = probe_host(target_host, try_wake=True)
            if not status.eligible:
                return f"Error: Host '{host}' is not eligible: {status.reason}"
        else:
            target_host, status = select_best_host(fleet, preferred_arch=arch or None, allow_wake=True)
    except Exception as e:
        return f"Host selection failed: {e}"

    real_repo: Optional[str] = _resolve_local_path(ctx, repo_path)
    if real_repo and not os.path.isdir(os.path.join(real_repo, ".git")):
        real_repo = None

    selinux_flag = ":Z" if target_host.needs_selinux_z else ""
    device_args = " ".join(target_host.device_flags) if target_host.device_flags else ""
    limit_args = " ".join(container_limit_flags(target_host, network))

    if real_repo:
        bundle_proc = subprocess.Popen(
            ["git", "-C", real_repo, "bundle", "create", "-", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        ssh_wrapper = f"""
set -e
RECV_DIR=$(mktemp -d /tmp/scavenger-job-XXXXXX)
trap 'rm -rf "$RECV_DIR"' EXIT
cat > "$RECV_DIR/repo.bundle"
git clone -q "$RECV_DIR/repo.bundle" "$RECV_DIR/workspace"

{target_host.engine} run --rm \\
    --memory={mem} \\
    --cpus={cpus} \\
    {limit_args} \\
    {device_args} \\
    -v "$RECV_DIR/workspace:/workspace{selinux_flag}" \\
    -w /workspace \\
    {image} \\
    sh -c '{command}'
"""
        ssh_proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", target_host.ssh_alias, ssh_wrapper],
            stdin=bundle_proc.stdout,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        if bundle_proc.stdout:
            bundle_proc.stdout.close()
        res = ssh_proc
    else:
        remote_script = f"""
set -e
JOB_DIR=$(mktemp -d /tmp/scavenger-job-XXXXXX)
trap 'rm -rf "$JOB_DIR"' EXIT
mkdir -p "$JOB_DIR/workspace"

{target_host.engine} run --rm \\
    --memory={mem} \\
    --cpus={cpus} \\
    {limit_args} \\
    {device_args} \\
    -v "$JOB_DIR/workspace:/workspace{selinux_flag}" \\
    -w /workspace \\
    {image} \\
    sh -c '{command}'
"""
        res = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", target_host.ssh_alias, remote_script],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )

    output = [
        f"Host: {target_host.name} ({target_host.arch}) | Engine: {target_host.engine} | Image: {image}",
        f"Exit Code: {res.returncode}",
    ]
    if res.stdout:
        output.append("\n--- STDOUT ---\n" + res.stdout.strip())
    if res.stderr:
        output.append("\n--- STDERR ---\n" + res.stderr.strip())

    return "\n".join(output)


def scavenger_exec_native(ctx: Any, host: str, command: str) -> str:
    """Execute a command natively on the target host OS under the scavenger user."""
    fleet = load_fleet_config()
    cfg = fleet.get(host)
    if not cfg:
        return f"Error: Unknown host '{host}'"

    res = run_ssh(cfg.ssh_alias, command, timeout=1800)
    host_os = getattr(cfg, "os", "linux")
    output = [
        f"Host: {cfg.name} (native {host_os} {cfg.arch}) | User: scavenger",
        f"Exit Code: {res.returncode}",
    ]
    if res.stdout:
        output.append("\n--- STDOUT ---\n" + res.stdout.strip())
    if res.stderr:
        output.append("\n--- STDERR ---\n" + res.stderr.strip())

    return "\n".join(output)


# =============================================================================
# Stateful Environment Tools
# =============================================================================


def scavenger_env_create(
    ctx: Any,
    env_id: str,
    host: str = "",
    kind: str = "container",
    image: str = "alpine:latest",
    recipe: str = "",
    setup_script: str = "",
    sync_dir: str = "",
    mem: str = "16g",
    cpus: int = 8,
    network: str = "default",
    ttl: str = "2h",
    arch: str = "",
    env_vars: Optional[Dict[str, str]] = None,
) -> str:
    """Reserve a stateful environment (container or native workspace) on the fleet."""
    if network not in NETWORK_MODES:
        return f"Error: Invalid network '{network}'. Use 'none' (no network) or 'default'."

    fleet = load_fleet_config()
    if not fleet:
        return "Error: No fleet configuration found."

    real_sync: Optional[str] = _resolve_local_path(ctx, sync_dir)
    if real_sync and not os.path.isdir(real_sync):
        real_sync = None

    real_setup: Optional[str] = _resolve_local_path(ctx, setup_script)
    cfg = getattr(ctx, "config", None)
    ws_root = getattr(cfg, "workspace_path", None)
    recipes_dir = resolve_recipes_dir(default=os.path.join(str(ws_root), "dockerfiles") if ws_root else None)

    clean_env: Optional[Dict[str, str]] = None
    if env_vars:
        if not isinstance(env_vars, dict):
            return "Error: env_vars must be an object mapping names to values."
        try:
            clean_env = parse_env_assignments([f"{k}={v}" for k, v in env_vars.items()])
        except ValueError as e:
            return f"Error: {e}"

    try:
        res = env_create(
            fleet=fleet,
            env_id=env_id,
            host_name=host or None,
            kind=kind,
            image=image,
            recipe=recipe or None,
            setup_script=real_setup or None,
            sync_dir=real_sync,
            mem_limit=mem,
            cpus=cpus,
            network=network,
            ttl=ttl,
            arch=arch or None,
            recipes_dir=recipes_dir,
            env_vars=clean_env,
        )
        env_note = f"\nEnv vars: {', '.join(sorted(clean_env))}" if clean_env else ""
        return (
            f"Environment '{res.env_id}' reserved on {res.host} ({res.kind}).\n"
            f"TTL: {res.ttl_seconds}s (expires in {int(res.expires_at - time.time())}s)\n"
            f"Target: {res.container_name if res.kind == 'container' else res.workspace_path}\n"
            f"Image/Recipe: {res.image or 'N/A'}{env_note}"
        )
    except Exception as e:
        return f"Failed to create environment: {e}"


async def scavenger_env_exec(
    ctx: Any,
    env_id: str,
    command: str,
    timeout: int = 1800,
    bg: bool = False,
    wake_on: str = "",
) -> str:
    """
    Execute a command inside a reserved stateful environment.
    If bg=True, runs asynchronously in the background.
    Optional wake_on (e.g. 'exit', 'pattern:BUILD SUCCESSFUL', 'file:app.apk') arms an
    automated wake event so DecafClaw is woken when the condition triggers.
    """
    fleet = load_fleet_config()
    res = find_lease(fleet, env_id)
    if not res:
        return f"Error: No active lease found for environment '{env_id}'"

    if bg:
        log_file = f".scavenger_exec_{int(time.time())}.log"
        exit_file = f".scavenger_exec_{int(time.time())}.exit"
        bg_info = env_exec_bg(fleet, res, command, log_file=log_file, exit_file=exit_file)

        output = [
            f"Env: {res.env_id} | Host: {res.host} ({res.kind}) [BACKGROUND JOB]",
            f"PID: {bg_info['pid']} | Log: {bg_info['log_file']}",
        ]

        if wake_on:
            ts = int(time.time())
            sentinel_rel = f"tmp/scavenger/sentinels/{env_id}_{ts}.sentinel"
            cfg = getattr(ctx, "config", None)
            ws_path = getattr(cfg, "workspace_path", None) or os.getcwd()
            sentinel_abs = os.path.abspath(os.path.join(str(ws_path), sentinel_rel))

            if wake_on.startswith("pattern:"):
                w_type = "pattern"
                pattern = wake_on.split(":", 1)[1]
                target = log_file
            elif wake_on.startswith("file:"):
                w_type = "file"
                target = wake_on.split(":", 1)[1]
                pattern = ""
            else:
                w_type = "exit"
                target = exit_file
                pattern = ""

            env_arm_watch(
                fleet=fleet,
                res=res,
                watch_type=w_type,
                sentinel_path=sentinel_abs,
                target=target,
                pattern=pattern,
                timeout_seconds=timeout,
            )

            armed_core = await _arm_watch_if_possible(
                ctx=ctx,
                sentinel_rel=sentinel_rel,
                reason=f"Scavenger env '{env_id}' background job completed ({wake_on})",
                timeout=timeout,
            )

            output.append(f"Sentinel: {sentinel_rel}")
            if armed_core:
                output.append("Wake Watcher: Armed! DecafClaw will autonomously wake when this condition triggers.")
            else:
                output.append(f"Wake Watcher: Ready. Call watch_file(path='{sentinel_rel}') to arm wake turn.")

        return "\n".join(output)

    proc = env_exec(fleet, res, command, timeout_seconds=timeout)
    output = [
        f"Env: {res.env_id} | Host: {res.host} ({res.kind})",
        f"Exit Code: {proc.returncode}",
    ]
    if proc.stdout:
        output.append("\n--- STDOUT ---\n" + proc.stdout.strip())
    if proc.stderr:
        output.append("\n--- STDERR ---\n" + proc.stderr.strip())

    return "\n".join(output)


async def scavenger_env_watch(
    ctx: Any,
    env_id: str,
    watch_type: str = "exit",
    target: str = ".scavenger_exec.log",
    pattern: str = "",
    timeout: int = 3600,
    reason: str = "",
) -> str:
    """
    Arm a custom event trigger on a reserved environment.
    Wakes DecafClaw when:
    - watch_type='exit': target exit sentinel file appears
    - watch_type='file': target file appears or is touched
    - watch_type='pattern': pattern matches in target log file
    """
    fleet = load_fleet_config()
    res = find_lease(fleet, env_id)
    if not res:
        return f"Error: No active lease found for environment '{env_id}'"

    ts = int(time.time())
    sentinel_rel = f"tmp/scavenger/sentinels/watch_{env_id}_{ts}.sentinel"
    cfg = getattr(ctx, "config", None)
    ws_path = getattr(cfg, "workspace_path", None) or os.getcwd()
    sentinel_abs = os.path.abspath(os.path.join(str(ws_path), sentinel_rel))

    env_arm_watch(
        fleet=fleet,
        res=res,
        watch_type=watch_type,
        sentinel_path=sentinel_abs,
        target=target,
        pattern=pattern,
        timeout_seconds=timeout,
    )

    armed_core = await _arm_watch_if_possible(
        ctx=ctx,
        sentinel_rel=sentinel_rel,
        reason=reason or f"Scavenger watch fired on env '{env_id}' ({watch_type}: {pattern or target})",
        timeout=timeout,
    )

    lines = [
        f"Event watch armed on env '{env_id}' ({res.host}).",
        f"Condition: {watch_type} (target='{target}', pattern='{pattern}')",
        f"Sentinel: {sentinel_rel}",
    ]
    if armed_core:
        lines.append("Wake Watcher: Armed! DecafClaw will autonomously wake when this condition triggers.")
    else:
        lines.append(f"Wake Watcher: Ready. Call watch_file(path='{sentinel_rel}') to arm wake turn.")

    return "\n".join(lines)


def scavenger_env_info(ctx: Any, env_id: str) -> str:
    """Inspect targeted details and remaining TTL for a specific environment reservation."""
    fleet = load_fleet_config()
    res = find_lease(fleet, env_id)
    if not res:
        return f"Error: No active lease found for environment '{env_id}'"

    now = time.time()
    rem = int(res.expires_at - now)
    rem_str = f"{rem}s" if rem > 0 else "EXPIRED"
    target = res.container_name if res.kind == "container" else (res.workspace_path or "")
    return (
        f"Environment: {res.env_id}\n"
        f"Host:        {res.host} ({res.kind})\n"
        f"Target:      {target}\n"
        f"Image:       {res.image or 'N/A'}\n"
        f"Expires In:  {rem_str} (TTL: {res.ttl_seconds}s)"
    )


async def scavenger_env_terminal(ctx: Any, env_id: str, shell: str = "sh") -> str:
    """
    Open an interactive Canvas terminal tab connected directly into a scavenger environment.
    Embeds DecafClaw's native xterm.js PTY terminal widget in a new tab.
    """
    fleet = load_fleet_config()
    res = find_lease(fleet, env_id)
    if not res:
        return f"Error: No active lease found for environment '{env_id}'"

    host = fleet.get(res.host)
    if not host:
        return f"Error: Unknown host '{res.host}'"

    cfg = getattr(ctx, "config", None)
    ws_path = getattr(cfg, "workspace_path", None) or os.getcwd()
    term_dir = os.path.join(str(ws_path), "tmp", "scavenger", "terminals")
    os.makedirs(term_dir, exist_ok=True)
    wrapper_path = os.path.join(term_dir, f"{env_id}.sh")

    if res.kind == "native":
        ws = res.workspace_path or f"workspaces/{res.env_id}"
        script = f"""#!/bin/sh
exec ssh -t -o BatchMode=no {host.ssh_alias} "cd ~/{ws} && exec ${{SHELL:-bash}} -l"
"""
    else:
        c_name = res.container_name or f"scavenger-env-{res.env_id}"
        script = f"""#!/bin/sh
exec ssh -t -o BatchMode=no {host.ssh_alias} "{host.engine} exec -it {c_name} {shell}"
"""

    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(script)
    os.chmod(wrapper_path, 0o755)

    session_id = uuid.uuid4().hex
    mgr = getattr(ctx, "manager", None)
    emit = mgr.emit if mgr else None

    result = await canvas.new_tab(
        cfg,
        ctx.conv_id,
        "terminal",
        {"session_id": session_id, "cwd": str(ws_path), "shell": wrapper_path},
        label=f"Terminal: {env_id}",
        emit=emit,
        enforce_agent_createable=False,
    )

    if not result.ok:
        return f"Error opening canvas terminal tab: {result.error}"

    tab_id = result.tab_id
    if not tab_id:
        return f"Error opening canvas terminal tab: {result.error}"

    registry = getattr(mgr, "terminal_registry", None)
    if not registry:
        return f"Canvas tab '{tab_id}' created, but terminal registry is not active on this server."

    try:
        await registry.spawn(ctx.conv_id, tab_id, session_id, str(ws_path), wrapper_path)
    except Exception as exc:
        await canvas.close_tab(cfg, ctx.conv_id, tab_id, emit=emit)
        return f"Failed to spawn terminal process: {exc}"

    return f"Opened interactive terminal in Canvas tab '{tab_id}' connected to scavenger environment '{env_id}' on {res.host}."


def scavenger_env_sync(ctx: Any, env_id: str, direction: str, local_path: str) -> str:
    """Sync files up to or down from a reserved environment."""
    fleet = load_fleet_config()
    res = find_lease(fleet, env_id)
    if not res:
        return f"Error: No active lease found for environment '{env_id}'"

    real_path = _resolve_local_path(ctx, local_path) or os.path.abspath(local_path)
    try:
        env_sync(fleet, res, direction=direction, local_path=real_path)
        return f"Sync {direction} complete between '{env_id}' and '{local_path}'."
    except Exception as e:
        return f"Sync failed: {e}"


def scavenger_env_destroy(ctx: Any, env_id: str) -> str:
    """Tear down a reserved environment and release its lease."""
    fleet = load_fleet_config()
    ok = env_destroy(fleet, env_id)
    if ok:
        return f"Environment '{env_id}' destroyed and lease released."
    still = load_leases().get(env_id)
    if still:
        return (
            f"Failed to destroy environment '{env_id}' on {still.host} (host unreachable?); lease retained for retry."
        )
    return f"Environment '{env_id}' not found or already destroyed."


def scavenger_env_list(ctx: Any) -> str:
    """List all active environment reservations across the fleet, as reported by each host."""
    fleet = load_fleet_config()
    leases, unreachable = reconcile_leases(fleet)
    now = time.time()
    if not leases:
        return "No active environment reservations."

    lines = [
        f"{'ENV_ID':<24} {'HOST':<12} {'KIND':<10} {'EXPIRES_IN':<12} {'TARGET'}",
        "-" * 75,
    ]
    for k, v in leases.items():
        rem = int(v.expires_at - now)
        rem_str = f"{rem}s" if rem > 0 else "EXPIRED"
        target = v.container_name if v.kind == "container" else (v.workspace_path or "")
        lines.append(f"{k:<24} {v.host:<12} {v.kind:<10} {rem_str:<12} {target}")
    if unreachable:
        lines.append(f"(unreachable, showing cached records: {', '.join(unreachable)})")

    return "\n".join(lines)


def scavenger_reap(ctx: Any) -> str:
    """Sweep and reap all expired environment reservations."""
    fleet = load_fleet_config()
    reaped = env_reap(fleet)
    if reaped:
        return f"Reaped {len(reaped)} expired environment(s): {', '.join(reaped)}"
    still_expired = [k for k, v in load_leases().items() if v.expires_at < time.time()]
    if still_expired:
        return (
            f"Could not reap {len(still_expired)} expired environment(s) ({', '.join(still_expired)}); "
            "their hosts are unreachable. They will be retried on the next sweep."
        )
    return "No expired environments to reap."


def scavenger_runner_path(ctx: Any) -> str:
    """Return the absolute path to the opencode scavenger runner executable."""
    runner_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "runners", "opencode_scavenger_runner.py"))
    return runner_script


TOOLS = {
    "scavenger_probe": scavenger_probe,
    "scavenger_wake": scavenger_wake,
    "scavenger_run": scavenger_run,
    "scavenger_exec_native": scavenger_exec_native,
    "scavenger_env_create": scavenger_env_create,
    "scavenger_env_exec": scavenger_env_exec,
    "scavenger_env_watch": scavenger_env_watch,
    "scavenger_env_info": scavenger_env_info,
    "scavenger_env_terminal": scavenger_env_terminal,
    "scavenger_env_sync": scavenger_env_sync,
    "scavenger_env_destroy": scavenger_env_destroy,
    "scavenger_env_list": scavenger_env_list,
    "scavenger_reap": scavenger_reap,
    "scavenger_runner_path": scavenger_runner_path,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "scavenger_probe",
            "description": "Probe reachability, power status (AC vs battery), and eligibility of the LAN scavenger compute fleet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "Optional specific host name to probe (e.g. 'desktop-linux', 'laptop-mac'). Omit to probe all hosts.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_wake",
            "description": "Send a Wake-on-LAN magic packet to a sleep-capable scavenger host (e.g. sleep-capable desktop or AC-connected laptop).",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "Target host name to wake.",
                    }
                },
                "required": ["host"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_run",
            "description": "Run a one-shot ephemeral container job (tests, compilation) on the best eligible scavenger host.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run inside the container (e.g. 'pytest tests/', 'cargo test').",
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Optional relative path to git repository to bundle and mount at /workspace (e.g. '.').",
                    },
                    "host": {
                        "type": "string",
                        "description": "Optional host name to target directly (bypasses automatic selection).",
                    },
                    "arch": {
                        "type": "string",
                        "enum": ["arm64", "x86_64"],
                        "description": "Optional architecture filter (e.g. 'arm64' or 'x86_64').",
                    },
                    "image": {
                        "type": "string",
                        "description": "Container image to use (default: 'alpine:latest').",
                    },
                    "mem": {
                        "type": "string",
                        "description": "Memory limit for container (default: '16g').",
                    },
                    "cpus": {
                        "type": "integer",
                        "description": "CPU core limit for container (default: 8).",
                    },
                    "network": {
                        "type": "string",
                        "enum": ["none", "default"],
                        "description": "Container network mode (default: 'default'). Use 'none' for test runs that need no downloads — it blocks all container network access, including the LAN.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_exec_native",
            "description": "Execute a one-shot command natively on the target host OS under the isolated 'scavenger' user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {
                        "type": "string",
                        "description": "Host to target (e.g. 'desktop-linux', 'laptop-mac').",
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to run natively on the host as user scavenger.",
                    },
                },
                "required": ["host", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_env_create",
            "description": "Reserve a persistent, warm execution environment (container with volume or native workspace) that stays alive across turns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env_id": {
                        "type": "string",
                        "description": "Unique identifier for this environment (e.g. 'android-build', 'mlx-eval').",
                    },
                    "host": {
                        "type": "string",
                        "description": "Optional host name to target (e.g. 'desktop-linux', 'laptop-mac'). Omit for automatic selection.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["container", "native"],
                        "description": "Environment kind: 'container' (default, isolated container with volume) or 'native' (scoped OS directory for Apple Silicon MLX).",
                    },
                    "image": {
                        "type": "string",
                        "description": "Container image if kind='container' (e.g. 'node:22-bookworm', 'rust:latest').",
                    },
                    "recipe": {
                        "type": "string",
                        "description": "Optional recipe name from workspace dockerfiles/ or skills/scavenger/dockerfiles/ (e.g. 'android-builder', 'node-dev'). Automatically builds/caches remote image.",
                    },
                    "setup_script": {
                        "type": "string",
                        "description": "Optional path to an idempotent setup script (e.g. 'skills/scavenger/setup-scripts/setup-darwin-mlx.sh') to run once upon creation.",
                    },
                    "sync_dir": {
                        "type": "string",
                        "description": "Optional local directory to initial-sync into /workspace (e.g. 'decafclaw/contrib/android').",
                    },
                    "mem": {
                        "type": "string",
                        "description": "Memory limit for container (default: '16g').",
                    },
                    "cpus": {
                        "type": "integer",
                        "description": "CPU limit for container (default: 8).",
                    },
                    "network": {
                        "type": "string",
                        "enum": ["none", "default"],
                        "description": "Container network mode (default: 'default'). Use 'none' for environments that need no downloads — it blocks all container network access, including the LAN.",
                    },
                    "ttl": {
                        "type": "string",
                        "description": "Time to live before auto-reap (e.g. '2h', '1h', '30m'). Default: '2h'.",
                    },
                    "arch": {
                        "type": "string",
                        "enum": ["arm64", "x86_64"],
                        "description": "Optional architecture preference.",
                    },
                    "env_vars": {
                        "type": "object",
                        "description": "Environment variables passed to every later env-exec in this environment (e.g. API keys, model endpoints). Stored in a 0600 file on the controller and never written to container labels.",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["env_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_env_exec",
            "description": "Run a command inside an active reserved environment. Supports synchronous and asynchronous background execution with optional wake triggers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env_id": {
                        "type": "string",
                        "description": "The reserved environment identifier.",
                    },
                    "command": {
                        "type": "string",
                        "description": "Command to run inside the reserved workspace.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 1800).",
                    },
                    "bg": {
                        "type": "boolean",
                        "description": "When true, run asynchronously in the background and return immediately without blocking the turn.",
                    },
                    "wake_on": {
                        "type": "string",
                        "description": "Optional automated wake condition when bg=true: 'exit' (when process exits), 'pattern:<regex>' (when log matches), or 'file:<rel_path>' (when file is created).",
                    },
                },
                "required": ["env_id", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_env_watch",
            "description": "Arm an event watch on a reserved environment to wake DecafClaw when an exit, file creation, or stdio log pattern triggers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env_id": {
                        "type": "string",
                        "description": "The reserved environment identifier.",
                    },
                    "watch_type": {
                        "type": "string",
                        "enum": ["exit", "file", "pattern"],
                        "description": "Event type to watch for: 'exit' (wait for .scavenger_exec.exit), 'file' (wait for target file), 'pattern' (regex match in target log).",
                    },
                    "target": {
                        "type": "string",
                        "description": "Target file to watch or tail (default: '.scavenger_exec.log').",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to match when watch_type='pattern'.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum seconds to wait before watch expires (default: 3600).",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason included in wake prompt (e.g. 'Build finished').",
                    },
                },
                "required": ["env_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_env_info",
            "description": "Inspect targeted status, target location, and remaining TTL for a single environment reservation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env_id": {
                        "type": "string",
                        "description": "The reserved environment identifier to inspect.",
                    },
                },
                "required": ["env_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_env_terminal",
            "description": "Open an interactive live xterm.js terminal tab in the Canvas connected directly into a scavenger environment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env_id": {
                        "type": "string",
                        "description": "The reserved environment identifier to connect to.",
                    },
                    "shell": {
                        "type": "string",
                        "description": "Shell to invoke inside container (default: 'sh' or 'bash').",
                    },
                },
                "required": ["env_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_env_sync",
            "description": "Sync files up to or down from an active reserved environment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env_id": {
                        "type": "string",
                        "description": "The reserved environment identifier.",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "'up' to push local files into remote workspace, 'down' to pull remote files to local path.",
                    },
                    "local_path": {
                        "type": "string",
                        "description": "Local workspace relative directory path.",
                    },
                },
                "required": ["env_id", "direction", "local_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_env_destroy",
            "description": "Destroy a reserved environment, free its container/volume or workspace, and release its lease.",
            "parameters": {
                "type": "object",
                "properties": {
                    "env_id": {
                        "type": "string",
                        "description": "The environment identifier to destroy.",
                    },
                },
                "required": ["env_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_env_list",
            "description": "List all active reserved environments across the fleet with remaining TTL.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_reap",
            "description": "Sweep and reap any expired environment reservations across the fleet.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scavenger_runner_path",
            "description": "Return the absolute path to the opencode scavenger runner executable.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


def get_tools(ctx: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    return TOOLS, TOOL_DEFINITIONS
