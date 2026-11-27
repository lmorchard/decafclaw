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
import shlex
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
    pids_limit: int = 4096  # --pids-limit for every container; -1 disables the cap
    log_max_size: str = "10m"  # --log-opt max-size (docker/podman json-file driver)
    log_max_file: int = 3  # --log-opt max-file (docker/podman json-file driver)
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
            pids_limit=limits.get("pids_limit", 4096),
            log_max_size=limits.get("log_max_size", "10m"),
            log_max_file=limits.get("log_max_file", 3),
            tags=h.get("tags", []),
            gpu_type=gpu.get("type"),
            gpu_passthrough_mode=gpu.get("passthrough_mode"),
            notes=h.get("notes", ""),
        )
        fleet[cfg.name] = cfg
    return fleet


DEFAULT_RECIPES_DIR = "dockerfiles"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Environment variables for reserved environments live here rather than in the
# lease cache: reconcile_leases() rebuilds that cache from host labels, so
# anything stored there would be wiped on the next sweep. The file is mode 0600
# because it holds credentials.
ENV_VARS_FILE = os.path.expanduser("~/.config/scavenger/env-vars.json")

# Container labels. Readers accept the previous decafclaw.* names so existing
# environments remain visible after the engine is made standalone.
LABEL_ENV_ID = "scavenger.env_id"
LABEL_EXPIRES_AT = "scavenger.expires_at"
LABEL_CREATED_AT = "scavenger.created_at"
LABEL_TTL_SECONDS = "scavenger.ttl_seconds"
LEGACY_LABEL_ENV_ID = "decafclaw.env_id"
LEGACY_LABEL_EXPIRES_AT = "decafclaw.expires_at"
LEGACY_LABEL_CREATED_AT = "decafclaw.created_at"
LEGACY_LABEL_TTL_SECONDS = "decafclaw.ttl_seconds"

#: Environment ids become container names, volume names, and remote workspace paths.
ENV_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")


def validate_env_id(env_id: str) -> str:
    """Return env_id when it is a safe name component, else raise ValueError.

    The id is interpolated into container/volume names and remote paths, so it
    must not carry shell metacharacters, whitespace, or path separators.
    """
    if not isinstance(env_id, str) or not ENV_ID_PATTERN.fullmatch(env_id):
        raise ValueError(
            f"Invalid env_id {env_id!r}: use 1-63 letters, digits, '.', '_' or '-', starting with a letter or digit"
        )
    return env_id


def read_env_labels(labels: Dict[str, str]) -> Tuple[Optional[str], Optional[int]]:
    """Read (env_id, expires_at) from container labels, accepting scavenger.* and legacy decafclaw.* names."""
    env_id = labels.get(LABEL_ENV_ID) or labels.get(LEGACY_LABEL_ENV_ID) or None
    raw_expires = labels.get(LABEL_EXPIRES_AT) or labels.get(LEGACY_LABEL_EXPIRES_AT)
    try:
        expires_at = int(float(raw_expires)) if raw_expires else None
    except (TypeError, ValueError):
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


# -----------------------------------------------------------------------------
# Per-environment variables (~/.config/scavenger/env-vars.json, mode 0600)
# -----------------------------------------------------------------------------


def _load_env_vars_file() -> Dict[str, Dict[str, str]]:
    """Read the env-vars store, tolerating a missing or corrupt file."""
    try:
        with open(ENV_VARS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(env_id): {str(k): str(v) for k, v in vals.items()}
        for env_id, vals in data.items()
        if isinstance(vals, dict)
    }


def _write_env_vars_file(data: Dict[str, Dict[str, str]]) -> None:
    """Write the env-vars store atomically with mode 0600."""
    os.makedirs(os.path.dirname(ENV_VARS_FILE), exist_ok=True)
    tmp_path = ENV_VARS_FILE + ".tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, ENV_VARS_FILE)
    os.chmod(ENV_VARS_FILE, 0o600)


