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


def find_fleet_config(config_path: Optional[str] = None) -> Optional[str]:
    """Return the fleet.json path to use (explicit path or first existing fallback), or None."""
    if not config_path:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.expanduser("~/.config/scavenger/fleet.json"),
            os.path.join(script_dir, "..", "config", "scavenger_fleet.json"),
            os.path.join(os.getcwd(), "config", "scavenger_fleet.json"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                config_path = c
                break

    if not config_path or not os.path.isfile(config_path):
        return None
    return config_path


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


DEFAULT_RECIPES_DIR = "dockerfiles"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Container labels. The decafclaw.* names were used before the engine was made standalone;
# readers accept both so containers created earlier are still found and reaped.
LABEL_ENV_ID = "scavenger.env_id"
LABEL_EXPIRES_AT = "scavenger.expires_at"
LEGACY_LABEL_ENV_ID = "decafclaw.env_id"
LEGACY_LABEL_EXPIRES_AT = "decafclaw.expires_at"


def read_env_labels(labels: Dict[str, str]) -> Tuple[Optional[str], Optional[int]]:
    """Read (env_id, expires_at) from container labels, accepting scavenger.* and legacy decafclaw.* names."""
    env_id = labels.get(LABEL_ENV_ID) or labels.get(LEGACY_LABEL_ENV_ID) or None
    raw_expires = labels.get(LABEL_EXPIRES_AT) or labels.get(LEGACY_LABEL_EXPIRES_AT)
    try:
        expires_at = int(raw_expires) if raw_expires else None
    except ValueError:
        expires_at = None
    return env_id, expires_at


def resolve_recipes_dir(
    flag: Optional[str] = None,
    config_path: Optional[str] = None,
    default: Optional[str] = None,
) -> str:
    """Pick the recipe directory: --recipes-dir flag, then fleet.json `recipes_dir`, then `default` or ./dockerfiles.

    A relative `recipes_dir` in fleet.json is resolved against the directory holding that file.
    """
    if flag:
        return os.path.abspath(os.path.expanduser(flag))

    found = find_fleet_config(config_path)
    if found:
        with open(found, "r", encoding="utf-8") as f:
            configured = json.load(f).get("recipes_dir")
        if configured:
            configured = os.path.expanduser(configured)
            if not os.path.isabs(configured):
                configured = os.path.join(os.path.dirname(os.path.abspath(found)), configured)
            return os.path.abspath(configured)

    return os.path.abspath(default or DEFAULT_RECIPES_DIR)


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


def wake_host(host: HostConfig) -> Dict[str, Any]:
    """Send a Wake-on-LAN packet to a host and wait for it to answer SSH.

    Returns {"host", "awake", "message"}; `awake` is False on any failure.
    """
    result: Dict[str, Any] = {"host": host.name, "awake": False, "message": ""}
    if not host.wol_mac:
        result["message"] = f"Host '{host.name}' has no wol_mac configured"
        return result

    try:
        send_magic_packet(host.wol_mac, host.wol_broadcast, host.wol_port)
    except Exception as e:
        result["message"] = f"Failed to send WoL packet to {host.name}: {e}"
        return result

    deadline = time.time() + host.wol_wait_seconds
    while time.time() < deadline:
        time.sleep(1.5)
        if is_ssh_open(host.ssh_alias):
            result["awake"] = True
            result["message"] = f"Host '{host.name}' is awake and answering SSH."
            return result

    result["message"] = (
        f"Sent WoL magic packet to {host.wol_mac}, but host '{host.name}' "
        f"did not answer within {host.wol_wait_seconds}s."
    )
    return result


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
    """One-shot job: stage repo bundle, run ephemeral container, stream output, clean up.

    `stdout`/`stderr` default to this process's streams; pass real files to capture output.
    """
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
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
            stdout=out,
            stderr=err,
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
            stdout=out,
            stderr=err,
            timeout=timeout_seconds,
            check=False,
        )
        return res.returncode


# =============================================================================
# Unified Environment Reservation Engine (Stateful / Long-running)
# =============================================================================


