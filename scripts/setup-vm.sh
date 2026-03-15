#!/usr/bin/env bash
# Setup a fresh Debian VM for running DecafClaw.
# Run as your regular user (not root), uses sudo where needed.
set -euo pipefail

echo "=== DecafClaw VM Setup ==="

# --- System packages ---
echo "Installing system packages..."
sudo apt-get update
sudo apt-get install -y \
    git \
    curl \
    build-essential \
    python3 \
    python3-dev

# --- Node.js (for npx MCP servers) ---
if ! command -v node &> /dev/null; then
    echo "Installing Node.js via NodeSource..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt-get install -y nodejs
else
    echo "Node.js already installed: $(node --version)"
fi

# --- uv (Python package manager) ---
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "uv already installed: $(uv --version)"
fi

# --- Clone repo ---
REPO_DIR="$HOME/decafclaw"
if [ ! -d "$REPO_DIR" ]; then
    echo "Cloning DecafClaw..."
    git clone https://github.com/lmorchard/decafclaw.git "$REPO_DIR"
else
    echo "Repo already exists at $REPO_DIR"
fi

# --- Install Python dependencies ---
cd "$REPO_DIR"
echo "Installing Python dependencies..."
uv sync

# --- Create .env if it doesn't exist ---
if [ ! -f "$REPO_DIR/.env" ]; then
    echo "Creating .env from example..."
    cp .env.example .env
    echo ""
    echo "!!! IMPORTANT: Edit $REPO_DIR/.env with your actual config !!!"
    echo ""
fi

# --- Create data directories ---
mkdir -p "$REPO_DIR/data/decafclaw/workspace"

# --- Install systemd user service ---
echo "Installing systemd user service..."
mkdir -p "$HOME/.config/systemd/user"
cp "$REPO_DIR/deploy/decafclaw.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload

# --- Enable linger (so service starts at boot, not just on login) ---
echo "Enabling linger for $(whoami)..."
sudo loginctl enable-linger "$(whoami)"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit $REPO_DIR/.env with your LLM, Mattermost, and API keys"
echo "  2. Optionally create $REPO_DIR/data/decafclaw/mcp_servers.json"
echo "  3. Optionally create $REPO_DIR/data/decafclaw/HEARTBEAT.md"
echo "  4. Start the service:  systemctl --user start decafclaw"
echo "  5. Enable on boot:     systemctl --user enable decafclaw"
echo "  6. View logs:          journalctl --user -u decafclaw -f"
echo ""