def save_env_vars(env_id: str, env_vars: Optional[Dict[str, str]]) -> None:
    """Replace the stored variables for one environment (an empty mapping clears them)."""
    data = _load_env_vars_file()
    if env_vars:
        data[env_id] = dict(env_vars)
    else:
        data.pop(env_id, None)
    _write_env_vars_file(data)


def load_env_vars(env_id: str) -> Dict[str, str]:
    """Return the stored variables for one environment, or an empty mapping."""
    return dict(_load_env_vars_file().get(env_id) or {})


def drop_env_vars(env_id: str) -> None:
    """Remove stored variables for one environment; never raise."""
    data = _load_env_vars_file()
    if data.pop(env_id, None) is not None:
        _write_env_vars_file(data)


def parse_env_assignments(values: Optional[List[str]]) -> Dict[str, str]:
    """Parse repeated KEY=VALUE assignments, rejecting malformed entries."""
    parsed: Dict[str, str] = {}
    for raw in values or []:
        if "=" not in raw:
            raise ValueError(f"--env expects KEY=VALUE, got {raw!r}")
        key, _, value = raw.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"--env expects a non-empty KEY, got {raw!r}")
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            raise ValueError(f"--env KEY must be a valid environment variable name, got {key!r}")
        parsed[key] = value
    return parsed


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
# scavenger.* labels, and native workspaces hold a small lease file. The local
# leases.json is only a cache, rebuilt from the hosts by reconcile_leases().
# -----------------------------------------------------------------------------

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
    env_id, expires_at = read_env_labels(labels)
    if not env_id or expires_at is None:
        return None
    try:
        created_at = float(labels.get(LABEL_CREATED_AT) or labels.get(LEGACY_LABEL_CREATED_AT, 0.0))
    except ValueError:
        created_at = 0.0
    raw_ttl = labels.get(LABEL_TTL_SECONDS) or labels.get(LEGACY_LABEL_TTL_SECONDS)
    try:
        ttl_seconds = int(raw_ttl) if raw_ttl else 0
    except (TypeError, ValueError):
        ttl_seconds = 0
    if not ttl_seconds and created_at:
        ttl_seconds = int(expires_at - created_at)
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


def list_host_script(host: HostConfig) -> str:
    """Remote script that lists labelled containers, labelled volumes, and native lease files."""
    containers = remote_argv(list_containers_args(host))
    volumes = remote_argv(list_volumes_args(host))
    return f"""
echo ---CONTAINERS---
{containers} || echo ---FAILED---
echo ---VOLUMES---
{volumes} || echo ---FAILED---
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
        res = run_ssh(host.ssh_alias, list_host_script(host), timeout=20)
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


#: Container engines whose json-file log driver accepts --log-opt size caps.
LOG_OPT_ENGINES = ("docker", "podman")


def container_limit_flags(host: HostConfig, network: str = "default") -> List[str]:
    """Build the container security and resource limit flags for a host.

    Pure function: reads no environment, runs nothing. Returns argv tokens
    for --network, --pids-limit, and (where the engine supports it) the
    json-file log size cap.
    """
    if network not in ("none", "default"):
        raise ValueError(f"Unsupported network mode: {network!r} (expected 'none' or 'default')")

    flags = [f"--network={network}"]
    if host.pids_limit is not None:
        flags.append(f"--pids-limit={host.pids_limit}")
    if host.engine in LOG_OPT_ENGINES and host.log_max_size:
        flags.extend(["--log-opt", f"max-size={host.log_max_size}"])
        if host.log_max_file:
            flags.extend(["--log-opt", f"max-file={host.log_max_file}"])
    return flags


def remote_argv(argv: List[str]) -> str:
    """Render an argument vector as one POSIX shell command word list for `ssh`."""
    return shlex.join(argv)


def home_shell_path(rel_path: str) -> str:
    """Shell word for a path under the remote user's home directory.

    ``$HOME`` stays outside the quoted segment so the remote shell expands it.
    """
    return '"$HOME"/' + shlex.quote(rel_path)


def new_job_dir() -> str:
    """A fresh literal remote scratch directory (no remote shell variable needed)."""
    return f"/tmp/scavenger-job-{uuid.uuid4().hex[:12]}"


def container_run_args(
    host: HostConfig,
    image: str,
    container_command: List[str],
    *,
    volume: Optional[str] = None,
    name: Optional[str] = None,
    detached: bool = False,
    remove: bool = True,
    workdir: Optional[str] = None,
    mem_limit: Optional[str] = None,
    cpus: Optional[int] = None,
    network: str = "default",
    labels: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Build container-engine argv for `run` / `run -d`.

    Pure function: reads no environment, runs nothing. Every value is a single
    argv token, so rendering with ``remote_argv`` quotes it exactly once.
    """
    argv = [host.engine, "run"]
    if remove:
        argv.append("--rm")
    if detached:
        argv.append("-d")
    if name:
        argv.extend(["--name", name])
    if mem_limit is not None:
        argv.append(f"--memory={mem_limit}")
    if cpus is not None:
        argv.append(f"--cpus={cpus}")
    argv.extend(container_limit_flags(host, network))
    argv.extend(host.device_flags)
    for key, value in (labels or {}).items():
        argv.extend(["--label", f"{key}={value}"])
    if volume:
        argv.extend(["-v", volume])
    if workdir:
        argv.extend(["-w", workdir])
    argv.append(image)
    argv.extend(container_command)
    return argv


