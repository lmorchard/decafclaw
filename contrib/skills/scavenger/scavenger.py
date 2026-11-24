#!/usr/bin/env python3
"""
scavenger.py — Ephemeral container and reserved environment execution across LAN scavenger fleet.

Agentless, deterministic orchestration over SSH for running heavy compilation,
test suites, subagent sessions, and ML/GPU workloads on idle workstations and laptops.
Zero daemons on targets. Hard blast-radius containment via dedicated scavenger user.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import fcntl
import hashlib
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import IO, Any, Callable, Dict, Iterator, List, Optional, Tuple

log = logging.getLogger(__name__)


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


def load_fleet_config(config_path: Optional[str] = None) -> Dict[str, HostConfig]:
    """Load declarative fleet configuration from JSON file with fallback paths."""
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


# -----------------------------------------------------------------------------
# Lease cache (~/.config/scavenger/leases.json)
#
# The hosts are the record of their environments: containers and volumes carry
# decafclaw.* labels, and native workspaces hold a small lease file. The local
# leases.json is only a cache, rebuilt from the hosts by reconcile_leases().
# -----------------------------------------------------------------------------

LABEL_ENV_ID = "decafclaw.env_id"
LABEL_EXPIRES_AT = "decafclaw.expires_at"
LABEL_CREATED_AT = "decafclaw.created_at"
LABEL_TTL_SECONDS = "decafclaw.ttl_seconds"
NATIVE_LEASE_FILE = ".scavenger-lease.json"


@contextlib.contextmanager
def _leases_lock() -> Iterator[None]:
    """Hold an exclusive advisory lock for read-modify-write cycles on the cache."""
    os.makedirs(os.path.dirname(LEASES_FILE), exist_ok=True)
    with open(LEASES_FILE + ".lock", "a", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def load_leases() -> Dict[str, EnvReservation]:
    """Load the lease cache from disk.

    A corrupt cache is logged and moved aside (never silently treated as the
    truth); callers rebuild it from the hosts with reconcile_leases().
    """
    if not os.path.isfile(LEASES_FILE):
        return {}
    try:
        with open(LEASES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: EnvReservation(**v) for k, v in data.items()}
    except (OSError, ValueError, TypeError, AttributeError) as e:
        quarantine = f"{LEASES_FILE}.corrupt-{int(time.time())}"
        log.error(
            "Lease cache %s is unreadable (%s); moving to %s and rebuilding from hosts", LEASES_FILE, e, quarantine
        )
        try:
            os.replace(LEASES_FILE, quarantine)
        except OSError as move_err:
            log.error("Could not move corrupt lease cache aside: %s", move_err)
        return {}


def _write_leases(leases: Dict[str, EnvReservation]) -> None:
    os.makedirs(os.path.dirname(LEASES_FILE), exist_ok=True)
    tmp_path = LEASES_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({k: dataclasses.asdict(v) for k, v in leases.items()}, f, indent=2)
    os.replace(tmp_path, LEASES_FILE)


def save_leases(leases: Dict[str, EnvReservation]) -> None:
    """Save the lease cache to disk atomically, under the file lock."""
    with _leases_lock():
        _write_leases(leases)


def update_leases(mutate: Callable[[Dict[str, EnvReservation]], None]) -> Dict[str, EnvReservation]:
    """Read-modify-write the cache under the file lock so concurrent writers do not lose updates."""
    with _leases_lock():
        leases = load_leases()
        mutate(leases)
        _write_leases(leases)
        return leases


def parse_engine_json(text: str) -> List[Dict[str, Any]]:
    """Parse `ps`/`volume ls --format json` output: a JSON array (podman) or one object per line (docker)."""
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        data = json.loads(text)
        return [d for d in data if isinstance(d, dict)]
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def parse_labels(raw: Any) -> Dict[str, str]:
    """Normalize labels: podman gives a dict, docker a 'k=v,k2=v2' string."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    labels = {}
    for part in str(raw).split(","):
        key, sep, value = part.partition("=")
        if sep:
            labels[key.strip()] = value
    return labels


