#!/usr/bin/env python3
"""
scavenger.py — Ephemeral container and reserved environment execution across LAN scavenger fleet.

Agentless, deterministic orchestration over SSH for running heavy compilation,
test suites, subagent sessions, and ML/GPU workloads on idle workstations and laptops.
Zero daemons on targets. Hard blast-radius containment via dedicated scavenger user.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class HostConfig:
    name: str
    ssh_alias: str
    os: str = "linux"  # "linux" or "darwin"
    engine: str = "docker"  # "docker" or "podman"
    arch: str = "arm64"  # "arm64" or "x86_64"
    needs_selinux_z: bool = False  # Podman on SELinux (e.g. Bazzite Fedora)
    device_flags: List[str] = field(default_factory=list)  # e.g. ["--device", "/dev/dri"]
    wol_mac: Optional[str] = None  # Generic MAC address for Wake-on-LAN
    wol_broadcast: str = "255.255.255.255"
    wol_port: int = 9
    wol_wait_seconds: int = 10
    battery_check: bool = False  # Check pmset -g batt for AC power
    sunshine_check: bool = False  # Check if gaming/streaming is active
    priority: int = 100  # Lower number = higher priority
    default_memory: str = "16g"
    default_cpus: int = 8
    tags: List[str] = field(default_factory=list)
    gpu_type: Optional[str] = None  # "metal", "vulkan", "cuda", etc.
    gpu_passthrough_mode: Optional[str] = None  # "container_device", "native_only", etc.
    notes: str = ""


DEFAULT_FLEET: Dict[str, HostConfig] = {}
LEASES_FILE = os.path.expanduser("~/.config/scavenger/leases.json")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RECIPES_DIR = "dockerfiles"

# Container label namespace. Containers created before the rename carry the
# legacy "decafclaw." names, so readers accept both.
LABEL_PREFIX = "scavenger"
LEGACY_LABEL_PREFIXES: Tuple[str, ...] = ("decafclaw",)
LABEL_ENV_ID = f"{LABEL_PREFIX}.env_id"
LABEL_EXPIRES_AT = f"{LABEL_PREFIX}.expires_at"


def parse_env_labels(labels: Any) -> Optional[Tuple[str, int]]:
    """
    Read (env_id, expires_at) from container labels, accepting the current
    `scavenger.*` names and the legacy `decafclaw.*` names.

    `labels` is a dict or the comma-separated `k=v` string printed by
    `docker ps --format '{{.Labels}}'`. Returns None when the container is not
    a scavenger environment or the expiry is missing or malformed.
    """
    if isinstance(labels, str):
        parsed: Dict[str, str] = {}
        for item in labels.split(","):
            key, sep, value = item.partition("=")
            if sep:
                parsed[key.strip()] = value.strip()
        labels = parsed

    def lookup(suffix: str) -> Optional[str]:
        for prefix in (LABEL_PREFIX, *LEGACY_LABEL_PREFIXES):
            value = labels.get(f"{prefix}.{suffix}")
            if value:
                return str(value)
        return None

    env_id = lookup("env_id")
    expires = lookup("expires_at")
    if not env_id or not expires:
        return None
    try:
        return env_id, int(expires)
    except ValueError:
        return None


def find_fleet_config(config_path: Optional[str] = None) -> Optional[str]:
    """Return the fleet.json path to use: an explicit path, else the first existing fallback."""
    if config_path:
        return config_path if os.path.isfile(config_path) else None
    candidates = [
        os.path.expanduser("~/.config/scavenger/fleet.json"),
        os.path.join(SCRIPT_DIR, "..", "config", "scavenger_fleet.json"),
        os.path.join(os.getcwd(), "config", "scavenger_fleet.json"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def configured_recipes_dir(cli_value: Optional[str] = None, config_path: Optional[str] = None) -> Optional[str]:
    """
    Return the operator-chosen recipe directory, or None when none is configured.

    Order: the `--recipes-dir` value, then `recipes_dir` in fleet.json. A relative
    `recipes_dir` in fleet.json is relative to the fleet.json file. When this returns
    None, `resolve_recipe` falls back to `./dockerfiles`.
    """
    if cli_value:
        return os.path.abspath(os.path.expanduser(cli_value))

    found = find_fleet_config(config_path)
    if found:
        with open(found, "r", encoding="utf-8") as f:
            configured = json.load(f).get("recipes_dir")
        if configured:
            path = os.path.expanduser(str(configured))
            if not os.path.isabs(path):
                path = os.path.join(os.path.dirname(os.path.abspath(found)), path)
            return os.path.abspath(path)

    return None


def load_fleet_config(config_path: Optional[str] = None) -> Dict[str, HostConfig]:
    """Load declarative fleet configuration from JSON file with fallback paths."""
    config_path = find_fleet_config(config_path)
    if not config_path:
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    default_bcast = data.get("default_broadcast", "255.255.255.255")
    default_port = data.get("default_wol_port", 9)
    fleet: Dict[str, HostConfig] = {}
    for h in data.get("hosts", []):
        container = h.get("container", {})
        power = h.get("power", {})
        courtesy = h.get("courtesy", {})
        limits = h.get("limits", {})
        gpu = h.get("gpu", {})

        cfg = HostConfig(
            name=h["name"],
            ssh_alias=h.get("ssh_alias", f"scavenger-{h['name']}"),
            os=h.get("os", "linux"),
            engine=container.get("engine", "docker"),
            arch=h.get("arch", "x86_64"),
            needs_selinux_z=container.get("needs_selinux_z", False),
            device_flags=container.get("device_flags", []),
            wol_mac=power.get("wol_mac"),
            wol_broadcast=power.get("wol_broadcast", default_bcast),
            wol_port=power.get("wol_port", default_port),
            wol_wait_seconds=power.get("wol_wait_seconds", 10),
            battery_check=power.get("battery_guard", False),
            sunshine_check=courtesy.get("sunshine_check", False),
            priority=h.get("priority", 100),
            default_memory=limits.get("default_memory", "16g"),
            default_cpus=limits.get("default_cpus", 8),
            tags=h.get("tags", []),
            gpu_type=gpu.get("type"),
            gpu_passthrough_mode=gpu.get("passthrough_mode"),
            notes=h.get("notes", ""),
        )
        fleet[cfg.name] = cfg
    return fleet


@dataclass
class HostStatus:
    name: str
    online: bool = False
    woken: bool = False
    arch: str = ""
    engine: str = ""
    engine_ok: bool = False
    eligible: bool = False
    reason: str = ""
    load_avg: str = ""
    mem_available_gb: Optional[float] = None
    on_ac_power: Optional[bool] = None
    is_gaming: bool = False


@dataclass
class EnvReservation:
    env_id: str
    host: str
    kind: str  # "container" or "native"
    created_at: float
    expires_at: float
    ttl_seconds: int
    container_name: Optional[str] = None
    volume_name: Optional[str] = None
    workspace_path: Optional[str] = None
    image: Optional[str] = None


def parse_ttl(ttl_str: str) -> int:
    """Parse time string like '1h', '30m', '120s' into seconds."""
    m = re.match(r"^(\d+)([smhd]?)$", ttl_str.strip().lower())
    if not m:
        return 3600
    val, unit = int(m.group(1)), m.group(2)
    if unit == "m":
        return val * 60
    elif unit == "h":
        return val * 3600
    elif unit == "d":
        return val * 86400
    return val


def load_leases() -> Dict[str, EnvReservation]:
    """Load active environment leases from disk."""
    if not os.path.isfile(LEASES_FILE):
        return {}
    try:
        with open(LEASES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        leases = {}
        for k, v in data.items():
            leases[k] = EnvReservation(**v)
        return leases
    except Exception:
        return {}


def save_leases(leases: Dict[str, EnvReservation]) -> None:
    """Save environment leases to disk atomically."""
    os.makedirs(os.path.dirname(LEASES_FILE), exist_ok=True)
    tmp_path = LEASES_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({k: dataclasses.asdict(v) for k, v in leases.items()}, f, indent=2)
    os.replace(tmp_path, LEASES_FILE)


def send_magic_packet(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    """Send a Wake-on-LAN magic packet to any MAC address using pure standard library."""
    clean = re.sub(r"[^0-9A-Fa-f]", "", mac)
    if len(clean) != 12:
        raise ValueError(f"Invalid MAC address format: {mac}")
    raw_mac = bytes.fromhex(clean)
    payload = b"\xff" * 6 + raw_mac * 16

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(payload, (broadcast, port))


def run_ssh(host_alias: str, command: str, timeout: int = 15, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Execute a command over SSH using BatchMode to fail quickly on disconnect."""
    ssh_cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=4",
        host_alias,
        command,
    ]
    return subprocess.run(
        ssh_cmd,
        capture_output=capture,
        text=True,
        timeout=timeout,
        check=False,
    )


