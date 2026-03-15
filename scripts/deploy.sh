#!/usr/bin/env bash
# Deploy latest code to the VM. Run from the repo directory.
# Usage: ./scripts/deploy.sh
set -euo pipefail

# Ensure uv/uvx/npx are on PATH (needed for non-interactive SSH)
export PATH="$HOME/.local/bin:$PATH"

REPO_DIR="$HOME/decafclaw"
cd "$REPO_DIR"

echo "=== DecafClaw Deploy ==="

# Pull latest code
echo "Pulling latest..."
git pull --ff-only

# Install/update dependencies
echo "Syncing dependencies..."
uv sync

# Restart the service
echo "Restarting service..."
systemctl --user restart decafclaw

# Show status
sleep 2
systemctl --user status decafclaw --no-pager

echo ""
echo "Deploy complete. View logs: journalctl --user -u decafclaw -f"