def _reservation_from_labels(
    labels: Dict[str, str],
    host_name: str,
    container_name: Optional[str] = None,
    volume_name: Optional[str] = None,
    image: Optional[str] = None,
) -> Optional[EnvReservation]:
    env_id = labels.get(LABEL_ENV_ID)
    if not env_id:
        return None
    try:
        expires_at = float(labels[LABEL_EXPIRES_AT])
    except (KeyError, ValueError):
        log.warning("Environment %s on %s has no valid %s label; ignoring it", env_id, host_name, LABEL_EXPIRES_AT)
        return None
    try:
        created_at = float(labels.get(LABEL_CREATED_AT, 0.0))
    except ValueError:
        created_at = 0.0
    try:
        ttl_seconds = int(labels[LABEL_TTL_SECONDS])
    except (KeyError, ValueError):
        ttl_seconds = int(expires_at - created_at) if created_at else 0
    return EnvReservation(
        env_id=env_id,
        host=host_name,
        kind="container",
        created_at=created_at,
        expires_at=expires_at,
        ttl_seconds=ttl_seconds,
        container_name=container_name,
        volume_name=volume_name,
        image=image,
    )


def native_lease_json(res: EnvReservation) -> str:
    """Single-line JSON written into a native workspace as its lease file."""
    return json.dumps(
        {
            "env_id": res.env_id,
            "created_at": res.created_at,
            "expires_at": res.expires_at,
            "ttl_seconds": res.ttl_seconds,
        }
    )


def list_host_script(engine: str) -> str:
    """Remote script that lists labelled containers, labelled volumes, and native lease files."""
    return f"""
echo ---CONTAINERS---
{engine} ps --all --filter label={LABEL_ENV_ID} --format json || echo ---FAILED---
echo ---VOLUMES---
{engine} volume ls --filter label={LABEL_ENV_ID} --format json || echo ---FAILED---
echo ---NATIVE---
for f in "$HOME"/workspaces/*/{NATIVE_LEASE_FILE}; do
    if [ -f "$f" ]; then cat "$f"; echo; fi
done
echo ---END---
"""


def parse_host_listing(host_name: str, output: str) -> Optional[List[EnvReservation]]:
    """Turn list_host_script output into reservations. None means the listing is not trustworthy."""
    markers = {"---CONTAINERS---": "containers", "---VOLUMES---": "volumes", "---NATIVE---": "native"}
    sections: Dict[str, List[str]] = {"containers": [], "volumes": [], "native": []}
    current: Optional[str] = None
    complete = False
    for line in output.splitlines():
        line = line.strip()
        if line == "---FAILED---":
            return None
        if line == "---END---":
            complete = True
            break
        if line in markers:
            current = markers[line]
        elif current:
            sections[current].append(line)
    if not complete:
        return None

    found: Dict[str, EnvReservation] = {}
    try:
        for row in parse_engine_json("\n".join(sections["containers"])):
            names = row.get("Names")
            name = names[0] if isinstance(names, list) and names else (names if isinstance(names, str) else None)
            res = _reservation_from_labels(
                parse_labels(row.get("Labels")),
                host_name,
                container_name=name,
                volume_name=None,
                image=row.get("Image"),
            )
            if res:
                res.volume_name = f"scavenger-vol-{res.env_id}"
                found[res.env_id] = res
        for row in parse_engine_json("\n".join(sections["volumes"])):
            res = _reservation_from_labels(parse_labels(row.get("Labels")), host_name, volume_name=row.get("Name"))
            if not res:
                continue
            if res.env_id in found:
                found[res.env_id].volume_name = res.volume_name
            else:
                found[res.env_id] = res
    except ValueError as e:
        log.warning("Could not parse container listing from %s: %s", host_name, e)
        return None

    for line in sections["native"]:
        if not line:
            continue
        try:
            data = json.loads(line)
            env_id = str(data["env_id"])
            found[env_id] = EnvReservation(
                env_id=env_id,
                host=host_name,
                kind="native",
                created_at=float(data.get("created_at", 0.0)),
                expires_at=float(data["expires_at"]),
                ttl_seconds=int(data.get("ttl_seconds", 0)),
                workspace_path=f"workspaces/{env_id}",
            )
        except (ValueError, KeyError, TypeError) as e:
            log.warning("Ignoring unreadable native lease file on %s (%s): %r", host_name, e, line)
    return list(found.values())


def list_host_envs(host: HostConfig) -> Optional[List[EnvReservation]]:
    """List the environments a host holds, from its own labels and lease files.

    Returns None when the host cannot be queried (unreachable, or no usable engine),
    so callers keep their cached records instead of treating the host as empty.
    """
    try:
        res = run_ssh(host.ssh_alias, list_host_script(host.engine), timeout=20)
    except subprocess.TimeoutExpired:
        log.warning("Timed out listing environments on %s", host.name)
        return None
    if res.returncode != 0:
        log.warning("Could not list environments on %s: %s", host.name, (res.stderr or "").strip())
        return None
    return parse_host_listing(host.name, res.stdout)