def is_ssh_open(host_alias: str) -> bool:
    """Test rapid SSH reachability without running heavy commands."""
    res = run_ssh(host_alias, "true", timeout=5)
    return res.returncode == 0


def wake_host(host: HostConfig) -> Tuple[bool, str]:
    """Send a Wake-on-LAN packet and wait for SSH. Returns (awake, message)."""
    if not host.wol_mac:
        return False, f"Host '{host.name}' has no wol_mac configured"

    try:
        send_magic_packet(host.wol_mac, host.wol_broadcast, host.wol_port)
    except Exception as e:
        return False, f"Failed to send WoL packet to {host.name}: {e}"

    deadline = time.time() + host.wol_wait_seconds
    while time.time() < deadline:
        time.sleep(1.5)
        if is_ssh_open(host.ssh_alias):
            return True, f"Host '{host.name}' is awake and answering SSH."

    return (
        False,
        f"Sent WoL magic packet to {host.wol_mac}, but host '{host.name}' did not answer within {host.wol_wait_seconds}s.",
    )


def probe_host(host: HostConfig, try_wake: bool = True) -> HostStatus:
    """Evaluate host reachability, hardware status, and eligibility."""
    status = HostStatus(name=host.name, arch=host.arch, engine=host.engine)

    # 1. Reachability
    if not is_ssh_open(host.ssh_alias):
        if try_wake and host.wol_mac:
            try:
                send_magic_packet(host.wol_mac, host.wol_broadcast, host.wol_port)
                status.woken = True
                deadline = time.time() + host.wol_wait_seconds
                while time.time() < deadline:
                    time.sleep(1.5)
                    if is_ssh_open(host.ssh_alias):
                        status.online = True
                        break
            except Exception as e:
                status.reason = f"WoL dispatch failed: {e}"
                return status

        if not status.online:
            status.reason = "Unreachable (offline or sleeping)"
            return status
    else:
        status.online = True

    # 2. Host metrics
    probe_script = """
    echo "---SYS---"
    uname -m
    echo "---LOAD---"
    uptime 2>/dev/null || cat /proc/loadavg 2>/dev/null
    echo "---MEM---"
    free -m 2>/dev/null || vm_stat 2>/dev/null
    echo "---BATT---"
    pmset -g batt 2>/dev/null || echo "N/A"
    echo "---SUNSHINE---"
    systemctl --user is-active sunshine 2>/dev/null || echo "inactive"
    echo "---ENGINE---"
    if command -v podman >/dev/null 2>&1; then podman --version; elif command -v docker >/dev/null 2>&1; then docker --version; else echo "NO_ENGINE"; fi
    """

    res = run_ssh(host.ssh_alias, probe_script, timeout=10)
    if res.returncode != 0:
        status.reason = f"Probe command error: {res.stderr.strip()}"
        return status

    output = res.stdout
    sections = {}
    current_key = "HEADER"
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("---") and line.endswith("---"):
            current_key = line.strip("-")
            sections[current_key] = []
        else:
            sections.setdefault(current_key, []).append(line)

    load_lines = sections.get("LOAD", [])
    if load_lines:
        load_match = re.search(r"load averages?:\s*([0-9.]+)", load_lines[0], re.IGNORECASE)
        if load_match:
            status.load_avg = load_match.group(1)

    mem_lines = sections.get("MEM", [])
    for line in mem_lines:
        if line.startswith("Mem:") or "Mem:" in line:
            parts = line.split()
            if len(parts) >= 7:
                try:
                    avail_mb = float(parts[6])
                    status.mem_available_gb = round(avail_mb / 1024.0, 1)
                except ValueError:
                    pass

    batt_lines = sections.get("BATT", [])
    batt_text = " ".join(batt_lines)
    if "Battery Power" in batt_text:
        status.on_ac_power = False
    elif "AC Power" in batt_text:
        status.on_ac_power = True

    sun_lines = sections.get("SUNSHINE", [])
    if sun_lines and sun_lines[0] == "active":
        status.is_gaming = True

    eng_lines = sections.get("ENGINE", [])
    eng_text = " ".join(eng_lines)
    if "NO_ENGINE" not in eng_text and ("podman" in eng_text or "Docker" in eng_text or "docker" in eng_text):
        status.engine_ok = True

    # 3. Courtesy invariants
    if not status.engine_ok:
        status.eligible = False
        status.reason = "Container engine not operational"
    elif host.battery_check and status.on_ac_power is False:
        status.eligible = False
        status.reason = "Laptop running on battery power"
    elif host.sunshine_check and status.is_gaming:
        status.eligible = False
        status.reason = "Gaming / game streaming currently active"
    else:
        status.eligible = True
        status.reason = "Ready"

    return status