def _container_exec_prefix(
    host: HostConfig,
    container: str,
    *,
    env_vars: Optional[Dict[str, str]] = None,
    interactive: bool = False,
    detach: bool = False,
) -> List[str]:
    argv = [host.engine, "exec"]
    if detach:
        argv.append("-d")
    if interactive:
        argv.append("-i")
    for key, value in sorted((env_vars or {}).items()):
        argv.extend(["-e", f"{key}={value}"])
    argv.append(container)
    return argv


def container_exec_args(
    host: HostConfig,
    container: str,
    command: str,
    *,
    env_vars: Optional[Dict[str, str]] = None,
    interactive: bool = False,
    detach: bool = False,
) -> List[str]:
    """Build argv for `exec ... sh -c COMMAND` inside a container."""
    return _container_exec_prefix(host, container, env_vars=env_vars, interactive=interactive, detach=detach) + [
        "sh",
        "-c",
        command,
    ]


def container_exec_raw_args(
    host: HostConfig,
    container: str,
    command_argv: List[str],
    *,
    env_vars: Optional[Dict[str, str]] = None,
    interactive: bool = False,
    detach: bool = False,
) -> List[str]:
    """Build argv for `exec` of a program without a shell wrapper (e.g. tar)."""
    return _container_exec_prefix(host, container, env_vars=env_vars, interactive=interactive, detach=detach) + list(
        command_argv
    )


def container_rm_args(host: HostConfig, container: str) -> List[str]:
    """Build argv for `rm -f CONTAINER`."""
    return [host.engine, "rm", "-f", container]


def volume_create_args(host: HostConfig, volume: str, labels: Optional[Dict[str, str]] = None) -> List[str]:
    """Build argv for `volume create` with labels."""
    argv = [host.engine, "volume", "create"]
    for key, value in (labels or {}).items():
        argv.extend(["--label", f"{key}={value}"])
    argv.append(volume)
    return argv


def volume_rm_args(host: HostConfig, volume: str) -> List[str]:
    """Build argv for `volume rm -f VOLUME`."""
    return [host.engine, "volume", "rm", "-f", volume]


def image_exists_args(host: HostConfig, tag: str) -> List[str]:
    """Build argv that exits 0 when the image is present on the host."""
    if host.engine == "podman":
        return [host.engine, "image", "exists", tag]
    return [host.engine, "image", "inspect", tag]


