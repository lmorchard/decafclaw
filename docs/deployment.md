# Deployment

Deploy DecafClaw as a persistent systemd service on a Debian VM.

## Prerequisites

- Debian 12+ VM (tested on Proxmox)
- Network access to your Mattermost server and LLM endpoint
- SSH access as a regular user

## Quick Start

### 1. Initial setup

SSH to the VM and run the setup script:

```bash
# Clone the repo first (or scp the script over)
git clone https://github.com/lmorchard/decafclaw.git ~/decafclaw
cd ~/decafclaw
./scripts/setup-vm.sh
```

This installs Python, uv, Node.js, clones the repo, installs dependencies, and sets up the systemd service.

### 2. Configure

Edit `~/decafclaw/.env` with your settings:

```bash
vim ~/decafclaw/.env
```

Required:
- LLM connection — either a `providers` + `model_configs` block in `config.json` (preferred, see [installation.md](installation.md) and [providers.md](providers.md)) or the legacy `LLM_URL` / `LLM_MODEL` / `LLM_API_KEY` env vars
- `MATTERMOST_URL`, `MATTERMOST_TOKEN`, `MATTERMOST_BOT_USERNAME`

Optional:
- `TABSTACK_API_KEY` — for web browsing tools (via tabstack skill)
- `HEARTBEAT_INTERVAL`, `HEARTBEAT_CHANNEL` — for periodic tasks
- See [installation.md](installation.md) for full config reference

### 3. Start

```bash
systemctl --user start decafclaw
systemctl --user enable decafclaw   # start on boot
```

### 4. Verify

```bash
systemctl --user status decafclaw
journalctl --user -u decafclaw -f   # live logs
```

## Deploying Updates

### Via Chat Command (/update)

You can check for and apply updates directly from the web chat interface (or Mattermost via `!update`):

1. **Check for updates:** Type `/update` in the chat input. DecafClaw fetches latest commits from upstream (`@{u}`) and displays incoming commits (`git log HEAD..@{u} --oneline`). If already up to date, it lets you know and stops.
2. **Safety guards:**
   - **Active turns:** If any conversation is currently running an active turn, `/update` refuses and names the busy conversations. Nothing is pulled or restarted.
   - **Dirty working tree:** If the working tree has uncommitted modifications or staged changes, `/update` aborts before pulling.
   - **Fast-forward only:** The update requires that the branch can be fast-forwarded (`git pull --ff-only`).
3. **Apply and restart:** Type `/update confirm` (or `/update --yes`). DecafClaw pulls the commits, runs `uv sync`, and restarts the service (`systemctl --user restart decafclaw`). If `uv sync` fails, it reports the error and does not restart.

*Note:* `/update` is a human-only side-effect command — it initiates no LLM turn, writes no archive entry, and is strictly inaccessible to agent tools and skills.

### Via SSH

After pushing changes to the repo:

```bash
ssh vm
cd ~/decafclaw
./scripts/deploy.sh
```

Or manually:

```bash
cd ~/decafclaw
git pull
uv sync
systemctl --user restart decafclaw
```

## Service Management

```bash
# Status
systemctl --user status decafclaw

# Logs (live)
journalctl --user -u decafclaw -f

# Logs (last 100 lines)
journalctl --user -u decafclaw -n 100

# Restart
systemctl --user restart decafclaw

# Stop
systemctl --user stop decafclaw

# Disable auto-start
systemctl --user disable decafclaw
```

## How It Works

### Systemd user service

The service runs as your user (not root) via `systemctl --user`. The unit file is at `~/.config/systemd/user/decafclaw.service`.

Key settings:
- `Restart=on-failure` — systemd restarts the process if it exits with an error
- `RestartSec=5` — 5 second delay between restarts
- `TimeoutStopSec=15` — graceful shutdown gets 15 seconds before SIGKILL
- `EnvironmentFile` — loads `.env` for configuration

Combined with DecafClaw's built-in auto-restart loop (10 attempts with backoff), the service is very resilient: the app tries to recover internally first, and if it still crashes, systemd brings it back.

### Boot persistence

`loginctl enable-linger` allows the user service to start at boot without an active login session.

