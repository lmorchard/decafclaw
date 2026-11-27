FROM node:22-bookworm-slim

# Avoid interactive prompts during apt installs
ENV DEBIAN_FRONTEND=noninteractive

# Install essential dev tools, runtimes, build utilities, and gh CLI
#
# openssh-client and procps are load-bearing for unattended worker sessions:
#
# - ssh: the scavenger fleet is driven over SSH. Without a client, a worker
#   cannot reach the fleet at all, and five scavenger tests skip rather than
#   run. More importantly, a worker asked to verify host-level behavior has no
#   way to do it and will substitute a plausible inference for a measurement
#   (see #1787 / PR #1996, where the worker "fixed" a Podman incompatibility
#   that does not exist because it could not run `podman` or reach a host).
# - procps (ps/pgrep/pkill): a worker that backgrounds a process has no way to
#   confirm it is alive or to stop it. `kill` on a shell PID does not reach the
#   child, so verifying death matters as much as sending the signal.
#
# Container engines (podman/docker) are deliberately NOT installed: running a
# container engine inside the worker container is not sound. Host-level checks
# belong to `scavenger_exec_native`, which the controller runs, not a worker.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    git \
    gnupg \
    jq \
    make \
    openssh-client \
    procps \
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

# Install OpenCode 2.x.
#
# The package was renamed: `opencode-ai` is the legacy 1.x line and stops at
# 1.19.6, while `@opencode/cli` carries 2.x. The runner probes the installed
# major and only passes `--standalone` to 2.x, so a 1.x image yields working but
# slower shared-server runs; pinning here keeps the image on the line ghflow
# targets and makes an upgrade a deliberate edit rather than a silent drift.
#
# The version is pinned on purpose. Installing unpinned is what let this image
# sit on 1.19.6 while the runner expected 2.x.
ARG OPENCODE_VERSION=2.4.2
RUN npm install -g @opencode/cli@${OPENCODE_VERSION}

# Fail the build if the installed major is not 2.x. A recipe that silently
# produces the wrong major is the failure this guard exists to prevent.
RUN test "$(opencode --version | sed 's/^[^0-9]*//' | cut -d. -f1)" = "2" \
    || { echo "Expected OpenCode 2.x, got: $(opencode --version)"; exit 1; }

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