def resolve_recipe(recipe_name: str, recipes_dir: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """
    Resolve a recipe name to a local file path and recipe type ("dockerfile" or "script").

    Searches `recipes_dir` (default ./dockerfiles), its sibling `setup-scripts/`, then the
    dockerfiles/ and setup-scripts/ shipped next to this file.
    """
    if not recipe_name:
        return None

    if os.path.isfile(recipe_name):
        kind = "dockerfile" if "dockerfile" in recipe_name.lower() else "script"
        return os.path.abspath(recipe_name), kind

    base = os.path.abspath(recipes_dir or DEFAULT_RECIPES_DIR)
    dockerfile_dirs = [base, os.path.join(SCRIPT_DIR, "dockerfiles")]
    script_dirs = [os.path.join(os.path.dirname(base), "setup-scripts"), os.path.join(SCRIPT_DIR, "setup-scripts")]

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
        resolved = resolve_recipe(recipe, recipes_dir)
        if not resolved:
            raise ValueError(f"Recipe '{recipe}' not found (searched {recipes_dir or DEFAULT_RECIPES_DIR})")
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


def _emit(args: argparse.Namespace, data: Any, text: str = "") -> None:
    """Print `data` as JSON when --json is set, otherwise the human-readable `text`."""
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
    elif text:
        print(text)


def _fail(args: argparse.Namespace, message: str, code: int = 1) -> int:
    """Report an error (JSON object on stdout with --json, plain text on stderr) and return the exit code."""
    if getattr(args, "json", False):
        print(json.dumps({"error": message}, indent=2))
    else:
        print(f"Error: {message}", file=sys.stderr)
    return code


def _load_fleet(args: argparse.Namespace) -> Dict[str, HostConfig]:
    fleet = load_fleet_config(args.config)
    if not fleet:
        raise ValueError("No fleet configuration found. Create ~/.config/scavenger/fleet.json or specify --config.")
    return fleet


def _lease_dict(res: EnvReservation, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    return {**dataclasses.asdict(res), "expires_in_seconds": int(res.expires_at - now)}


def cmd_probe(args: argparse.Namespace) -> int:
    fleet = _load_fleet(args)

    targets = [args.host] if args.host else list(fleet.keys())
    results = []
    unknown = []
    for name in targets:
        cfg = fleet.get(name)
        if not cfg:
            unknown.append(name)
            continue
        status = probe_host(cfg, try_wake=args.wake)
        results.append(dataclasses.asdict(status))

    if unknown:
        return _fail(args, f"Unknown host: {', '.join(unknown)}")

    lines = [f"{'HOST':<14} {'ONLINE':<8} {'ARCH':<8} {'ENGINE':<8} {'AC':<6} {'STATUS':<10} {'NOTES'}", "-" * 75]
    for r in results:
        ac_str = "Yes" if r["on_ac_power"] is True else ("No" if r["on_ac_power"] is False else "N/A")
        status_str = "ELIGIBLE" if r["eligible"] else "BUSY/OFF"
        lines.append(
            f"{r['name']:<14} {str(r['online']):<8} {r['arch']:<8} {r['engine']:<8} {ac_str:<6} {status_str:<10} {r['reason']}"
        )
    _emit(args, results, "\n".join(lines))
    return 0


def cmd_wake(args: argparse.Namespace) -> int:
    fleet = _load_fleet(args)
    host = fleet.get(args.host)
    if not host:
        return _fail(args, f"Unknown host: {args.host}")

    result = wake_host(host)
    _emit(args, result, result["message"])
    return 0 if result["awake"] else 1


def cmd_run(args: argparse.Namespace) -> int:
    fleet = _load_fleet(args)

    if args.host:
        host = fleet.get(args.host)
        if not host:
            return _fail(args, f"Unknown host: {args.host}")
        status = probe_host(host, try_wake=args.wake)
        if not status.eligible and not args.force:
            return _fail(args, f"Host {host.name} not eligible: {status.reason} (use --force to bypass)")
    else:
        host, status = select_best_host(fleet, preferred_arch=args.arch, allow_wake=args.wake)

    job_args: Dict[str, Any] = dict(
        host=host,
        command=args.command,
        repo_path=args.repo,
        image=args.image,
        mem_limit=args.mem,
        cpus=args.cpus,
        timeout_seconds=args.timeout,
    )
    if not args.json:
        return execute_job(**job_args)

    with tempfile.TemporaryFile(mode="w+") as out_f, tempfile.TemporaryFile(mode="w+") as err_f:
        code = execute_job(**job_args, stdout=out_f, stderr=err_f)
        out_f.seek(0)
        err_f.seek(0)
        _emit(args, {"host": host.name, "exit_code": code, "stdout": out_f.read(), "stderr": err_f.read()})
    return code


def cmd_exec_native(args: argparse.Namespace) -> int:
    fleet = _load_fleet(args)
    host = fleet.get(args.host)
    if not host:
        return _fail(args, f"Unknown host: {args.host}")

    proc = run_ssh(host.ssh_alias, args.command, timeout=args.timeout)
    if args.json:
        _emit(args, {"host": host.name, "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
    else:
        sys.stdout.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
    return proc.returncode


def cmd_env_create(args: argparse.Namespace) -> int:
    fleet = _load_fleet(args)
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
        recipes_dir=resolve_recipes_dir(args.recipes_dir, args.config),
    )
    # env-create has always printed JSON; keep that regardless of --json.
    print(json.dumps(dataclasses.asdict(res), indent=2))
    return 0


def _get_lease(args: argparse.Namespace) -> Optional[EnvReservation]:
    return load_leases().get(args.env_id)


def cmd_env_exec(args: argparse.Namespace) -> int:
    fleet = _load_fleet(args)
    res = _get_lease(args)
    if not res:
        return _fail(args, f"No active lease for environment '{args.env_id}'")

    proc = env_exec(fleet, res, args.command, timeout_seconds=args.timeout)
    if args.json:
        _emit(args, {"env_id": res.env_id, "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
    else:
        sys.stdout.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
    return proc.returncode


def cmd_env_info(args: argparse.Namespace) -> int:
    res = _get_lease(args)
    if not res:
        return _fail(args, f"No active lease for environment '{args.env_id}'")

    info = _lease_dict(res)
    rem = info["expires_in_seconds"]
    target = res.container_name if res.kind == "container" else (res.workspace_path or "")
    text = (
        f"Environment: {res.env_id}\n"
        f"Host:        {res.host} ({res.kind})\n"
        f"Target:      {target}\n"
        f"Image:       {res.image or 'N/A'}\n"
        f"Expires In:  {f'{rem}s' if rem > 0 else 'EXPIRED'} (TTL: {res.ttl_seconds}s)"
    )
    _emit(args, info, text)
    return 0


def cmd_env_sync(args: argparse.Namespace) -> int:
    fleet = _load_fleet(args)
    res = _get_lease(args)
    if not res:
        return _fail(args, f"No active lease for environment '{args.env_id}'")

    env_sync(fleet, res, direction=args.direction, local_path=args.local_path)
    message = f"Sync {args.direction} complete for '{args.env_id}' with '{args.local_path}'."
    _emit(
        args,
        {"env_id": args.env_id, "direction": args.direction, "local_path": args.local_path, "ok": True},
        message,
    )
    return 0


def cmd_env_destroy(args: argparse.Namespace) -> int:
    fleet = _load_fleet(args)
    ok = env_destroy(fleet, args.env_id)
    if ok:
        _emit(args, {"env_id": args.env_id, "destroyed": True}, f"Environment '{args.env_id}' destroyed.")
        return 0
    return _fail(args, f"Environment '{args.env_id}' not found or already destroyed.")


def cmd_env_list(args: argparse.Namespace) -> int:
    leases = load_leases()
    now = time.time()
    lines = [f"{'ENV_ID':<24} {'HOST':<12} {'KIND':<10} {'EXPIRES_IN':<12} {'TARGET'}", "-" * 75]
    for k, v in leases.items():
        rem = int(v.expires_at - now)
        rem_str = f"{rem}s" if rem > 0 else "EXPIRED"
        target = v.container_name if v.kind == "container" else (v.workspace_path or "")
        lines.append(f"{k:<24} {v.host:<12} {v.kind:<10} {rem_str:<12} {target}")
    # JSON shape (env_id -> lease) is unchanged; consumers read expires_at.
    _emit(args, {k: dataclasses.asdict(v) for k, v in leases.items()}, "\n".join(lines))
    return 0


def cmd_reap(args: argparse.Namespace) -> int:
    fleet = _load_fleet(args)
    reaped = env_reap(fleet)
    text = (
        f"Reaped {len(reaped)} expired environment(s): {', '.join(reaped)}"
        if reaped
        else "No expired environments to reap."
    )
    _emit(args, {"reaped": reaped}, text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LAN Scavenger Compute Fleet Controller")
    parser.add_argument("--config", help="Path to custom fleet JSON config (default: ~/.config/scavenger/fleet.json)")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    # Every subcommand accepts --json; failures return a non-zero exit code.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    def add(name: str, help_text: str, func: Any, **defaults: Any) -> argparse.ArgumentParser:
        p = subparsers.add_parser(name, help=help_text, parents=[common])
        p.set_defaults(cmd_func=func, **defaults)
        return p

    p_probe = add("probe", "Probe fleet reachability and eligibility", cmd_probe, wake=True)
    p_probe.add_argument("--host", help="Specific host to probe (default: all)")
    p_probe.add_argument("--no-wake", dest="wake", action="store_false", help="Do not fire Wake-on-LAN packets")

    p_wake = add("wake", "Send a Wake-on-LAN packet and wait for SSH", cmd_wake)
    p_wake.add_argument("host", help="Host name from fleet.json")

    p_run = add("run", "Run an ephemeral container job on the scavenger fleet", cmd_run, wake=True)
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

    p_en = add("exec-native", "Run a command natively on a host as the scavenger user", cmd_exec_native)
    p_en.add_argument("host", help="Host name from fleet.json")
    p_en.add_argument("command", help="Command to execute")
    p_en.add_argument("--timeout", type=int, default=1800, help="Timeout in seconds")

    p_ec = add("env-create", "Reserve a stateful environment (always prints JSON)", cmd_env_create)
    p_ec.add_argument("env_id", help="Unique identifier for this environment")
    p_ec.add_argument("--host", help="Target specific host")
    p_ec.add_argument("--kind", choices=["container", "native"], default="container", help="Environment backend")
    p_ec.add_argument("--image", default="alpine:latest", help="Container image")
    p_ec.add_argument("--recipe", help="Recipe name from the recipes directory (e.g. 'android-builder', 'node-dev')")
    p_ec.add_argument(
        "--recipes-dir",
        help="Directory of recipe Dockerfiles (default: `recipes_dir` in fleet.json, then ./dockerfiles)",
    )
    p_ec.add_argument("--setup-script", help="Path to idempotent setup script to run inside environment")
    p_ec.add_argument("--sync-dir", help="Local directory to initial-sync into /workspace")
    p_ec.add_argument("--mem", default="16g", help="Memory limit")
    p_ec.add_argument("--cpus", type=int, default=8, help="CPU limit")
    p_ec.add_argument("--ttl", default="2h", help="Time to live (e.g. '1h', '30m')")
    p_ec.add_argument("--arch", choices=["arm64", "x86_64"], help="Architecture preference")

    p_ee = add("env-exec", "Run command inside a reserved environment", cmd_env_exec)
    p_ee.add_argument("env_id", help="Environment identifier")
    p_ee.add_argument("command", help="Command to execute")
    p_ee.add_argument("--timeout", type=int, default=1800, help="Timeout in seconds")

    p_ei = add("env-info", "Show details and remaining TTL for one environment", cmd_env_info)
    p_ei.add_argument("env_id", help="Environment identifier")

    p_es = add("env-sync", "Sync files to/from a reserved environment", cmd_env_sync)
    p_es.add_argument("env_id", help="Environment identifier")
    p_es.add_argument("direction", choices=["up", "down"], help="Sync direction")
    p_es.add_argument("local_path", help="Local directory path")

    p_ed = add("env-destroy", "Destroy a reserved environment", cmd_env_destroy)
    p_ed.add_argument("env_id", help="Environment identifier")

    add("env-list", "List active environment reservations", cmd_env_list)
    add("reap", "Reap all expired environment reservations", cmd_reap)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.cmd_func(args)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as e:
        return _fail(args, str(e))


if __name__ == "__main__":
    sys.exit(main())