def _merge_record(host_rec: EnvReservation, cached: Optional[EnvReservation]) -> EnvReservation:
    """Host record wins; the cache only fills fields the host record does not carry."""
    if cached is None:
        return host_rec
    merged = {}
    for f in dataclasses.fields(host_rec):
        value = getattr(host_rec, f.name)
        merged[f.name] = value if value else getattr(cached, f.name)
    return EnvReservation(**merged)


def reconcile_leases(fleet: Dict[str, HostConfig]) -> Tuple[Dict[str, EnvReservation], List[str]]:
    """Rebuild the cache from what each reachable host reports.

    Entries on unreachable hosts are kept as cached. Returns (leases, unreachable host names).
    """
    started = time.time()
    listings: Dict[str, Dict[str, EnvReservation]] = {}
    unreachable: List[str] = []
    for name, host in fleet.items():
        envs = list_host_envs(host)
        if envs is None:
            unreachable.append(name)
        else:
            listings[name] = {e.env_id: e for e in envs}

    def apply(cache: Dict[str, EnvReservation]) -> None:
        for env_id, res in list(cache.items()):
            listing = listings.get(res.host)
            # An entry created after the scan began may simply be newer than the listing.
            if listing is not None and env_id not in listing and res.created_at < started:
                del cache[env_id]
        for listing in listings.values():
            for env_id, rec in listing.items():
                cache[env_id] = _merge_record(rec, cache.get(env_id))

    return update_leases(apply), unreachable


def find_lease(fleet: Dict[str, HostConfig], env_id: str) -> Optional[EnvReservation]:
    """Look up one environment in the cache, rebuilding the cache from the hosts on a miss."""
    res = load_leases().get(env_id)
    if res:
        return res
    leases, _ = reconcile_leases(fleet)
    return leases.get(env_id)


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
    stdout: Optional[IO[str]] = None,
) -> int:
    """One-shot job: stage repo bundle, run ephemeral container, stream output, clean up.

    ``stdout`` must be a real file (it needs a fileno); it defaults to ``sys.stdout``.
    """
    out_stream = stdout if stdout is not None else sys.stdout
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
            stderr=sys.stderr,
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
            stderr=sys.stderr,
            timeout=timeout_seconds,
            check=False,
        )
        return res.returncode


# =============================================================================
# Unified Environment Reservation Engine (Stateful / Long-running)
# =============================================================================