def select_best_host(
    fleet: Dict[str, HostConfig],
    preferred_arch: Optional[str] = None,
    allow_wake: bool = True,
) -> Tuple[HostConfig, HostStatus]:
    """Select the best eligible host according to priority and architecture preference."""
    candidates = sorted(fleet.values(), key=lambda h: h.priority)

    if preferred_arch:
        arch_candidates = [c for c in candidates if c.arch == preferred_arch]
        if arch_candidates:
            candidates = arch_candidates

    tested: List[Tuple[HostConfig, HostStatus]] = []
    for host in candidates:
        st = probe_host(host, try_wake=allow_wake)
        tested.append((host, st))
        if st.eligible:
            return host, st

    summary = ", ".join(f"{h.name}: {s.reason}" for h, s in tested)
    raise RuntimeError(f"No eligible scavenger host available. Probed: [{summary}]")


def execute_job(
    host: HostConfig,
    command: str,
    repo_path: Optional[str] = None,
    image: str = "alpine:latest",
    mem_limit: str = "16g",
    cpus: int = 8,
    timeout_seconds: int = 1800,
    stdout: Any = None,
    stderr: Any = None,
) -> int:
    """
    One-shot job: stage repo bundle, run ephemeral container, stream output, clean up.
    `stdout`/`stderr` default to this process's streams; pass real file objects to capture.
    """
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    selinux_flag = ":Z" if host.needs_selinux_z else ""
    device_args = " ".join(host.device_flags) if host.device_flags else ""

    remote_script = f"""
set -e
JOB_DIR=$(mktemp -d /tmp/scavenger-job-XXXXXX)
trap 'rm -rf "$JOB_DIR"' EXIT

if [ -f "$JOB_DIR/repo.bundle" ]; then
    git clone -q "$JOB_DIR/repo.bundle" "$JOB_DIR/workspace"
else
    mkdir -p "$JOB_DIR/workspace"
fi

{host.engine} run --rm \\
    --memory={mem_limit} \\
    --cpus={cpus} \\
    {device_args} \\
    -v "$JOB_DIR/workspace:/workspace{selinux_flag}" \\
    -w /workspace \\
    {image} \\
    sh -c '{command}'
"""

    if repo_path and os.path.isdir(os.path.join(repo_path, ".git")):
        print(f"📦 Bundling repository from {repo_path} (HEAD)...", file=sys.stderr)
        bundle_proc = subprocess.Popen(
            ["git", "-C", repo_path, "bundle", "create", "-", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        ssh_wrapper = f"""
set -e
RECV_DIR=$(mktemp -d /tmp/scavenger-job-XXXXXX)
trap 'rm -rf "$RECV_DIR"' EXIT
cat > "$RECV_DIR/repo.bundle"
git clone -q "$RECV_DIR/repo.bundle" "$RECV_DIR/workspace"

{host.engine} run --rm \\
    --memory={mem_limit} \\
    --cpus={cpus} \\
    {device_args} \\
    -v "$RECV_DIR/workspace:/workspace{selinux_flag}" \\
    -w /workspace \\
    {image} \\
    sh -c '{command}'
"""
        print(f"🚀 Dispatching job to {host.name} ({host.arch}) inside {image}...", file=sys.stderr)
        ssh_proc = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", host.ssh_alias, ssh_wrapper],
            stdin=bundle_proc.stdout,
            stdout=out_stream,
            stderr=err_stream,
        )
        if bundle_proc.stdout:
            bundle_proc.stdout.close()

        try:
            ssh_proc.wait(timeout=timeout_seconds)
            return ssh_proc.returncode
        except subprocess.TimeoutExpired:
            ssh_proc.kill()
            print(f"❌ Job timed out after {timeout_seconds}s", file=sys.stderr)
            return 124
    else:
        print(f"🚀 Dispatching command to {host.name} ({host.arch}) inside {image}...", file=sys.stderr)
        res = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", host.ssh_alias, remote_script],
            stdout=out_stream,
            stderr=err_stream,
            timeout=timeout_seconds,
            check=False,
        )
        return res.returncode


# =============================================================================
# Unified Environment Reservation Engine (Stateful / Long-running)
# =============================================================================


