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
