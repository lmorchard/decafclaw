# Scavenger Compute Fleet Architecture

> **Design:** Agentless, deterministic execution over SSH  
> **Key identity:** `~/.ssh/id_ed25519_scavenger`  
> **Config location:** `~/.config/scavenger/fleet.json` (outside the repository)  
> **Controller:** `scavenger.py` / `tools.py`

The Scavenger Compute Fleet provides on-demand, agentless ephemeral container execution across idle workstations and laptops on your local network or Tailnet. It replaces expensive cloud VMs with zero-cost, high-memory local compute without requiring cluster daemons (such as Kubernetes or Nomad) on client hardware.

---

## 1. Core Principles & Scavenger Ethos

1. **Zero Target Daemons:** No Kubernetes, Nomad, or HTCondor agents installed on target boxen. Targets are controlled purely over standard SSH.
2. **Blast Radius Isolation:** All remote execution runs as a dedicated, low-privilege `scavenger` user account. The `scavenger` user has **no sudo rights**, and operator home directories are locked down with `chmod 700`. This guards against accidents, not against a job set on reading the host. See [Isolation Limits](#6-isolation-limits).
3. **Containerized Containment:** Container workloads run inside rootless Podman or Docker containers with explicit `--memory`, `--cpus`, and `--pids-limit` limits, bounded json-file logs, and an explicit network mode (`--network=none` or `--network=default`). Native Darwin jobs (Track B) are not containerized.
4. **Disposable Ephemeral Workspaces:** Source code is bundled as an in-memory `git bundle` over SSH into a throwaway `/tmp` directory, cloned, executed, and reaped immediately in a `trap ... EXIT` block.
5. **Stateful Reservations on Demand:** Multi-step pipelines (such as Android builds, Node modules, or compiler caches) can reserve warm environments with named persistent volumes or scoped workspaces that expire automatically via hard TTLs. The controller reaps expired reservations on `probe` and `env-create`; a host that is unreachable keeps its lease for the next sweep rather than orphaning the container.
6. **Courtesy Guards:**
   - **Laptops:** Probes check `pmset -g batt` (macOS) or sysfs battery status (Linux). If running on battery power, the laptop is disqualified so the agent never drains battery.
   - **Workstations:** Probes can check game streaming or active foreground sessions before scheduling heavy jobs.

---

## 2. Fleet Configuration Schema (`~/.config/scavenger/fleet.json`)

Operator host inventory is kept out of version control and loaded from `~/.config/scavenger/fleet.json`. An example configuration is provided in `fleet.example.json`:

```json
{
  "default_broadcast": "192.168.1.255",
  "default_wol_port": 9,
  "hosts": [
    {
      "name": "desktop-linux",
      "ssh_alias": "scavenger-desktop",
      "os": "linux",
      "arch": "x86_64",
      "priority": 10,
      "container": {
        "engine": "podman",
        "needs_selinux_z": true,
        "device_flags": ["--device", "/dev/dri"]
      },
      "power": {
        "wol_mac": "AA:BB:CC:DD:EE:01",
        "wol_wait_seconds": 10
      },
      "limits": {
        "default_memory": "16g",
        "default_cpus": 8,
        "pids_limit": 4096,
        "log_max_size": "10m",
        "log_max_file": 3
      }
    },
    {
      "name": "laptop-mac",
      "ssh_alias": "scavenger-laptop",
      "os": "darwin",
      "arch": "arm64",
      "priority": 20,
      "container": {
        "engine": "docker"
      },
      "power": {
        "battery_guard": true,
        "wol_mac": "AA:BB:CC:DD:EE:02"
      }
    }
  ]
}
```

Optional top-level key `recipes_dir` points at a directory of recipe Dockerfiles (relative paths are relative to `fleet.json`). The `--recipes-dir` flag overrides it; with neither, `./dockerfiles` is used. Recipes bundled with the skill are always searched last.

Containers are labelled `scavenger.env_id` and `scavenger.expires_at`. The older `decafclaw.*` label names are still read. For shell-only use, see `SKILL-cli.md`.

---

## 3. Wake-on-LAN & Apple Silicon DarkWake

Wake-on-LAN is data-driven and handled natively via pure Python standard library (`socket` UDP broadcast):

- **Linux Desktops:** Wakes from S3 sleep in ~5–8 seconds upon receiving the magic packet.
- **Apple Silicon Laptops & Desktops:**
  - **On AC Power:** Supported via macOS **DarkWake** (`womp 1` / Wake on Magic Packet). When plugged into AC power with the lid closed, receiving a magic packet wakes the CPU and networking headlessly (the screen remains off), brings Docker online, runs the job, and lets the machine sleep when finished.
  - **On Battery:** When closed on battery power, macOS shuts down Wi-Fi into deep sleep; magic packets are ignored and the battery guard skips the machine.

---

## 4. Two-Track GPU Acceleration

Because macOS container runtimes do not support paravirtualized Metal drivers to Linux container guests, the scavenger architecture employs a clean two-track acceleration model:

### Track A: Linux GPU Container Passthrough
- Direct device node passthrough: `--device /dev/dri` (for Intel/AMD GPUs) or `--gpus all` (for NVIDIA).
- Supported APIs: Vulkan (Mesa RADV/ANV), ROCm, CUDA.
- Runs inside rootless containers on Linux hosts. See [Isolation Limits](#6-isolation-limits) for macOS and Docker Desktop.

### Track B: Apple Silicon Native Darwin Execution
- Runs natively on Darwin under the unprivileged `scavenger` account in a dedicated workspace directory (`~scavenger/workspaces/<env_id>`).
- Single-occupancy advisory lease locks ensure only one native workload accesses the unified memory bus at a time.
- Supported APIs: Apple Metal, MLX (`uv run --with mlx`), PyTorch MPS.

---

## 5. Security Checklist

- [x] Dedicated SSH keypair (`~/.ssh/id_ed25519_scavenger`).
- [x] Unprivileged `scavenger` user on each host.
- [x] No `sudo` access for `scavenger`.
- [x] Operator home directory permissions set to `0700` to prevent direct `scavenger` read access. This does not stop a container engine that reads the home directory as the operator (see below).
- [x] Ephemeral jobs run with `--rm` and automatic `trap ... EXIT` workspace deletion.
- [x] Every container runs with a process-table cap (`--pids-limit`, default `4096`) and bounded json-file logs (default `10m` × 3 files) to limit the impact of a fork bomb or runaway build.
- [x] Containers use an explicit network mode: `--network=none` for jobs that need no downloads, `--network=default` otherwise.

---

## 6. Isolation Limits

The isolation guards against accidents, not against a job set on reading the host.

| Path | What it protects | What it does not protect |
| --- | --- | --- |
| Linux, rootless Podman or Docker as `scavenger` | The container runs as `scavenger`'s subuid range. `chmod 700` on the operator home blocks it. `--network=none` disables container networking. | Network access is not restricted in the default network mode. |
| macOS, Docker Desktop | Container resource limits. | **Unverified, likely open:** `ENROLLMENT.md` makes `/var/run/docker.sock` group-writable for `staff`, and `scavenger` is in `staff`. Docker Desktop shares `/Users` with its VM by default and reads those files as the operator's user. A job can run `docker run -v /Users/<operator>:/h alpine ls /h` and may read files that `chmod 700` protects. Docker Desktop also shares `/Volumes`, `/private` and `/var/folders` by default. Narrow Docker Desktop file sharing to `/Users/scavenger` and `/private/tmp`, as `ENROLLMENT.md` describes, before relying on `chmod 700`. |
| Native exec (`scavenger_exec_native`, `kind="native"`) | Runs as the unprivileged `scavenger` user. No sudo. | No sandbox. It reads anything `scavenger` can read, such as world-readable host files and other readable system paths. It has full network access. |

Rootful Docker has the same weakness on any OS: access to its socket is equivalent to root. Do not add `scavenger` to the `docker` group on Linux. Use rootless Podman or rootless Docker.