def resolve_recipe(recipe_name: str, workspace_root: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """
    Resolve a recipe name to a local file path and recipe type ("dockerfile" or "script").
    Checks workspace dockerfiles/, skills/scavenger/dockerfiles/, and skills/scavenger/setup-scripts/.
    """
    if not recipe_name:
        return None

    if os.path.isfile(recipe_name):
        kind = "dockerfile" if "dockerfile" in recipe_name.lower() else "script"
        return os.path.abspath(recipe_name), kind

    ws = workspace_root or "/home/lmorchard/.decafclaw/decafclaw/workspace"
    candidates = [
        (os.path.join(ws, "dockerfiles", f"{recipe_name}.Dockerfile"), "dockerfile"),
        (os.path.join(ws, "dockerfiles", recipe_name), "dockerfile"),
        (os.path.join(ws, "skills", "scavenger", "dockerfiles", f"{recipe_name}.Dockerfile"), "dockerfile"),
        (os.path.join(ws, "skills", "scavenger", "dockerfiles", recipe_name), "dockerfile"),
        (os.path.join(ws, "skills", "scavenger", "setup-scripts", f"{recipe_name}.sh"), "script"),
        (os.path.join(ws, "skills", "scavenger", "setup-scripts", f"setup-{recipe_name}.sh"), "script"),
        (os.path.join(ws, "skills", "scavenger", "setup-scripts", recipe_name), "script"),
    ]

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
) -> EnvReservation:
    """
    Reserve a stateful environment (container or native workspace) on the fleet.
    Preserves state across turns until env_destroy or TTL expiry.
    """
    now = time.time()
    ttl_sec = parse_ttl(ttl)
    expires_at = now + ttl_sec

    # Check for existing active lease with same env_id (cache first, then the hosts)
    existing = find_lease(fleet, env_id)
    if existing and existing.expires_at > now:
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
        resolved = resolve_recipe(recipe, workspace_root)
        if resolved:
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
        # Single occupancy is decided by the lease files on the host, not the local cache.
        on_host = list_host_envs(host)
        if on_host is None:
            raise RuntimeError(f"Cannot verify native execution slot on {host.name}: host listing failed")
        for lease in on_host:
            if lease.kind == "native" and lease.expires_at > now and lease.env_id != env_id:
                raise RuntimeError(f"Native execution slot on {host.name} is currently leased to {lease.env_id}")

        ws_rel = f"workspaces/{env_id}"
        res = EnvReservation(
            env_id=env_id,
            host=host.name,
            kind="native",
            created_at=now,
            expires_at=expires_at,
            ttl_seconds=ttl_sec,
            workspace_path=ws_rel,
        )
        b64_lease = base64.b64encode(native_lease_json(res).encode("utf-8")).decode("ascii")
        mkdir_res = run_ssh(
            host.ssh_alias,
            f"mkdir -p ~/{ws_rel} && echo '{b64_lease}' | base64 -d > ~/{ws_rel}/{NATIVE_LEASE_FILE}",
        )
        if mkdir_res.returncode != 0:
            raise RuntimeError(f"Failed to create workspace on {host.name}: {mkdir_res.stderr}")

        if sync_dir and os.path.isdir(sync_dir):
            env_sync(fleet, res, direction="up", local_path=sync_dir)

    else:
        c_name = f"scavenger-env-{env_id}"
        v_name = f"scavenger-vol-{env_id}"
        selinux_flag = ":Z" if host.needs_selinux_z else ""
        device_args = " ".join(host.device_flags) if host.device_flags else ""
        labels = " ".join(
            f'--label {k}="{v}"'
            for k, v in (
                (LABEL_ENV_ID, env_id),
                (LABEL_EXPIRES_AT, int(expires_at)),
                (LABEL_CREATED_AT, int(now)),
                (LABEL_TTL_SECONDS, ttl_sec),
            )
        )

        create_script = f"""
set -e
{host.engine} rm -f {c_name} 2>/dev/null || true
{host.engine} volume create {labels} {v_name} >/dev/null 2>&1 || true
{host.engine} run -d \\
    --name {c_name} \\
    {labels} \\
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

    def record(leases: Dict[str, EnvReservation]) -> None:
        leases[env_id] = res

    update_leases(record)
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
    """Tear down a reserved environment on its host and drop it from the cache.

    The cache entry is kept when the host teardown fails, so the environment is not orphaned.
    """
    res = find_lease(fleet, env_id)
    if not res:
        return False

    host = fleet.get(res.host)
    if not host:
        return False

    if res.kind == "native":
        ws = res.workspace_path or f"workspaces/{env_id}"
        run = run_ssh(host.ssh_alias, f"rm -rf ~/{ws}")
    else:
        c_name = res.container_name or f"scavenger-env-{env_id}"
        v_name = res.volume_name or f"scavenger-vol-{env_id}"
        run = run_ssh(
            host.ssh_alias,
            f"{host.engine} rm -f {c_name} 2>/dev/null; {host.engine} volume rm -f {v_name} 2>/dev/null || true",
        )
    if run.returncode != 0:
        log.error("Teardown of %s on %s failed: %s", env_id, host.name, (run.stderr or "").strip())
        return False

    def drop(leases: Dict[str, EnvReservation]) -> None:
        leases.pop(env_id, None)

    update_leases(drop)
    return True


def env_reap(fleet: Dict[str, HostConfig]) -> List[str]:
    """Sweep and purge all expired environments, as reported by each reachable host."""
    leases, unreachable = reconcile_leases(fleet)
    for name in unreachable:
        log.warning("Host %s unreachable; its environments were not reaped", name)
    now = time.time()
    reaped = []

    for env_id, res in list(leases.items()):
        if res.host in unreachable:
            continue
        if res.expires_at < now and env_destroy(fleet, env_id):
            reaped.append(env_id)

    return reaped


# =============================================================================
# End-to-end selfcheck (manual, against one real host)
# =============================================================================


class SelfcheckFailure(Exception):
    """A selfcheck step failed; the message is shown on the FAIL line."""


@dataclass
class SelfcheckOps:
    """The host operations selfcheck drives. Tests substitute fakes for these."""

    probe: Callable[[HostConfig], HostStatus]
    run_job: Callable[..., int]
    env_create: Callable[..., EnvReservation]
    env_exec: Callable[[EnvReservation, str], "subprocess.CompletedProcess[str]"]
    env_sync: Callable[[EnvReservation, str, str], None]
    env_destroy: Callable[[str], bool]
    ssh: Callable[[HostConfig, str], "subprocess.CompletedProcess[str]"]


def build_selfcheck_ops(fleet: Dict[str, HostConfig], wake: bool = True) -> SelfcheckOps:
    """Bind the real fleet operations into a SelfcheckOps."""
    return SelfcheckOps(
        probe=lambda host: probe_host(host, try_wake=wake),
        run_job=execute_job,
        env_create=lambda **kw: env_create(fleet, **kw),
        env_exec=lambda res, cmd: env_exec(fleet, res, cmd, timeout_seconds=120),
        env_sync=lambda res, direction, path: env_sync(fleet, res, direction=direction, local_path=path),
        env_destroy=lambda env_id: env_destroy(fleet, env_id),
        ssh=lambda host, cmd: run_ssh(host.ssh_alias, cmd, timeout=60),
    )


def _sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _git_init_repo(path: str, marker: str) -> None:
    """Create a one-commit git repo holding marker.txt for the one-shot run check."""
    with open(os.path.join(path, "marker.txt"), "w", encoding="utf-8") as f:
        f.write(marker + "\n")
    git = ["git", "-C", path, "-c", "user.name=selfcheck", "-c", "user.email=selfcheck@localhost"]
    for args in (["init", "-q"], ["add", "marker.txt"], ["commit", "-q", "-m", "selfcheck"]):
        proc = subprocess.run(git + args, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise SelfcheckFailure(f"local git {args[0]} failed: {proc.stderr.strip()}")


def _leftover_script(host: HostConfig, env_id: str) -> str:
    return f"""
