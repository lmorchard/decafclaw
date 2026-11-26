FROM node:22-bookworm-slim

# Avoid interactive prompts during apt installs
ENV DEBIAN_FRONTEND=noninteractive

# Install essential dev tools, runtimes, build utilities, and gh CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    git \
    gnupg \
    jq \
    make \
    python3 \
    python3-pip \
    python3-venv \
    ripgrep \
    tar \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install official GitHub CLI (gh)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Install Astral uv for blazing fast Python project management
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="/usr/local/bin" sh

# Install OpenCode CLI globally
RUN npm install -g opencode-ai

# Provider configuration for the homelab model gateway. The API key is resolved
# from OPENAI_API_KEY at runtime, so no credential is baked into the image.
# Supply the key through scavenger_env_create(env_vars={...}) or --env.
RUN mkdir -p /root/.config/opencode && cat > /root/.config/opencode/opencode.json <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "otari": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Otari Homelab Gateway",
      "options": {
        "baseURL": "{env:OPENAI_BASE_URL}",
        "apiKey": "{env:OPENAI_API_KEY}"
      },
      "models": {
        "deepseek-v4.1-flash-1017": {
          "name": "DeepSeek V4.1 Flash 1017",
          "id": "deepseek/deepseek-v4.1-flash-1017"
        }
      }
    }
  }
}
JSON

# Set up working directory
WORKDIR /workspace

# Default command keeps the container alive for scavenger execs
CMD ["sleep", "infinity"]