## MCP Servers and Skills

MCP server subprocesses (stdio) are managed by DecafClaw and start/stop with the service. Configure them in `data/decafclaw/mcp_servers.json`.

Skills in `data/decafclaw/workspace/skills/` are discovered on startup. Community skills from ClawHub can be installed there.

## Subagent Memory Limits

The `opencode_send` and `claude_code_send` subagent CLIs run as child processes of the daemon. A runaway subagent (an LSP index, a test run) can otherwise exhaust host memory, and the kernel OOM killer can then select the daemon itself. Each subagent process therefore runs in its own transient systemd scope with a memory cap:

```
systemd-run --user --scope --quiet -p MemoryMax=50% -p MemoryHigh=40% -- <cli>
```

When the subagent goes over `MemoryMax`, the kernel's cgroup OOM killer kills the subagent's processes, and the daemon — outside the scope — keeps running. The tool call that started the killed process returns an error result that names the kill.

**Defaults** (percentages of host memory, so they do not depend on VM size):

| Setting | Default | Env var |
|---|---|---|
| `opencode` cap enabled | `true` | `OPENCODE_MEMORY_CAP_ENABLED` |
| `opencode` memory cap (hard) | `50%` | `OPENCODE_MEMORY_MAX` |
| `opencode` memory threshold (soft) | `40%` | `OPENCODE_MEMORY_HIGH` |
| `claude_code` cap enabled | `true` | `CLAUDE_CODE_MEMORY_CAP_ENABLED` |
| `claude_code` memory cap (hard) | `50%` | `CLAUDE_CODE_MEMORY_MAX` |
| `claude_code` memory threshold (soft) | `40%` | `CLAUDE_CODE_MEMORY_HIGH` |

Values are systemd memory specs: a percentage of physical memory or an absolute size (e.g. `2G`, `300M`). `MemoryHigh` is the soft threshold where the kernel starts reclaiming pressure; `MemoryMax` is the hard limit where the OOM killer fires.

**Turning the cap off.** The canonical switch is `memory_cap_enabled`, per skill, in `config.json` or via its env var:

```json
{
  "skills": {
    "opencode": { "memory_cap_enabled": false }
  }
}
```

(`OPENCODE_MEMORY_CAP_ENABLED=false` / `CLAUDE_CODE_MEMORY_CAP_ENABLED=false` work from the environment as well.) An explicitly disabled cap runs the CLI uncapped with no warning. As an alternative, set both limit values to empty in `config.json` per skill (e.g. `"memory_max": "", "memory_high": ""`); an empty *env var* is treated as unset by the config layer (project-wide convention), so the config.json form is the way to disable via empty limits.

**Examining delegation.** The cap only applies when the user systemd instance has delegated the memory controller:

```bash
cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers
```

`memory` should be listed. If it is not, delegation is a host setup task (out of scope for this repo); DecafClaw logs one warning and runs the subagent uncapped. If you see the warning on a host where `memory` *is* listed, check that `XDG_RUNTIME_DIR` is set in the service environment (the user manager bus that `systemd-run --user` needs).

**Why the service unit has no `MemoryMax`.** Setting `MemoryMax` on `decafclaw.service` would put the daemon inside the same cap as its subagents: the cgroup OOM killer could then select the daemon, and the default `OOMPolicy=stop` would stop the whole unit. Per-child scopes keep the daemon outside every cap.

## Shell Command Limits

Agent-authored shell commands — the `shell` tool and every background job — are the least predictable child processes in the system. A build, an LSP index, a runaway script, or a fork bomb can outgrow the host, and when it does the VM usually stops answering SSH before the OOM killer selects anything. Each shell command therefore runs in its own transient systemd scope:

```
systemd-run --user --scope --quiet \
  -p MemoryMax=40% -p MemorySwapMax=0 -p CPUWeight=50 -p TasksMax=512 -- \
  /bin/sh -c <command>
```

**Defaults** (`config.shell`, overridable per-key in `config.json` or via env):