def resolve_recipe(
    recipe_name: str,
    workspace_root: Optional[str] = None,
    recipes_dir: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    """
    Resolve a recipe name to a local file path and recipe type ("dockerfile" or "script").

    Search order: the recipe directory (`recipes_dir`, else `<workspace_root>/dockerfiles`
    when a workspace root is given, else `./dockerfiles`), then the DecafClaw workspace copy of
    the skill (only with a workspace root), then the recipes bundled next to this file.
    Callers get `recipes_dir` from `configured_recipes_dir`.
    """
    if not recipe_name:
        return None

    if os.path.isfile(recipe_name):
        kind = "dockerfile" if "dockerfile" in recipe_name.lower() else "script"
        return os.path.abspath(recipe_name), kind

    if recipes_dir:
        primary = recipes_dir
    elif workspace_root:
        primary = os.path.join(workspace_root, "dockerfiles")
    else:
        primary = os.path.abspath(DEFAULT_RECIPES_DIR)

    dockerfile_dirs = [primary]
    script_dirs = [primary]
    if workspace_root:
        skill_dir = os.path.join(workspace_root, "skills", "scavenger")
        dockerfile_dirs.append(os.path.join(skill_dir, "dockerfiles"))
        script_dirs.append(os.path.join(skill_dir, "setup-scripts"))
    dockerfile_dirs.append(os.path.join(SCRIPT_DIR, "dockerfiles"))
    script_dirs.append(os.path.join(SCRIPT_DIR, "setup-scripts"))

    candidates: List[Tuple[str, str]] = []
    for d in dockerfile_dirs:
        candidates.append((os.path.join(d, f"{recipe_name}.Dockerfile"), "dockerfile"))
        candidates.append((os.path.join(d, recipe_name), "dockerfile"))
    for d in script_dirs:
        candidates.append((os.path.join(d, f"{recipe_name}.sh"), "script"))
        candidates.append((os.path.join(d, f"setup-{recipe_name}.sh"), "script"))
        candidates.append((os.path.join(d, recipe_name), "script"))

    for path, kind in candidates:
        if os.path.isfile(path):
            return os.path.abspath(path), kind

    return None


def ensure_remote_image(host: HostConfig, tag: str, dockerfile_path: str) -> None:
    """Ensure a container image exists on the remote host; builds it from Dockerfile if missing."""
    if host.engine == "podman":
        check_cmd = f"podman image exists {tag}"
    else:
        check_cmd = f"docker image inspect {tag} >/dev/null 2>&1"

    res = run_ssh(host.ssh_alias, check_cmd)
    if res.returncode == 0:
        return

    print(f"🔨 Building remote recipe image '{tag}' on {host.name}...", file=sys.stderr)
    with open(dockerfile_path, "rb") as f:
        dockerfile_bytes = f.read()

    build_cmd = f"{host.engine} build -t {tag} -f - /tmp"
    ssh_proc = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", host.ssh_alias, build_cmd],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = ssh_proc.communicate(input=dockerfile_bytes, timeout=600)
    if ssh_proc.returncode != 0:
        raise RuntimeError(f"Remote container build failed on {host.name}:\n{stderr.decode('utf-8')}")
    print(f"✅ Successfully built and cached '{tag}' on {host.name}.", file=sys.stderr)


def env_create(
    fleet: Dict[str, HostConfig],
    env_id: str,
    host_name: Optional[str] = None,
    kind: str = "container",
    image: str = "alpine:latest",
    recipe: Optional[str] = None,
    setup_script: Optional[str] = None,
    sync_dir: Optional[str] = None,
    mem_limit: str = "16g",
    cpus: int = 8,
    ttl: str = "2h",
    arch: Optional[str] = None,
    workspace_root: Optional[str] = None,
    recipes_dir: Optional[str] = None,
) -> EnvReservation:
    """
    Reserve a stateful environment (container or native workspace) on the fleet.
    Preserves state across turns until env_destroy or TTL expiry.
    """
    leases = load_leases()
    now = time.time()
    ttl_sec = parse_ttl(ttl)
    expires_at = now + ttl_sec

    # Check for existing active lease with same env_id
    if env_id in leases:
        existing = leases[env_id]
        if existing.expires_at > now:
            return existing

    # Select host
    if host_name:
        host = fleet.get(host_name)
        if not host:
            raise ValueError(f"Unknown host: {host_name}")
        st = probe_host(host, try_wake=True)
        if not st.eligible:
            raise RuntimeError(f"Host {host.name} not eligible: {st.reason}")
    else:
        host, _ = select_best_host(fleet, preferred_arch=arch, allow_wake=True)

    # Resolve recipe if provided
    if recipe:
        resolved = resolve_recipe(recipe, workspace_root, recipes_dir)
        if not resolved:
            raise ValueError(f"Recipe not found: {recipe}")
        rec_path, rec_kind = resolved
        if rec_kind == "dockerfile":
            clean_rec_name = re.sub(r"[^a-zA-Z0-9_-]", "-", os.path.basename(rec_path).replace(".Dockerfile", ""))
            tag = f"scavenger-recipe-{clean_rec_name}:latest"
            ensure_remote_image(host, tag, rec_path)
            image = tag
            kind = "container"
        elif rec_kind == "script":
            setup_script = rec_path

    if kind == "native":
        for lease in leases.values():
            if lease.host == host.name and lease.kind == "native" and lease.expires_at > now and lease.env_id != env_id:
                raise RuntimeError(f"Native execution slot on {host.name} is currently leased to {lease.env_id}")

        ws_rel = f"workspaces/{env_id}"
        mkdir_res = run_ssh(host.ssh_alias, f"mkdir -p ~/{ws_rel}")
        if mkdir_res.returncode != 0:
            raise RuntimeError(f"Failed to create workspace on {host.name}: {mkdir_res.stderr}")

        res = EnvReservation(
            env_id=env_id,
            host=host.name,
            kind="native",
            created_at=now,
            expires_at=expires_at,
            ttl_seconds=ttl_sec,
            workspace_path=ws_rel,
        )

        if sync_dir and os.path.isdir(sync_dir):
            env_sync(fleet, res, direction="up", local_path=sync_dir)

    else:
        c_name = f"scavenger-env-{env_id}"
        v_name = f"scavenger-vol-{env_id}"
        selinux_flag = ":Z" if host.needs_selinux_z else ""
        device_args = " ".join(host.device_flags) if host.device_flags else ""

        create_script = f"""
set -e
{host.engine} rm -f {c_name} 2>/dev/null || true
{host.engine} volume create {v_name} >/dev/null 2>&1 || true
{host.engine} run -d \\
    --name {c_name} \\
    --label {LABEL_ENV_ID}="{env_id}" \\
    --label {LABEL_EXPIRES_AT}="{int(expires_at)}" \\
    --memory={mem_limit} \\
    --cpus={cpus} \\
    {device_args} \\
    -v {v_name}:/workspace{selinux_flag} \\
    -w /workspace \\
    {image} \\
    sleep infinity
"""
        c_res = run_ssh(host.ssh_alias, create_script, timeout=60)
        if c_res.returncode != 0:
            raise RuntimeError(f"Failed to launch container on {host.name}: {c_res.stderr}")

        res = EnvReservation(
            env_id=env_id,
            host=host.name,
            kind="container",
            created_at=now,
            expires_at=expires_at,
            ttl_seconds=ttl_sec,
            container_name=c_name,
            volume_name=v_name,
            image=image,
        )

        if sync_dir and os.path.isdir(sync_dir):
            env_sync(fleet, res, direction="up", local_path=sync_dir)

    # Execute setup_script if provided
    if setup_script and os.path.isfile(setup_script):
        print(f"⚙️ Running setup script for {res.env_id}...", file=sys.stderr)
        import base64

        with open(setup_script, "r", encoding="utf-8") as sf:
            script_content = sf.read()
        b64_setup = base64.b64encode(script_content.encode("utf-8")).decode("ascii")
        if res.kind == "native":
            ws = res.workspace_path or f"workspaces/{res.env_id}"
            run_cmd = f"cd ~/{ws} && echo '{b64_setup}' | base64 -d | sh"
        else:
            c_name = res.container_name or f"scavenger-env-{res.env_id}"
            run_cmd = f"{host.engine} exec -i {c_name} sh -c 'echo \"{b64_setup}\" | base64 -d | sh'"
        setup_proc = run_ssh(host.ssh_alias, run_cmd, timeout=300)
        if setup_proc.returncode != 0:
            raise RuntimeError(f"Setup script failed on {host.name}:\n{setup_proc.stderr}")

    leases[env_id] = res
    save_leases(leases)
    return res


def env_exec(
    fleet: Dict[str, HostConfig],
    res: EnvReservation,
    command: str,
    timeout_seconds: int = 1800,
) -> subprocess.CompletedProcess[str]:
    """Execute a command inside an active reserved environment."""
    host = fleet.get(res.host)
    if not host:
        raise ValueError(f"Unknown host {res.host} for environment {res.env_id}")

    if res.kind == "native":
        ws = res.workspace_path or f"workspaces/{res.env_id}"
        remote_cmd = f"cd ~/{ws} && {command}"
    else:
        c_name = res.container_name or f"scavenger-env-{res.env_id}"
        remote_cmd = f"{host.engine} exec -i {c_name} sh -c '{command}'"

    return run_ssh(host.ssh_alias, remote_cmd, timeout=timeout_seconds, capture=True)


def env_exec_bg(
    fleet: Dict[str, HostConfig],
    res: EnvReservation,
    command: str,
    log_file: str = ".scavenger_exec.log",
    exit_file: str = ".scavenger_exec.exit",
) -> Dict[str, Any]:
    """Execute a command in the background inside an active reserved environment."""
    host = fleet.get(res.host)
    if not host:
        raise ValueError(f"Unknown host {res.host} for environment {res.env_id}")

    import base64

    b64_cmd = base64.b64encode(command.encode("utf-8")).decode("ascii")
    inner_script = (
        f"rm -f '{exit_file}'; (echo '{b64_cmd}' | base64 -d | sh) > '{log_file}' 2>&1; echo $? > '{exit_file}'"
    )
    b64_full = base64.b64encode(inner_script.encode("utf-8")).decode("ascii")

    if res.kind == "native":
        ws = res.workspace_path or f"workspaces/{res.env_id}"
        remote_cmd = f"cd ~/{ws} && nohup sh -c 'echo \"{b64_full}\" | base64 -d | sh' >/dev/null 2>&1 & echo $!"
    else:
        c_name = res.container_name or f"scavenger-env-{res.env_id}"
        remote_cmd = f"{host.engine} exec -d {c_name} sh -c 'echo \"{b64_full}\" | base64 -d | sh'"

    proc = run_ssh(host.ssh_alias, remote_cmd, timeout=15)
    pid = proc.stdout.strip().splitlines()[-1] if proc.stdout else "unknown"
    return {
        "env_id": res.env_id,
        "host": res.host,
        "pid": pid,
        "log_file": log_file,
        "exit_file": exit_file,
    }


def env_arm_watch(
    fleet: Dict[str, HostConfig],
    res: EnvReservation,
    watch_type: str,  # "exit", "pattern", "file"
    sentinel_path: str,  # local path on DecafClaw
    target: str = ".scavenger_exec.log",  # file to check or tail
    pattern: str = "",  # regex pattern if watch_type == "pattern"
    timeout_seconds: int = 3600,
) -> Dict[str, Any]:
    """
    Arm an event watch over SSH that writes to local sentinel_path when the event fires.
    Runs asynchronously in a local detached subprocess.
    """
    host = fleet.get(res.host)
    if not host:
        raise ValueError(f"Unknown host {res.host}")

    os.makedirs(os.path.dirname(os.path.abspath(sentinel_path)), exist_ok=True)
    if os.path.exists(sentinel_path):
        try:
            os.unlink(sentinel_path)
        except OSError:
            pass

    if res.kind == "native":
        ws = res.workspace_path or f"workspaces/{res.env_id}"
    else:
        c_name = res.container_name or f"scavenger-env-{res.env_id}"

    if watch_type == "exit":
        target_file = target or ".scavenger_exec.exit"
        inner = f"while [ ! -f '{target_file}' ]; do sleep 1; done; cat '{target_file}'"
    elif watch_type == "file":
        inner = f"while [ ! -f '{target}' ]; do sleep 1; done; ls -ld '{target}'"
    elif watch_type == "pattern":
        inner = f"touch '{target}' && tail -n +1 -f '{target}' | grep -m 1 -E '{pattern}'"
    else:
        raise ValueError(f"Unknown watch_type: {watch_type}")

    import base64

    if res.kind == "native":
        full_script = f"cd ~/{ws} && {inner}"
    else:
        full_script = f"{host.engine} exec -i {c_name} sh -c '{inner}'"

    b64_cmd = base64.b64encode(full_script.encode("utf-8")).decode("ascii")
    runner_script = f"""
RESULT=$(ssh -o BatchMode=yes {host.ssh_alias} 'echo {b64_cmd} | base64 -d | sh' 2>/dev/null || echo 'WATCH_SSH_ERROR')
cat <<EOF > "{sentinel_path}"
{{"env_id": "{res.env_id}", "host": "{res.host}", "watch_type": "{watch_type}", "target": "{target}", "pattern": "{pattern}", "result": "$RESULT", "timestamp": $(date +%s)}}
EOF
"""
    subprocess.Popen(["sh", "-c", runner_script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return {
        "status": "armed",
        "env_id": res.env_id,
        "watch_type": watch_type,
        "sentinel_path": sentinel_path,
        "target": target,
        "pattern": pattern,
    }


def env_sync(
    fleet: Dict[str, HostConfig],
    res: EnvReservation,
    direction: str,
    local_path: str,
) -> None:
    """Tar-stream workspace files up to or down from the reserved environment."""
    host = fleet.get(res.host)
    if not host:
        raise ValueError(f"Unknown host {res.host}")

    if direction == "up":
        if not os.path.isdir(local_path):
            raise ValueError(f"Local path does not exist: {local_path}")

        tar_proc = subprocess.Popen(
            ["tar", "-czf", "-", "-C", local_path, "."],
            stdout=subprocess.PIPE,
        )

        if res.kind == "native":
            ws = res.workspace_path or f"workspaces/{res.env_id}"
            ssh_cmd = ["ssh", "-o", "BatchMode=yes", host.ssh_alias, f"mkdir -p ~/{ws} && tar -xzf - -C ~/{ws}"]
        else:
            c_name = res.container_name or f"scavenger-env-{res.env_id}"
            ssh_cmd = [
                "ssh",
                "-o",
                "BatchMode=yes",
                host.ssh_alias,
                f"{host.engine} exec -i {c_name} tar -xzf - -C /workspace",
            ]

        ssh_proc = subprocess.Popen(ssh_cmd, stdin=tar_proc.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if tar_proc.stdout:
            tar_proc.stdout.close()
        out, err = ssh_proc.communicate(timeout=300)
        if ssh_proc.returncode != 0:
            raise RuntimeError(f"Sync-up failed: {err.decode('utf-8')}")

    elif direction == "down":
        os.makedirs(local_path, exist_ok=True)
        if res.kind == "native":
            ws = res.workspace_path or f"workspaces/{res.env_id}"
            ssh_cmd = ["ssh", "-o", "BatchMode=yes", host.ssh_alias, f"tar -czf - -C ~/{ws} ."]
        else:
            c_name = res.container_name or f"scavenger-env-{res.env_id}"
            ssh_cmd = [
                "ssh",
                "-o",
                "BatchMode=yes",
                host.ssh_alias,
                f"{host.engine} exec {c_name} tar -czf - -C /workspace .",
            ]

        ssh_proc = subprocess.Popen(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        untar_proc = subprocess.Popen(["tar", "-xzf", "-", "-C", local_path], stdin=ssh_proc.stdout)
        if ssh_proc.stdout:
            ssh_proc.stdout.close()
        untar_proc.communicate(timeout=300)
        if untar_proc.returncode != 0:
            raise RuntimeError("Sync-down untar failed")


def env_destroy(fleet: Dict[str, HostConfig], env_id: str) -> bool:
    """Tear down a reserved environment and release its lease."""
    leases = load_leases()
    res = leases.pop(env_id, None)
    save_leases(leases)

    if not res:
        return False

    host = fleet.get(res.host)
    if not host:
        return False

    if res.kind == "native":
        ws = res.workspace_path or f"workspaces/{env_id}"
        run_ssh(host.ssh_alias, f"rm -rf ~/{ws}")
    else:
        c_name = res.container_name or f"scavenger-env-{env_id}"
        v_name = res.volume_name or f"scavenger-vol-{env_id}"
        run_ssh(
            host.ssh_alias,
            f"{host.engine} rm -f {c_name} 2>/dev/null; {host.engine} volume rm -f {v_name} 2>/dev/null || true",
        )

    return True


def env_reap(fleet: Dict[str, HostConfig]) -> List[str]:
    """Sweep and purge all expired environment leases across the fleet."""
    leases = load_leases()
    now = time.time()
    reaped = []

    for env_id, res in list(leases.items()):
        if res.expires_at < now:
            env_destroy(fleet, env_id)
            reaped.append(env_id)

    return reaped


# =============================================================================
# CLI Subcommands
# =============================================================================


class CLIError(Exception):
    """A command failure: reported as text on stderr, or as JSON with --json, and exits non-zero."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.message = message
        self.code = code


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2))


def _require_fleet(args: argparse.Namespace) -> Dict[str, HostConfig]:
    fleet = load_fleet_config(args.config)
    if not fleet:
        raise CLIError("No fleet configuration found. Create ~/.config/scavenger/fleet.json or specify --config.")
    return fleet


def _require_host(fleet: Dict[str, HostConfig], name: str) -> HostConfig:
    host = fleet.get(name)
    if not host:
        raise CLIError(f"Unknown host: {name}")
    return host


def _require_lease(env_id: str) -> EnvReservation:
    res = load_leases().get(env_id)
    if not res:
        raise CLIError(f"No active lease for environment '{env_id}'")
    return res


def _lease_target(res: EnvReservation) -> str:
    return (res.container_name if res.kind == "container" else res.workspace_path) or ""


def cmd_probe(args: argparse.Namespace) -> int:
    fleet = _require_fleet(args)

    targets = [args.host] if args.host else list(fleet.keys())
    results = []
    for name in targets:
        cfg = _require_host(fleet, name)
        status = probe_host(cfg, try_wake=args.wake)
        results.append(dataclasses.asdict(status))

    if args.json:
        _print_json(results)
    else:
        print(f"{'HOST':<14} {'ONLINE':<8} {'ARCH':<8} {'ENGINE':<8} {'AC':<6} {'STATUS':<10} {'NOTES'}")
        print("-" * 75)
        for r in results:
            ac_str = "Yes" if r["on_ac_power"] is True else ("No" if r["on_ac_power"] is False else "N/A")
            status_str = "ELIGIBLE" if r["eligible"] else "BUSY/OFF"
            print(
                f"{r['name']:<14} {str(r['online']):<8} {r['arch']:<8} {r['engine']:<8} {ac_str:<6} {status_str:<10} {r['reason']}"
            )
    return 0


def cmd_wake(args: argparse.Namespace) -> int:
    fleet = _require_fleet(args)
    host = _require_host(fleet, args.host)
    awake, message = wake_host(host)
    if args.json:
        _print_json({"host": host.name, "awake": awake, "message": message})
    elif awake:
        print(message)
    else:
        print(message, file=sys.stderr)
    return 0 if awake else 1


def cmd_run(args: argparse.Namespace) -> int:
    fleet = _require_fleet(args)

    if args.host:
        host = _require_host(fleet, args.host)
        status = probe_host(host, try_wake=args.wake)
        if not status.eligible and not args.force:
            raise CLIError(f"Host {host.name} not eligible: {status.reason} (use --force to bypass)")
    else:
        try:
            host, status = select_best_host(fleet, preferred_arch=args.arch, allow_wake=args.wake)
        except RuntimeError as e:
            raise CLIError(str(e))

    def dispatch(stdout: Any = None, stderr: Any = None) -> int:
        return execute_job(
            host=host,
            command=args.command,
            repo_path=args.repo,
            image=args.image,
            mem_limit=args.mem,
            cpus=args.cpus,
            timeout_seconds=args.timeout,
            stdout=stdout,
            stderr=stderr,
        )

    if not args.json:
        return dispatch()

    with tempfile.TemporaryFile("w+") as out, tempfile.TemporaryFile("w+") as err:
        try:
            code = dispatch(out, err)
        except subprocess.TimeoutExpired:
            code = 124
        out.seek(0)
        err.seek(0)
        _print_json({"host": host.name, "exit_code": code, "stdout": out.read(), "stderr": err.read()})
    return code


def cmd_exec_native(args: argparse.Namespace) -> int:
    fleet = _require_fleet(args)
    host = _require_host(fleet, args.host)
    try:
        proc = run_ssh(host.ssh_alias, args.command, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        raise CLIError(f"Command timed out after {args.timeout}s", code=124)
    return _emit_exec_result(args, proc, {"host": host.name})


def _emit_exec_result(args: argparse.Namespace, proc: subprocess.CompletedProcess[str], extra: Dict[str, Any]) -> int:
    if args.json:
        _print_json({**extra, "exit_code": proc.returncode, "stdout": proc.stdout or "", "stderr": proc.stderr or ""})
    else:
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
    return proc.returncode


def cmd_env_create(args: argparse.Namespace) -> int:
    fleet = _require_fleet(args)
    try:
        res = env_create(
            fleet=fleet,
            env_id=args.env_id,
            host_name=args.host,
            kind=args.kind,
            image=args.image,
            recipe=args.recipe,
            setup_script=args.setup_script,
            sync_dir=args.sync_dir,
            mem_limit=args.mem,
            cpus=args.cpus,
            ttl=args.ttl,
            arch=args.arch,
            recipes_dir=configured_recipes_dir(args.recipes_dir, args.config),
        )
    except (ValueError, RuntimeError) as e:
        raise CLIError(str(e))
    # The reservation is JSON in either mode: scripts already depend on it.
    _print_json(dataclasses.asdict(res))
    return 0


def cmd_env_exec(args: argparse.Namespace) -> int:
    fleet = _require_fleet(args)
    res = _require_lease(args.env_id)
    try:
        proc = env_exec(fleet, res, args.command, timeout_seconds=args.timeout)
    except subprocess.TimeoutExpired:
        raise CLIError(f"Command timed out after {args.timeout}s", code=124)
    except ValueError as e:
        raise CLIError(str(e))
    return _emit_exec_result(args, proc, {"env_id": res.env_id, "host": res.host})


def cmd_env_info(args: argparse.Namespace) -> int:
    res = _require_lease(args.env_id)
    remaining = int(res.expires_at - time.time())
    if args.json:
        _print_json({**dataclasses.asdict(res), "expires_in_seconds": remaining, "expired": remaining <= 0})
    else:
        print(f"Environment: {res.env_id}")
        print(f"Host:        {res.host} ({res.kind})")
        print(f"Target:      {_lease_target(res)}")
        print(f"Image:       {res.image or 'N/A'}")
        print(f"Expires In:  {f'{remaining}s' if remaining > 0 else 'EXPIRED'} (TTL: {res.ttl_seconds}s)")
    return 0


def cmd_env_sync(args: argparse.Namespace) -> int:
    fleet = _require_fleet(args)
    res = _require_lease(args.env_id)
    try:
        env_sync(fleet, res, direction=args.direction, local_path=args.local_path)
    except (ValueError, RuntimeError) as e:
        raise CLIError(str(e))
    if args.json:
        _print_json({"env_id": args.env_id, "direction": args.direction, "local_path": args.local_path, "ok": True})
    else:
        print(f"Sync {args.direction} complete for '{args.env_id}' with '{args.local_path}'.")
    return 0


def cmd_env_destroy(args: argparse.Namespace) -> int:
    fleet = _require_fleet(args)
    ok = env_destroy(fleet, args.env_id)
    if args.json:
        _print_json({"env_id": args.env_id, "destroyed": ok})
    elif ok:
        print(f"Environment '{args.env_id}' destroyed.")
    else:
        print(f"Environment '{args.env_id}' not found or already destroyed.", file=sys.stderr)
    return 0 if ok else 1


def cmd_env_list(args: argparse.Namespace) -> int:
    leases = load_leases()
    now = time.time()
    if args.json:
        _print_json({k: dataclasses.asdict(v) for k, v in leases.items()})
    else:
        print(f"{'ENV_ID':<24} {'HOST':<12} {'KIND':<10} {'EXPIRES_IN':<12} {'TARGET'}")
        print("-" * 75)
        for k, v in leases.items():
            rem = int(v.expires_at - now)
            rem_str = f"{rem}s" if rem > 0 else "EXPIRED"
            print(f"{k:<24} {v.host:<12} {v.kind:<10} {rem_str:<12} {_lease_target(v)}")
    return 0


def cmd_reap(args: argparse.Namespace) -> int:
    fleet = _require_fleet(args)
    reaped = env_reap(fleet)
    if args.json:
        _print_json({"reaped": reaped})
    elif reaped:
        print(f"Reaped {len(reaped)} expired environment(s): {', '.join(reaped)}")
    else:
        print("No expired environments to reap.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LAN Scavenger Compute Fleet Controller")
    parser.add_argument("--config", help="Path to custom fleet JSON config (default: ~/.config/scavenger/fleet.json)")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        help='Print machine-readable JSON (errors become {"error": ...}); exit code is non-zero on failure',
    )

    # Probe
    p_probe = subparsers.add_parser("probe", parents=[common], help="Probe fleet reachability and eligibility")
    p_probe.add_argument("--host", help="Specific host to probe (default: all)")
    p_probe.add_argument("--no-wake", dest="wake", action="store_false", help="Do not fire Wake-on-LAN packets")
    p_probe.set_defaults(cmd_func=cmd_probe, wake=True)

    # Wake
    p_wake = subparsers.add_parser("wake", parents=[common], help="Send Wake-on-LAN and wait for SSH")
    p_wake.add_argument("host", help="Host name from fleet.json")
    p_wake.set_defaults(cmd_func=cmd_wake)

    # One-shot Run
    p_run = subparsers.add_parser("run", parents=[common], help="Run an ephemeral container job on the scavenger fleet")
    p_run.add_argument("command", help="Shell command to run inside the container")
    p_run.add_argument("--host", help="Target specific host (bypasses automatic arbiter)")
    p_run.add_argument("--arch", choices=["arm64", "x86_64"], help="Filter by architecture")
    p_run.add_argument("--repo", help="Path to local git repo to bundle and stream into /workspace")
    p_run.add_argument("--image", default="alpine:latest", help="Container image (default: alpine:latest)")
    p_run.add_argument("--mem", default="16g", help="Memory limit (default: 16g)")
    p_run.add_argument("--cpus", type=int, default=8, help="CPU core limit (default: 8)")
    p_run.add_argument("--timeout", type=int, default=1800, help="Watchdog timeout in seconds (default: 1800)")
    p_run.add_argument("--no-wake", dest="wake", action="store_false", help="Do not fire WoL packets during selection")
    p_run.add_argument("--force", action="store_true", help="Run on host even if courtesy checks flag busy/battery")
    p_run.set_defaults(cmd_func=cmd_run, wake=True)

    # Native exec
    p_en = subparsers.add_parser(
        "exec-native", parents=[common], help="Run a command natively on a host as the scavenger user"
    )
    p_en.add_argument("host", help="Host name from fleet.json")
    p_en.add_argument("command", help="Command to execute")
    p_en.add_argument("--timeout", type=int, default=1800, help="Timeout in seconds")
    p_en.set_defaults(cmd_func=cmd_exec_native)

    # Env Create
    p_ec = subparsers.add_parser("env-create", parents=[common], help="Reserve a stateful environment")
    p_ec.add_argument("env_id", help="Unique identifier for this environment")
    p_ec.add_argument("--host", help="Target specific host")
    p_ec.add_argument("--kind", choices=["container", "native"], default="container", help="Environment backend")
    p_ec.add_argument("--image", default="alpine:latest", help="Container image")
    p_ec.add_argument("--recipe", help="Recipe name, looked up in the recipe directory (e.g. 'android-builder')")
    p_ec.add_argument(
        "--recipes-dir",
        help="Directory holding <recipe>.Dockerfile files (default: 'recipes_dir' in fleet.json, then ./dockerfiles)",
    )
    p_ec.add_argument("--setup-script", help="Path to idempotent setup script to run inside environment")
    p_ec.add_argument("--sync-dir", help="Local directory to initial-sync into /workspace")
    p_ec.add_argument("--mem", default="16g", help="Memory limit")
    p_ec.add_argument("--cpus", type=int, default=8, help="CPU limit")
    p_ec.add_argument("--ttl", default="2h", help="Time to live (e.g. '1h', '30m')")
    p_ec.add_argument("--arch", choices=["arm64", "x86_64"], help="Architecture preference")
    p_ec.set_defaults(cmd_func=cmd_env_create)

    # Env Exec
    p_ee = subparsers.add_parser("env-exec", parents=[common], help="Run command inside a reserved environment")
    p_ee.add_argument("env_id", help="Environment identifier")
    p_ee.add_argument("command", help="Command to execute")
    p_ee.add_argument("--timeout", type=int, default=1800, help="Timeout in seconds")
    p_ee.set_defaults(cmd_func=cmd_env_exec)

    # Env Info
    p_ei = subparsers.add_parser("env-info", parents=[common], help="Show details and remaining TTL of a reservation")
    p_ei.add_argument("env_id", help="Environment identifier")
    p_ei.set_defaults(cmd_func=cmd_env_info)

    # Env Sync
    p_es = subparsers.add_parser("env-sync", parents=[common], help="Sync files to/from a reserved environment")
    p_es.add_argument("env_id", help="Environment identifier")
    p_es.add_argument("direction", choices=["up", "down"], help="Sync direction")
    p_es.add_argument("local_path", help="Local directory path")
    p_es.set_defaults(cmd_func=cmd_env_sync)

    # Env Destroy
    p_ed = subparsers.add_parser("env-destroy", parents=[common], help="Destroy a reserved environment")
    p_ed.add_argument("env_id", help="Environment identifier")
    p_ed.set_defaults(cmd_func=cmd_env_destroy)

    # Env List
    p_el = subparsers.add_parser("env-list", parents=[common], help="List active environment reservations")
    p_el.set_defaults(cmd_func=cmd_env_list)

    # Reap
    p_reap = subparsers.add_parser("reap", parents=[common], help="Reap all expired environment reservations")
    p_reap.set_defaults(cmd_func=cmd_reap)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        code = args.cmd_func(args)
    except CLIError as e:
        if args.json:
            _print_json({"error": e.message})
        else:
            print(f"Error: {e.message}", file=sys.stderr)
        sys.exit(e.code)
    sys.exit(code)


if __name__ == "__main__":
    main()
