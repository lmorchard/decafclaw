---
name: scavenger
description: Offload heavy compilation, test suites, subagents, and ML/GPU workloads to an operator's LAN scavenger compute fleet over SSH. Replaces cloud VMs with zero-cost local compute.
---

# LAN Scavenger Compute Fleet

Use this skill whenever you need to run resource-intensive work (test suites, language server indexing, compilation, deep subagent turns, or local ML/LLM inference) without exhausting memory or CPU on the DecafClaw host.

The scavenger fleet borrows compute from idle LAN workstations and laptops over Tailscale/SSH configured in `~/.config/scavenger/fleet.json`.

All remote execution runs under a dedicated, unprivileged **`scavenger`** account with no sudo rights and automatic ephemeral cleanup. The operator's home directory is locked down with `chmod 700`.

**What the isolation covers, and what it does not.** It guards against accidents, not against a job set on reading the host.
- Container jobs (`scavenger_run`, `kind="container"`) mount only the job workspace. On Linux with a rootless engine, the container cannot see the operator's files.
- On macOS with Docker Desktop, `scavenger` talks to a Docker socket shared with the operator's Docker Desktop. Docker Desktop shares `/Users` with its VM by default and reads it as the operator's user, so a job that runs `docker run -v /Users/<operator>:/h ...` may read files that `chmod 700` protects (other default shares such as `/Volumes` and `/var/folders` are exposed the same way). Unless the operator has narrowed Docker Desktop file sharing (see `ENROLLMENT.md`), treat macOS container jobs as able to read the operator's files.
- `scavenger_exec_native` and `kind="native"` environments run with no sandbox as `scavenger`. They can read anything on the host that is readable by `scavenger`, including world-readable files outside the operator's home, and they have full network access.
- Do not send secrets or private data to a scavenger host that you would not give to the `scavenger` account.

---

## 1. Dynamic Fleet Discovery

**Always probe before assuming what hardware exists.** Fleet inventory is defined by the operator in `~/.config/scavenger/fleet.json`. Call `scavenger_probe()` to discover available nodes, their CPU architectures (`arm64` vs `x86_64`), container engines (`podman` vs `docker`), power state (AC vs battery), and courtesy eligibility:

```python
scavenger_probe()
```

Output reports which nodes are online, their architecture, and whether courtesy guards (battery power or active game streaming) flag them as busy.

Probing also reaps expired environments first: any reservation past its TTL is destroyed and its lease released before the probe runs. A host that cannot be reached is skipped with a warning (its lease is retained for the next sweep) and does not block the probe.

---

## 2. Ephemeral One-Shot Jobs

For quick test runs, builds, or linting without maintaining state across turns:

### `scavenger_run(command, repo_path="", host="", arch="", image="alpine:latest", mem="16g", cpus=8, network="default")`
- **`command`**: Shell command to run inside the container (e.g. `pytest tests/`, `cargo test`).
- **`repo_path`**: Local git directory to bundle and mount at `/workspace` (e.g. `.` or relative path).
- **`arch`**: Optional architecture filter (`"arm64"` or `"x86_64"`). Scavenger automatically steers the job to an eligible host matching this architecture.
- **`host`**: Optional specific host name discovered from `scavenger_probe()`.
- **`image`**: Container image (default: `"alpine:latest"`).
- **`mem`** / **`cpus`**: Resource limits (default: `"16g"`, `8`).
- **`network`**: Container network mode, `"none"` or `"default"` (default: `"default"`). **Prefer `"none"` for test runs that need no downloads** — it blocks all container network access, including the LAN. Only use `"default"` when the job must fetch dependencies or reach external hosts.

### `scavenger_exec_native(host, command)`
Runs a command natively on the target host OS under the `scavenger` user account.
- **Primary Use Case:** Apple Silicon GPU/Metal workloads via `uv` (e.g. MLX fine-tuning, PyTorch MPS, Whisper), because Linux container engines on macOS cannot access host Metal drivers.

### `scavenger_wake(host)`
Sends a Wake-on-LAN magic packet to a sleep-capable host (such as an S3 desktop or an AC-connected MacBook in DarkWake) and waits for it to come online.

---

## 3. Stateful / Long-Running Environment Tools

For multi-turn subagent sessions, full build pipelines, or dev environments where toolchains, `node_modules/`, Gradle caches, or virtualenvs must stay preserved across turns:

### `scavenger_env_create(env_id, host="", kind="container", image="alpine:latest", recipe="", setup_script="", sync_dir="", mem="16g", cpus=8, network="default", ttl="2h", arch="")`
Reserves a warm, isolated environment across turns:
- **`recipe`**: Pre-configured build recipe from the recipe directory (`recipes_dir` in `fleet.json`, else the workspace `dockerfiles/`) or the skill's bundled `dockerfiles/` (e.g. `"android-builder"`, `"node-dev"`, `"rust-builder"`). Automatically builds and caches the remote container image on the target host.
- **`setup_script`**: Idempotent setup script executed once inside the workspace (e.g. `skills/scavenger/setup-scripts/setup-darwin-mlx.sh`). Essential for native Darwin nodes.
- **`kind="container"`**: Creates a named container with a persistent volume (`/workspace`) on the chosen host.
- **`kind="native"`**: Creates a scoped workspace directory (`~scavenger/workspaces/<env_id>`) on an Apple Silicon Mac with an exclusive single-occupancy lease. The workspace holds a `.scavenger-lease.json` (env id, expiry) that the host check reads.
- **`sync_dir`**: Optional local directory path initial-synced into the workspace via compressed tar stream.
- **`network`**: Container network mode, `"none"` or `"default"` (default: `"default"`). Use `"none"` when the environment needs no downloads — it blocks all container network access, including the LAN.
- **`ttl`**: Automatic expiry time (e.g. `"2h"`, `"30m"`). Expired environments are reaped automatically before the new reservation is made, so stale containers and native slots do not linger.

**Container security limits.** Every container is created with a process-table cap (`--pids-limit`, default `4096`) and a bounded json-file log size (default `10m` × 3 files) so a fork bomb or runaway build cannot exhaust the host. Both are configurable per host in `fleet.json` under `limits` (`pids_limit`, `log_max_size`, `log_max_file`).

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
Lists active reservations with remaining TTL; `scavenger_reap()` cleans up any expired leases across the fleet. Expired environments are also reaped automatically by `scavenger_probe()` and `scavenger_env_create()`, so a manual reap is only needed to force an immediate sweep.

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

The underlying deterministic Python CLI is available directly within the skill. It uses only the standard library and works outside decafclaw. Every command accepts `--json` and exits non-zero on failure. See `SKILL-cli.md` for the full command list (including `wake`, `exec-native`, and `env-info`).

```bash
# Probe fleet:
python contrib/skills/scavenger/scavenger.py probe --json

# End-to-end check of one real host (run after enrolling it):
python contrib/skills/scavenger/scavenger.py selfcheck desktop-linux

# Run one-shot job:
python contrib/skills/scavenger/scavenger.py run --repo . 'pytest tests/'

# Stateful environments:
python contrib/skills/scavenger/scavenger.py env-create my-env --image node:22-alpine --sync-dir decafclaw/contrib/android
python contrib/skills/scavenger/scavenger.py env-exec my-env 'npm test'
python contrib/skills/scavenger/scavenger.py env-destroy my-env
```

Operator configuration is loaded from `~/.config/scavenger/fleet.json`. See `fleet.example.json` and `ENROLLMENT.md` for enrollment instructions.