| Setting | Default | Env var | Effect |
|---|---|---|---|
| `resource_limits_enabled` | `true` | `SHELL_RESOURCE_LIMITS_ENABLED` | master switch |
| `memory_max` | `40%` | `SHELL_MEMORY_MAX` | hard cap; cgroup OOM killer fires here |
| `memory_high` | empty | `SHELL_MEMORY_HIGH` | soft reclaim threshold — see below |
| `memory_swap_max` | `0` | `SHELL_MEMORY_SWAP_MAX` | swap available to the command |
| `cpu_weight` | `50` | `SHELL_CPU_WEIGHT` | relative CPU share (systemd default is 100) |
| `tasks_max` | `512` | `SHELL_TASKS_MAX` | hard task/process count |

Set `cpu_weight` or `tasks_max` to `none` (env) or `null` (config.json) to drop that individual limit. An explicitly disabled cap (`resource_limits_enabled: false`) runs commands uncapped and logs nothing — an intentional disable is not a missing capability.

**`memory_swap_max` defaults to `0` for a reason.** A cgroup's `memory.swap.max` is `max` by default, so a `MemoryMax` alone lets a runaway spill into swap instead of dying — which reproduces the exact thrash the cap exists to prevent on a host given swap as a shock absorber. `MemoryMax` therefore implies `MemorySwapMax=0` unless you set `memory_swap_max` explicitly.

**Why `memory_high` is empty by default.** `MemoryHigh` is a *soft* threshold: the kernel reclaims instead of killing. With swap available that means "page out and survive". With `memory_swap_max: 0` there is nowhere to reclaim to, so the kernel instead **throttles** the allocating process — it slows to near-zero progress, holds its memory, and never trips `MemoryMax`. The command burns its whole timeout and the agent sees a timeout rather than "you hit your memory cap". A hard cap plus swap disabled gives a fast, unambiguous kill. Set `memory_high` only if you also allow swapping and want graceful reclaim.

**Reporting.** A command killed by its cap says so instead of returning a bare `-9`. Note that when the kernel OOM-kills something *inside* a scope, the victim is usually a child of `sh` rather than `sh` itself, and systemd's default `OOMPolicy=stop` then stops the scope — so the process the caller waited on reports SIGTERM (`-15`), not SIGKILL. That case is reported as a *possible* cap kill with a pointer at `journalctl --user`, since a deliberate kill looks identical.

**Graceful degradation.** Limits are applied per controller and degrade independently: on a host that delegates `cpu` and `pids` but not `memory`, commands still get `CPUWeight` and `TasksMax`, and the missing memory cap is logged once. When no requested controller is delegated — or `systemd-run` is absent, as on macOS development — commands run uncapped exactly as before.

**Examining delegation.** Same probe as the subagent caps; `cpu`, `memory`, and `pids` should all be listed:

```bash
cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers
```

**Host precondition: swap.** Caps bound a single command, not the host's total footprint. On a small VM, give the kernel somewhere to put cold pages so a near-miss is a slowdown rather than a lockup — see `lmorchard-homelab`'s `decafclaw_host` role (`decafclaw_host_swap_*`), which provisions a 4 GB swapfile at `vm.swappiness=20`. Swap is a shock absorber here, never a substitute for `memory_max`: with `memory_swap_max: 0` a capped command cannot use it at all.

## Updating the Systemd Unit

If you modify `deploy/decafclaw.service`:

```bash
cp deploy/decafclaw.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user restart decafclaw
```

## Troubleshooting

### Service won't start

```bash
journalctl --user -u decafclaw -n 50   # check logs
uv run decafclaw                         # try running manually
```

### Bot connects but doesn't respond

- Check that only one instance is running (second instance silently misses websocket events)
- Check `MATTERMOST_REQUIRE_MENTION` — in channels, the bot only responds to @-mentions by default

### MCP servers failing

```bash
journalctl --user -u decafclaw | grep "MCP server"
```

MCP servers need their binaries available (node/npx for JS servers, python/uvx for Python servers). The setup script installs Node.js; Python MCP servers may need additional setup.

### Heartbeat not firing

- Check `HEARTBEAT_INTERVAL` is set (not empty)
- Check `HEARTBEAT_CHANNEL` or `HEARTBEAT_USER` is set for Mattermost reporting
- Check `data/decafclaw/HEARTBEAT.md` exists with content