def image_build_args(host: HostConfig, tag: str) -> List[str]:
    """Build argv that builds an image from a Dockerfile on stdin."""
    return [host.engine, "build", "-t", tag, "-f", "-", "/tmp"]


def list_containers_args(host: HostConfig) -> List[str]:
    """Build argv that lists every container, including stopped ones, as JSON."""
    return [host.engine, "ps", "--all", "--format", "json"]


def list_volumes_args(host: HostConfig) -> List[str]:
    """Build argv that lists volumes as JSON."""
    return [host.engine, "volume", "ls", "--format", "json"]


def job_remote_script(
    host: HostConfig,
    command: str,
    job_dir: str,
    *,
    image: str = "alpine:latest",
    mem_limit: str = "16g",
    cpus: int = 8,
    network: str = "default",
    repo_bundle: bool = False,
) -> str:
    """Build the remote shell script for a one-shot container job.

    Pure function: reads no environment, runs nothing. ``job_dir`` is a literal
    path (see ``new_job_dir``) so no remote shell variable reaches an argv token.
    """
    quoted_dir = shlex.quote(job_dir)
    workspace = f"{job_dir}/workspace"
    mount = f"{workspace}:/workspace" + (":Z" if host.needs_selinux_z else "")
    run_argv = container_run_args(
        host,
        image,
        ["sh", "-c", command],
        volume=mount,
        workdir="/workspace",
        mem_limit=mem_limit,
        cpus=cpus,
        network=network,
    )

    lines = [
        "set -e",
        f"mkdir -p {quoted_dir}",
        f"trap {shlex.quote(f'rm -rf {job_dir}')} EXIT",
    ]
    if repo_bundle:
        bundle = shlex.quote(f"{job_dir}/repo.bundle")
        lines.append(f"cat > {bundle}")
        lines.append(f"git clone -q {bundle} {shlex.quote(workspace)}")
    else:
        lines.append(f"mkdir -p {shlex.quote(workspace)}")
    lines.append(remote_argv(run_argv))
    return "\n".join(lines) + "\n"


