---
name: scavenger
description: Offload heavy compilation, test suites, subagents, and ML/GPU workloads to an operator's LAN scavenger compute fleet over SSH. Replaces cloud VMs with zero-cost local compute.
---

# LAN Scavenger Compute Fleet

Use this skill whenever you need to run resource-intensive work (test suites, language server indexing, compilation, deep subagent turns, or local ML/LLM inference) without exhausting memory or CPU on the DecafClaw host.

The scavenger fleet borrows compute from idle LAN workstations and laptops over Tailscale/SSH configured in `~/.config/scavenger/fleet.json`.

All remote execution runs under an isolated **`scavenger`** account with zero sudo rights, zero access to operator personal files, and automatic ephemeral cleanup.

---

## 1. Dynamic Fleet Discovery

**Always probe before assuming what hardware exists.** Fleet inventory is defined by the operator in `~/.config/scavenger/fleet.json`. Call `scavenger_probe()` to discover available nodes, their CPU architectures (`arm64` vs `x86_64`), container engines (`podman` vs `docker`), power state (AC vs battery), and courtesy eligibility:

```python
scavenger_probe()
```

Output reports which nodes are online, their architecture, and whether courtesy guards (battery power or active game streaming) flag them as busy.

---

## 2. Ephemeral One-Shot Jobs

For quick test runs, builds, or linting without maintaining state across turns:

### `scavenger_run(command, repo_path="", host="", arch="", image="alpine:latest", mem="16g", cpus=8)`
- **`command`**: Shell command to run inside the container (e.g. `pytest tests/`, `cargo test`).
- **`repo_path`**: Local git directory to bundle and mount at `/workspace` (e.g. `.` or relative path).
- **`arch`**: Optional architecture filter (`"arm64"` or `"x86_64"`). Scavenger automatically steers the job to an eligible host matching this architecture.
- **`host`**: Optional specific host name discovered from `scavenger_probe()`.
- **`image`**: Container image (default: `"alpine:latest"`).
- **`mem`** / **`cpus`**: Resource limits (default: `"16g"`, `8`).

### `scavenger_exec_native(host, command)`
Runs a command natively on the target host OS under the `scavenger` user account.
- **Primary Use Case:** Apple Silicon GPU/Metal workloads via `uv` (e.g. MLX fine-tuning, PyTorch MPS, Whisper), because Linux container engines on macOS cannot access host Metal drivers.

### `scavenger_wake(host)`
Sends a Wake-on-LAN magic packet to a sleep-capable host (such as an S3 desktop or an AC-connected MacBook in DarkWake) and waits for it to come online.

---

## 3. Stateful / Long-Running Environment Tools

For multi-turn subagent sessions, full build pipelines, or dev environments where toolchains, `node_modules/`, Gradle caches, or virtualenvs must stay preserved across turns:

### `scavenger_env_create(env_id, host="", kind="container", image="alpine:latest", recipe="", setup_script="", sync_dir="", mem="16g", cpus=8, ttl="2h", arch="")`
Reserves a warm, isolated environment across turns:
- **`recipe`**: Pre-configured build recipe from `dockerfiles/` or `skills/scavenger/dockerfiles/` (e.g. `"android-builder"`, `"node-dev"`, `"rust-builder"`). Automatically builds and caches the remote container image on the target host.
- **`setup_script`**: Idempotent setup script executed once inside the workspace (e.g. `skills/scavenger/setup-scripts/setup-darwin-mlx.sh`). Essential for native Darwin nodes.
- **`kind="container"`**: Creates a named container with a persistent volume (`/workspace`) on the chosen host.
- **`kind="native"`**: Creates a scoped workspace directory (`~scavenger/workspaces/<env_id>`) on an Apple Silicon Mac with an exclusive single-occupancy lease. The workspace holds a `.scavenger-lease.json` (env id, expiry) that the host check reads.
- **`sync_dir`**: Optional local directory path initial-synced into the workspace via compressed tar stream.
- **`ttl`**: Automatic expiry time (e.g. `"2h"`, `"30m"`).

### `scavenger_env_exec(env_id, command, timeout=1800, bg=False, wake_on="")`
Executes a command inside the warm environment with sub-second launch latency.
- **`bg=True`**: Runs command asynchronously in the background.
- **`wake_on`**: Automated wake trigger (`"exit"`, `"pattern:<regex>"`, or `"file:<path>"`). Arms an event watch and automatically wakes DecafClaw via `watch_file` when the trigger condition fires.

### `scavenger_env_watch(env_id, watch_type="exit", target=".scavenger_exec.log", pattern="", timeout=3600, reason="")`
Arms an independent event trigger on an environment (monitoring log patterns, exit codes, or generated artifacts) and wires it into DecafClaw's autonomous wake loop.

### `scavenger_env_info(env_id)`
Inspects the status, host, target container/volume, and remaining TTL for a specific reservation.

### `scavenger_env_terminal(env_id, shell="sh")`
Opens an interactive live xterm.js terminal tab in the Canvas connected directly into the remote container or workspace over SSH.

### `scavenger_env_sync(env_id, direction="up"|"down", local_path="...")`
Transfers files up to or down from the reserved workspace via fast compressed tar stream.

### `scavenger_env_destroy(env_id)`
Tears down the container and volume (or removes the native workspace directory) and releases the lease lock.

### `scavenger_env_list()` / `scavenger_reap()`
Lists active reservations with remaining TTL; `scavenger_reap()` cleans up any expired leases across the fleet.

Each host is the record of its environments. Listing and reaping read the `decafclaw.*` labels on containers and volumes (and the native lease files) on every reachable host. `~/.config/scavenger/leases.json` is only a cache: it is rebuilt from the hosts, a corrupt copy is logged and moved aside as `leases.json.corrupt-<time>`, and writes take a file lock. An unreachable host keeps its cached records and is skipped by reap.

---

## 4. Recipe & Setup Libraries

- **Workspace Custom Recipes (`dockerfiles/`)**: Operators and agents can author custom Dockerfiles in `dockerfiles/<name>.Dockerfile` in the workspace root.
- **Bundled Container Recipes (`dockerfiles/` in skill)**:
  - `android-builder.Dockerfile`: Android SDK 35, OpenJDK 17, Node 22 LTS, Gradle.
  - `node-dev.Dockerfile`: Node 22 Bookworm with C/C++ compilation tools.
- **Bundled Native Darwin Scripts (`setup-scripts/` in skill)**:
  - `setup-darwin-mlx.sh`: Idempotent `uv` virtualenv and Apple Silicon Metal MLX setup.

---

## 5. CLI Alternative

The underlying deterministic Python CLI is available directly within the skill:
```bash
# Probe fleet:
python contrib/skills/scavenger/scavenger.py probe

# Run one-shot job:
python contrib/skills/scavenger/scavenger.py run --repo . 'pytest tests/'

# Stateful environments:
python contrib/skills/scavenger/scavenger.py env-create my-env --image node:22-alpine --sync-dir decafclaw/contrib/android
python contrib/skills/scavenger/scavenger.py env-exec my-env 'npm test'
python contrib/skills/scavenger/scavenger.py env-destroy my-env
```

Operator configuration is loaded from `~/.config/scavenger/fleet.json`. See `fleet.example.json` and `ENROLLMENT.md` for enrollment instructions.