set -e
c=$({host.engine} ps -a -q --filter name=scavenger-env-{env_id})
v=$({host.engine} volume ls -q --filter name=scavenger-vol-{env_id})
w=""
if [ -e ~/workspaces/{env_id} ]; then w=present; fi
echo "container=$c"
echo "volume=$v"
echo "workspace=$w"
"""


def run_selfcheck(
    host: HostConfig,
    ops: SelfcheckOps,
    image: str = "alpine:latest",
    emit: Callable[[str], None] = print,
    env_id: Optional[str] = None,
) -> bool:
    """Run the end-to-end steps against one host. Prints one PASS/FAIL line per step.

    Stops at the first failing step. Returns True only if every step passed.
    """
    env_id = env_id or f"selfcheck-{uuid.uuid4().hex[:8]}"
    token = uuid.uuid4().hex
    state: Dict[str, Any] = {"res": None, "destroyed": False}

    with tempfile.TemporaryDirectory(prefix="scavenger-selfcheck-") as tmp:
        payload_dir = os.path.join(tmp, "payload")
        down_dir = os.path.join(tmp, "down")
        repo_dir = os.path.join(tmp, "repo")
        for d in (payload_dir, repo_dir):
            os.makedirs(d)
        payload = os.path.join(payload_dir, "payload.bin")
        with open(payload, "wb") as f:
            f.write(os.urandom(8192))
        payload_sha = _sha256_file(payload)

        def step_probe() -> str:
            st = ops.probe(host)
            if not st.online:
                raise SelfcheckFailure(st.reason or "host unreachable")
            if not st.eligible:
                raise SelfcheckFailure(f"online but not eligible: {st.reason}")
            return f"eligible ({st.engine}, {st.arch})"

        def step_run() -> str:
            _git_init_repo(repo_dir, f"marker-{token}")
            with tempfile.TemporaryFile("w+", encoding="utf-8") as out:
                rc = ops.run_job(
                    host=host,
                    command="cat marker.txt",
                    repo_path=repo_dir,
                    image=image,
                    mem_limit=host.default_memory,
                    cpus=host.default_cpus,
                    timeout_seconds=300,
                    stdout=out,
                )
                out.seek(0)
                output = out.read()
            if rc != 0:
                raise SelfcheckFailure(f"run exited {rc}")
            if f"marker-{token}" not in output:
                raise SelfcheckFailure(f"repo marker missing from output: {output.strip()[:200]!r}")
            return "repo bundle reached the container"

        def step_env_create() -> str:
            state["res"] = ops.env_create(
                env_id=env_id,
                host_name=host.name,
                kind="container",
                image=image,
                mem_limit=host.default_memory,
                cpus=host.default_cpus,
                ttl="15m",
            )
            return f"created {env_id}"

        def step_env_exec() -> str:
            proc = ops.env_exec(state["res"], f"echo exec-{token}")
            if proc.returncode != 0:
                raise SelfcheckFailure(f"exec exited {proc.returncode}: {proc.stderr.strip()[:200]}")
            if f"exec-{token}" not in proc.stdout:
                raise SelfcheckFailure(f"unexpected exec output: {proc.stdout.strip()[:200]!r}")
            return "command output matched"

        def step_sync_up() -> str:
            ops.env_sync(state["res"], "up", payload_dir)
            proc = ops.env_exec(state["res"], "sha256sum payload.bin")
            remote_sha = proc.stdout.split()[0] if proc.returncode == 0 and proc.stdout.split() else ""
            if remote_sha != payload_sha:
                raise SelfcheckFailure(
                    f"checksum mismatch after sync up (local {payload_sha[:12]}, remote {remote_sha[:12] or 'none'})"
                )
            return f"sha256 {payload_sha[:12]} matches on host"

        def step_sync_down() -> str:
            proc = ops.env_exec(state["res"], f"echo remote-{token} > remote.txt")
            if proc.returncode != 0:
                raise SelfcheckFailure(f"could not write remote file: {proc.stderr.strip()[:200]}")
            ops.env_sync(state["res"], "down", down_dir)
            down_payload = os.path.join(down_dir, "payload.bin")
            if not os.path.isfile(down_payload):
                raise SelfcheckFailure("payload.bin missing after sync down")
            if _sha256_file(down_payload) != payload_sha:
                raise SelfcheckFailure("payload.bin checksum changed on the round trip")
            remote_txt = os.path.join(down_dir, "remote.txt")
            if not os.path.isfile(remote_txt):
                raise SelfcheckFailure("remote.txt missing after sync down")
            with open(remote_txt, "r", encoding="utf-8") as f:
                if f.read().strip() != f"remote-{token}":
                    raise SelfcheckFailure("remote.txt content differs after sync down")
            return "round trip checksum matches, host-made file came back"

        def step_env_destroy() -> str:
            ok = ops.env_destroy(env_id)
            state["destroyed"] = True
            if not ok:
                raise SelfcheckFailure("env-destroy reported no such environment")
            return f"destroyed {env_id}"

        def step_leftovers() -> str:
            proc = ops.ssh(host, _leftover_script(host, env_id))
            if proc.returncode != 0:
                raise SelfcheckFailure(f"leftover query failed: {proc.stderr.strip()[:200]}")
            found = []
            for line in proc.stdout.splitlines():
                key, _, value = line.partition("=")
                if key in ("container", "volume", "workspace") and value.strip():
                    found.append(f"{key}={value.strip()}")
            if found:
                raise SelfcheckFailure("left on host: " + ", ".join(found))
            return "no container, volume, or workspace left"

        steps: List[Tuple[str, Callable[[], str]]] = [
            ("probe", step_probe),
            ("run", step_run),
            ("env-create", step_env_create),
            ("env-exec", step_env_exec),
            ("env-sync-up", step_sync_up),
            ("env-sync-down", step_sync_down),
            ("env-destroy", step_env_destroy),
            ("leftovers", step_leftovers),
        ]

        passed = True
        try:
            for name, fn in steps:
                try:
                    detail = fn()
                except SelfcheckFailure as e:
                    emit(f"FAIL {name}: {e}")
                    passed = False
                    break
                except Exception as e:  # noqa: BLE001 - any host or tooling error is a step failure
                    emit(f"FAIL {name}: {type(e).__name__}: {e}")
                    passed = False
                    break
                emit(f"PASS {name}: {detail}")
        finally:
            if state["res"] is not None and not state["destroyed"]:
                try:
                    ops.env_destroy(env_id)
                    emit(f"NOTE cleaned up {env_id} after failure")
                except Exception as e:  # noqa: BLE001
                    emit(f"NOTE cleanup of {env_id} failed ({type(e).__name__}: {e}); remove it by hand")

    return passed


# =============================================================================
# CLI Subcommands
# =============================================================================


def cmd_probe(args: argparse.Namespace) -> None:
    fleet = load_fleet_config(args.config)
    if not fleet:
        sys.exit("Error: No fleet configuration found. Create ~/.config/scavenger/fleet.json or specify --config.")

    targets = [args.host] if args.host else list(fleet.keys())
    results = []
    for name in targets:
        cfg = fleet.get(name)
        if not cfg:
            print(f"Unknown host: {name}", file=sys.stderr)
            continue
        status = probe_host(cfg, try_wake=args.wake)
        results.append(dataclasses.asdict(status))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"{'HOST':<14} {'ONLINE':<8} {'ARCH':<8} {'ENGINE':<8} {'AC':<6} {'STATUS':<10} {'NOTES'}")
        print("-" * 75)
        for r in results:
            ac_str = "Yes" if r["on_ac_power"] is True else ("No" if r["on_ac_power"] is False else "N/A")
            status_str = "ELIGIBLE" if r["eligible"] else "BUSY/OFF"
            print(
                f"{r['name']:<14} {str(r['online']):<8} {r['arch']:<8} {r['engine']:<8} {ac_str:<6} {status_str:<10} {r['reason']}"
            )


def cmd_run(args: argparse.Namespace) -> None:
    fleet = load_fleet_config(args.config)
    if not fleet:
        sys.exit("Error: No fleet configuration found. Create ~/.config/scavenger/fleet.json or specify --config.")

    if args.host:
        host = fleet.get(args.host)
        if not host:
            sys.exit(f"Unknown host: {args.host}")
        status = probe_host(host, try_wake=args.wake)
        if not status.eligible and not args.force:
            sys.exit(f"Host {host.name} not eligible: {status.reason} (use --force to bypass)")
    else:
        host, status = select_best_host(fleet, preferred_arch=args.arch, allow_wake=args.wake)

    ret = execute_job(
        host=host,
        command=args.command,
        repo_path=args.repo,
        image=args.image,
        mem_limit=args.mem,
        cpus=args.cpus,
        timeout_seconds=args.timeout,
    )
    sys.exit(ret)


def cmd_env_create(args: argparse.Namespace) -> None:
    fleet = load_fleet_config(args.config)
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
    )
    print(json.dumps(dataclasses.asdict(res), indent=2))


def cmd_env_exec(args: argparse.Namespace) -> None:
    fleet = load_fleet_config(args.config)
    res = find_lease(fleet, args.env_id)
    if not res:
        sys.exit(f"Error: No active lease for environment '{args.env_id}'")

    proc = env_exec(fleet, res, args.command, timeout_seconds=args.timeout)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    sys.exit(proc.returncode)


def cmd_env_sync(args: argparse.Namespace) -> None:
    fleet = load_fleet_config(args.config)
    res = find_lease(fleet, args.env_id)
    if not res:
        sys.exit(f"Error: No active lease for environment '{args.env_id}'")

    env_sync(fleet, res, direction=args.direction, local_path=args.local_path)
    print(f"Sync {args.direction} complete for '{args.env_id}' with '{args.local_path}'.")


def cmd_env_destroy(args: argparse.Namespace) -> None:
    fleet = load_fleet_config(args.config)
    ok = env_destroy(fleet, args.env_id)
    if ok:
        print(f"Environment '{args.env_id}' destroyed.")
    else:
        print(f"Environment '{args.env_id}' not found or already destroyed.")


def cmd_env_list(args: argparse.Namespace) -> None:
    fleet = load_fleet_config(args.config)
    leases, unreachable = reconcile_leases(fleet)
    for name in unreachable:
        print(f"Warning: host {name} unreachable; showing cached records for it", file=sys.stderr)
    now = time.time()
    if args.json:
        print(json.dumps({k: dataclasses.asdict(v) for k, v in leases.items()}, indent=2))
    else:
        print(f"{'ENV_ID':<24} {'HOST':<12} {'KIND':<10} {'EXPIRES_IN':<12} {'TARGET'}")
        print("-" * 75)
        for k, v in leases.items():
            rem = int(v.expires_at - now)
            rem_str = f"{rem}s" if rem > 0 else "EXPIRED"
            target = v.container_name if v.kind == "container" else (v.workspace_path or "")
            print(f"{k:<24} {v.host:<12} {v.kind:<10} {rem_str:<12} {target}")


def cmd_reap(args: argparse.Namespace) -> None:
    fleet = load_fleet_config(args.config)
    reaped = env_reap(fleet)
    if reaped:
        print(f"Reaped {len(reaped)} expired environment(s): {', '.join(reaped)}")
    else:
        print("No expired environments to reap.")


def cmd_selfcheck(args: argparse.Namespace) -> None:
    fleet = load_fleet_config(args.config)
    if not fleet:
        sys.exit("Error: No fleet configuration found. Create ~/.config/scavenger/fleet.json or specify --config.")
    host = fleet.get(args.host)
    if not host:
        sys.exit(f"Unknown host: {args.host}")

    ops = build_selfcheck_ops(fleet, wake=args.wake)
    ok = run_selfcheck(host, ops, image=args.image)
    if ok:
        print(f"selfcheck passed for {host.name}")
    else:
        print(f"selfcheck FAILED for {host.name}", file=sys.stderr)
    sys.exit(0 if ok else 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="LAN Scavenger Compute Fleet Controller")
    parser.add_argument("--config", help="Path to custom fleet JSON config (default: ~/.config/scavenger/fleet.json)")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    # Probe
    p_probe = subparsers.add_parser("probe", help="Probe fleet reachability and eligibility")
    p_probe.add_argument("--host", help="Specific host to probe (default: all)")
    p_probe.add_argument("--no-wake", dest="wake", action="store_false", help="Do not fire Wake-on-LAN packets")
    p_probe.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_probe.set_defaults(cmd_func=cmd_probe, wake=True)

    # One-shot Run
    p_run = subparsers.add_parser("run", help="Run an ephemeral container job on the scavenger fleet")
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

    # Env Create
    p_ec = subparsers.add_parser("env-create", help="Reserve a stateful environment")
    p_ec.add_argument("env_id", help="Unique identifier for this environment")
    p_ec.add_argument("--host", help="Target specific host")
    p_ec.add_argument("--kind", choices=["container", "native"], default="container", help="Environment backend")
    p_ec.add_argument("--image", default="alpine:latest", help="Container image")
    p_ec.add_argument(
        "--recipe", help="Recipe name from dockerfiles/ or skills/scavenger/dockerfiles/ (e.g. 'android-builder')"
    )
    p_ec.add_argument("--setup-script", help="Path to idempotent setup script to run inside environment")
    p_ec.add_argument("--sync-dir", help="Local directory to initial-sync into /workspace")
    p_ec.add_argument("--mem", default="16g", help="Memory limit")
    p_ec.add_argument("--cpus", type=int, default=8, help="CPU limit")
    p_ec.add_argument("--ttl", default="2h", help="Time to live (e.g. '1h', '30m')")
    p_ec.add_argument("--arch", choices=["arm64", "x86_64"], help="Architecture preference")
    p_ec.set_defaults(cmd_func=cmd_env_create)

    # Env Exec
    p_ee = subparsers.add_parser("env-exec", help="Run command inside a reserved environment")
    p_ee.add_argument("env_id", help="Environment identifier")
    p_ee.add_argument("command", help="Command to execute")
    p_ee.add_argument("--timeout", type=int, default=1800, help="Timeout in seconds")
    p_ee.set_defaults(cmd_func=cmd_env_exec)

    # Env Sync
    p_es = subparsers.add_parser("env-sync", help="Sync files to/from a reserved environment")
    p_es.add_argument("env_id", help="Environment identifier")
    p_es.add_argument("direction", choices=["up", "down"], help="Sync direction")
    p_es.add_argument("local_path", help="Local directory path")
    p_es.set_defaults(cmd_func=cmd_env_sync)

    # Env Destroy
    p_ed = subparsers.add_parser("env-destroy", help="Destroy a reserved environment")
    p_ed.add_argument("env_id", help="Environment identifier")
    p_ed.set_defaults(cmd_func=cmd_env_destroy)

    # Env List
    p_el = subparsers.add_parser("env-list", help="List active environment reservations")
    p_el.add_argument("--json", action="store_true", help="JSON output")
    p_el.set_defaults(cmd_func=cmd_env_list)

    # Reap
    p_reap = subparsers.add_parser("reap", help="Reap all expired environment reservations")
    p_reap.set_defaults(cmd_func=cmd_reap)

    # Selfcheck
    p_sc = subparsers.add_parser("selfcheck", help="Run an end-to-end check against one real host")
    p_sc.add_argument("host", help="Host name from the fleet config")
    p_sc.add_argument("--image", default="alpine:latest", help="Container image (default: alpine:latest)")
    p_sc.add_argument("--no-wake", dest="wake", action="store_false", help="Do not fire Wake-on-LAN packets")
    p_sc.set_defaults(cmd_func=cmd_selfcheck, wake=True)

    args = parser.parse_args()
    args.cmd_func(args)


if __name__ == "__main__":
    main()