def execute_job(
    host: HostConfig,
    command: str,
    repo_path: Optional[str] = None,
    image: str = "alpine:latest",
    mem_limit: str = "16g",
    cpus: int = 8,
    network: str = "default",
    timeout_seconds: int = 1800,
    stdout: Optional[IO[str]] = None,
    stderr: Optional[IO[str]] = None,
) -> int:
    """One-shot job: stage repo bundle, run ephemeral container, stream output, clean up.

    ``stdout``/``stderr`` default to this process's streams; pass real files to capture output.
    """
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr

    job_dir = new_job_dir()
    remote_script = job_remote_script(
        host,
        command,
        job_dir,
        image=image,
        mem_limit=mem_limit,
        cpus=cpus,
        network=network,
        repo_bundle=False,
    )

    if repo_path and os.path.isdir(os.path.join(repo_path, ".git")):
        print(f"📦 Bundling repository from {repo_path} (HEAD)...", file=sys.stderr)
        bundle_proc = subprocess.Popen(
            ["git", "-C", repo_path, "bundle", "create", "-", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        ssh_wrapper = job_remote_script(
            host,
            command,
            job_dir,
            image=image,
            mem_limit=mem_limit,
            cpus=cpus,
            network=network,
            repo_bundle=True,
        )
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


def recipe_image_tag(recipe_path: str) -> str:
    """Return a content-addressed image tag for a recipe Dockerfile.

    The tag used to be ``scavenger-recipe-<name>:latest`` and the build was
    skipped whenever that image already existed on the host. Editing a recipe
    therefore never reached a container: a stale image could keep serving
    months-old tool versions while the source said otherwise. Including the
    Dockerfile digest in the tag makes an edit produce a new tag, which misses
    the image check and builds.
    """
    with open(recipe_path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()[:12]
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "-", os.path.basename(recipe_path).replace(".Dockerfile", ""))
    return f"scavenger-recipe-{clean_name}:{digest}"


def ensure_remote_image(host: HostConfig, tag: str, dockerfile_path: str) -> None:
    """Ensure a container image exists on the remote host; builds it from Dockerfile if missing."""
    res = run_ssh(host.ssh_alias, remote_argv(image_exists_args(host, tag)))
    if res.returncode == 0:
        return

    print(f"🔨 Building remote recipe image '{tag}' on {host.name}...", file=sys.stderr)
    with open(dockerfile_path, "rb") as f:
        dockerfile_bytes = f.read()

    build_cmd = remote_argv(image_build_args(host, tag))
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
    network: str = "default",
    ttl: str = "2h",
    arch: Optional[str] = None,
    recipes_dir: Optional[str] = None,
    env_vars: Optional[Dict[str, str]] = None,
) -> EnvReservation:
    """
    Reserve a stateful environment (container or native workspace) on the fleet.
    Preserves state across turns until env_destroy or TTL expiry.

    ``env_vars`` are stored outside the lease cache (see ENV_VARS_FILE) and are
    passed to every later env_exec / env_exec_bg in this environment. They never
    reach container labels, which any local user on the target host can inspect.

    Reaps expired environments first so their containers, volumes, and native
    slots are freed before host selection. A reap failure on one host is
    logged and does not block creation.
    """
    validate_env_id(env_id)
    env_reap(fleet)

    now = time.time()
    ttl_sec = parse_ttl(ttl)
    expires_at = now + ttl_sec

    # Check for existing active lease with same env_id (cache first, then the hosts)
    existing = find_lease(fleet, env_id)
    if existing and existing.expires_at > now:
        if env_vars is not None:
            save_env_vars(env_id, env_vars)
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
            tag = recipe_image_tag(rec_path)
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
        lease_path = home_shell_path(f"{ws_rel}/{NATIVE_LEASE_FILE}")
        mkdir_res = run_ssh(
            host.ssh_alias,
            f"mkdir -p {home_shell_path(ws_rel)} && echo {shlex.quote(b64_lease)} | base64 -d > {lease_path}",
        )
        if mkdir_res.returncode != 0:
            raise RuntimeError(f"Failed to create workspace on {host.name}: {mkdir_res.stderr}")

        if sync_dir and os.path.isdir(sync_dir):
            env_sync(fleet, res, direction="up", local_path=sync_dir)

    else:
        c_name = f"scavenger-env-{env_id}"
        v_name = f"scavenger-vol-{env_id}"
        selinux_flag = ":Z" if host.needs_selinux_z else ""
        labels = {
            LABEL_ENV_ID: env_id,
            LABEL_EXPIRES_AT: str(int(expires_at)),
            LABEL_CREATED_AT: str(int(now)),
            LABEL_TTL_SECONDS: str(ttl_sec),
        }

        create_script = "\n".join(
            [
                "set -e",
                f"{remote_argv(container_rm_args(host, c_name))} 2>/dev/null || true",
                f"{remote_argv(volume_create_args(host, v_name, labels))} >/dev/null 2>&1 || true",
                remote_argv(
                    container_run_args(
                        host,
                        image,
                        ["sleep", "infinity"],
                        name=c_name,
                        detached=True,
                        remove=False,
                        volume=f"{v_name}:/workspace{selinux_flag}",
                        workdir="/workspace",
                        mem_limit=mem_limit,
                        cpus=cpus,
                        network=network,
                        labels=labels,
                    )
                ),
            ]
        )
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
        setup_inner = f"echo {shlex.quote(b64_setup)} | base64 -d | sh"
        if res.kind == "native":
            ws = res.workspace_path or f"workspaces/{res.env_id}"
            run_cmd = f"cd {home_shell_path(ws)} && {setup_inner}"
        else:
            c_name = res.container_name or f"scavenger-env-{res.env_id}"
            run_cmd = remote_argv(container_exec_args(host, c_name, setup_inner, interactive=True))
        setup_proc = run_ssh(host.ssh_alias, run_cmd, timeout=300)
        if setup_proc.returncode != 0:
            raise RuntimeError(f"Setup script failed on {host.name}:\n{setup_proc.stderr}")

    def record(leases: Dict[str, EnvReservation]) -> None:
        leases[env_id] = res

    update_leases(record)
    if env_vars is not None:
        save_env_vars(env_id, env_vars)
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

    env_vars = load_env_vars(res.env_id)
    if res.kind == "native":
        exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(env_vars.items()))
        prefix = f"export {exports} && " if exports else ""
        ws = res.workspace_path or f"workspaces/{res.env_id}"
        remote_cmd = f"cd {home_shell_path(ws)} && {prefix}{command}"
    else:
        c_name = res.container_name or f"scavenger-env-{res.env_id}"
        remote_cmd = remote_argv(container_exec_args(host, c_name, command, env_vars=env_vars, interactive=True))

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

    env_vars = load_env_vars(res.env_id)
    b64_cmd = base64.b64encode(command.encode("utf-8")).decode("ascii")
    # Native environments have no engine to pass -e through, so the exports ride
    # inside the wrapped script instead.
    exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(env_vars.items()))
    prefix = f"export {exports}; " if exports else ""
    inner_script = (
        f"rm -f {shlex.quote(exit_file)}; "
        f"({prefix}echo {shlex.quote(b64_cmd)} | base64 -d | sh) > {shlex.quote(log_file)} 2>&1; "
        f"echo $? > {shlex.quote(exit_file)}"
    )
    b64_full = base64.b64encode(inner_script.encode("utf-8")).decode("ascii")
    wrapped = f"echo {shlex.quote(b64_full)} | base64 -d | sh"

    if res.kind == "native":
        ws = res.workspace_path or f"workspaces/{res.env_id}"
        remote_cmd = f"cd {home_shell_path(ws)} && nohup sh -c {shlex.quote(wrapped)} >/dev/null 2>&1 & echo $!"
    else:
        c_name = res.container_name or f"scavenger-env-{res.env_id}"
        remote_cmd = remote_argv(container_exec_args(host, c_name, wrapped, detach=True))

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
        quoted = shlex.quote(target_file)
        inner = f"while [ ! -f {quoted} ]; do sleep 1; done; cat {quoted}"
    elif watch_type == "file":
        quoted = shlex.quote(target)
        inner = f"while [ ! -f {quoted} ]; do sleep 1; done; ls -ld {quoted}"
    elif watch_type == "pattern":
        quoted = shlex.quote(target)
        inner = f"touch {quoted} && tail -n +1 -f {quoted} | grep -m 1 -E {shlex.quote(pattern)}"
    else:
        raise ValueError(f"Unknown watch_type: {watch_type}")

    if res.kind == "native":
        full_script = f"cd {home_shell_path(ws)} && {inner}"
    else:
        full_script = remote_argv(container_exec_args(host, c_name, inner, interactive=True))

    b64_cmd = base64.b64encode(full_script.encode("utf-8")).decode("ascii")
    remote_cmd = f"echo {shlex.quote(b64_cmd)} | base64 -d | sh"
    meta = json.dumps(
        {
            "env_id": res.env_id,
            "host": res.host,
            "watch_type": watch_type,
            "target": target,
            "pattern": pattern,
        }
    )
    sentinel_json = meta[:-1]  # opening object with the static fields; result/timestamp appended below
    runner_script = f"""
RESULT=$({remote_argv(["ssh", "-o", "BatchMode=yes", host.ssh_alias, remote_cmd])} 2>/dev/null || echo 'WATCH_SSH_ERROR')
cat > {shlex.quote(sentinel_path)} <<'SCAVENGER_SENTINEL'
{sentinel_json}
SCAVENGER_SENTINEL
printf ', "result": "%s", "timestamp": %s}}\\n' "$RESULT" "$(date +%s)" >> {shlex.quote(sentinel_path)}
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
            remote_cmd = f"mkdir -p {home_shell_path(ws)} && tar -xzf - -C {home_shell_path(ws)}"
        else:
            c_name = res.container_name or f"scavenger-env-{res.env_id}"
            remote_cmd = remote_argv(
                container_exec_raw_args(host, c_name, ["tar", "-xzf", "-", "-C", "/workspace"], interactive=True)
            )
        ssh_cmd = ["ssh", "-o", "BatchMode=yes", host.ssh_alias, remote_cmd]

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
            remote_cmd = f"tar -czf - -C {home_shell_path(ws)} ."
        else:
            c_name = res.container_name or f"scavenger-env-{res.env_id}"
            remote_cmd = remote_argv(container_exec_raw_args(host, c_name, ["tar", "-czf", "-", "-C", "/workspace"]))
        ssh_cmd = ["ssh", "-o", "BatchMode=yes", host.ssh_alias, remote_cmd]

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

    try:
        if res.kind == "native":
            ws = res.workspace_path or f"workspaces/{env_id}"
            destroy_res = run_ssh(host.ssh_alias, f"rm -rf {home_shell_path(ws)}")
        else:
            c_name = res.container_name or f"scavenger-env-{env_id}"
            v_name = res.volume_name or f"scavenger-vol-{env_id}"
            destroy_res = run_ssh(
                host.ssh_alias,
                f"{remote_argv(container_rm_args(host, c_name))} 2>/dev/null; "
                f"{remote_argv(volume_rm_args(host, v_name))} 2>/dev/null || true",
            )
    except Exception as e:
        print(f"⚠️ Failed to destroy {env_id} on {host.name}: {e}", file=sys.stderr)
        return False

    if destroy_res.returncode != 0:
        print(
            f"⚠️ Destroy of {env_id} on {host.name} failed (rc={destroy_res.returncode}): "
            f"{(destroy_res.stderr or '').strip()}",
            file=sys.stderr,
        )
        return False

    def drop(leases: Dict[str, EnvReservation]) -> None:
        leases.pop(env_id, None)

    update_leases(drop)
    drop_env_vars(env_id)
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
        if res.expires_at < now:
            try:
                ok = env_destroy(fleet, env_id)
            except Exception as e:
                print(f"⚠️ Reap of {env_id} on {res.host} failed: {e}", file=sys.stderr)
                continue
            if ok:
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
c=$({remote_argv([host.engine, "ps", "-a", "-q", "--filter", f"name=scavenger-env-{env_id}"])})
v=$({remote_argv([host.engine, "volume", "ls", "-q", "--filter", f"name=scavenger-vol-{env_id}"])})
w=""
if [ -e {home_shell_path(f"workspaces/{env_id}")} ]; then w=present; fi
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

    reaped = env_reap(fleet)
    if reaped:
        print(f"Reaped {len(reaped)} expired environment(s): {', '.join(reaped)}", file=sys.stderr)

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
        network=args.network,
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
        network=args.network,
        ttl=args.ttl,
        arch=args.arch,
        recipes_dir=resolve_recipes_dir(args.recipes_dir, args.config),
        env_vars=parse_env_assignments(getattr(args, "env", None)),
    )
    # env-create has always printed JSON; keep that regardless of --json.
    print(json.dumps(dataclasses.asdict(res), indent=2))
    return 0


def _get_lease(args: argparse.Namespace, fleet: Optional[Dict[str, HostConfig]] = None) -> Optional[EnvReservation]:
    if fleet is not None:
        return find_lease(fleet, args.env_id)
    cached = load_leases().get(args.env_id)
    if cached:
        return cached
    configured = load_fleet_config(args.config)
    return find_lease(configured, args.env_id) if configured else None


def cmd_env_exec(args: argparse.Namespace) -> int:
    fleet = _load_fleet(args)
    res = find_lease(fleet, args.env_id)
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
    res = find_lease(fleet, args.env_id)
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
    if env_destroy(fleet, args.env_id):
        _emit(args, {"env_id": args.env_id, "destroyed": True}, f"Environment '{args.env_id}' destroyed.")
        return 0
    if load_leases().get(args.env_id):
        return _fail(args, f"Failed to destroy environment '{args.env_id}'; lease retained for retry.")
    return _fail(args, f"Environment '{args.env_id}' not found or already destroyed.")


def cmd_env_list(args: argparse.Namespace) -> int:
    fleet = load_fleet_config(args.config)
    if fleet:
        leases, unreachable = reconcile_leases(fleet)
        for name in unreachable:
            print(f"Warning: host {name} unreachable; showing cached records for it", file=sys.stderr)
    else:
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


def cmd_selfcheck(args: argparse.Namespace) -> None:
    fleet = _load_fleet(args)
    host = fleet.get(args.host)
    if not host:
        sys.exit(_fail(args, f"Unknown host: {args.host}"))

    ops = build_selfcheck_ops(fleet, wake=args.wake)
    ok = run_selfcheck(host, ops, image=args.image)
    text = f"selfcheck {'passed' if ok else 'FAILED'} for {host.name}"
    if args.json:
        _emit(args, {"host": host.name, "passed": ok})
    elif ok:
        print(text)
    else:
        print(text, file=sys.stderr)
    sys.exit(0 if ok else 1)


def cmd_runner(args: argparse.Namespace) -> int:
    try:
        from .runners.opencode_scavenger_runner import run_remote_opencode
    except ImportError:
        try:
            from contrib.skills.scavenger.runners.opencode_scavenger_runner import run_remote_opencode
        except ImportError:
            runners_dir = os.path.join(os.path.dirname(__file__), "runners")
            sys.path.insert(0, os.path.dirname(runners_dir))
            from runners.opencode_scavenger_runner import run_remote_opencode

    runner_args = list(args.runner_args or [])
    if runner_args and runner_args[0] == "--":
        runner_args = runner_args[1:]
    return run_remote_opencode(runner_args, env_id=args.env_id)


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
    p_run.add_argument(
        "--network",
        choices=["none", "default"],
        default="default",
        help="Container network: 'none' blocks all network access (use for test runs that need no downloads)",
    )
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
    p_ec.add_argument(
        "--network",
        choices=["none", "default"],
        default="default",
        help="Container network: 'none' blocks all network access (use for environments that need no downloads)",
    )
    p_ec.add_argument("--ttl", default="2h", help="Time to live (e.g. '1h', '30m')")
    p_ec.add_argument("--arch", choices=["arm64", "x86_64"], help="Architecture preference")
    p_ec.add_argument(
        "--env",
        action="append",
        metavar="KEY=VALUE",
        help="Environment variable passed to every env-exec in this environment; repeatable",
    )

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

    p_sc = add("selfcheck", "Run an end-to-end check against one real host", cmd_selfcheck, wake=True)
    p_sc.add_argument("host", help="Host name from the fleet config")
    p_sc.add_argument("--image", default="alpine:latest", help="Container image (default: alpine:latest)")
    p_sc.add_argument("--no-wake", dest="wake", action="store_false", help="Do not fire Wake-on-LAN packets")

    # Runner (OpenCode proxy adapter)
    p_rn = add("runner", "Run opencode transparently inside a reserved scavenger container", cmd_runner)
    p_rn.add_argument("--env-id", help="Target scavenger environment ID (default: SCAVENGER_ENV_ID or ghflow-worker)")
    p_rn.add_argument("runner_args", nargs=argparse.REMAINDER, help="Arguments passed through to opencode")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.cmd_func(args)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as e:
        return _fail(args, str(e))


if __name__ == "__main__":
    sys.exit(main())
