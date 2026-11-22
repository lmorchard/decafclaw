#!/bin/bash
# setup-darwin-mlx.sh — Idempotent Apple Silicon MLX environment setup
set -e

# 1. Ensure uv is available
if ! command -v uv >/dev/null 2>&1; then
    if [ -f "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    else
        echo "Installing uv to ~/.local/bin..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi

# 2. Ensure virtualenv exists in current workspace
if [ ! -d ".venv" ]; then
    echo "Creating virtualenv with Python 3.12..."
    uv venv --python 3.12 .venv
fi

# 3. Ensure MLX is installed
echo "Verifying MLX packages..."
uv pip install --quiet mlx mlx-lm

# 4. Verify GPU device
python3 -c "import mlx.core as mx; print('MLX Hardware Ready:', mx.default_device())"
