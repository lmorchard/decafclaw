# Installation & Setup

DecafClaw is a Python project managed with [uv](https://docs.astral.sh/uv/). It requires Python 3.13+ and an LLM provider (Vertex AI, OpenAI, or any OpenAI-compatible endpoint).

## Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** — Python package/project manager
- **An LLM provider** — Vertex AI (Gemini), OpenAI, or any OpenAI-compatible API (LiteLLM, Ollama, vLLM, OpenRouter). See [LLM Providers](providers.md) for setup.

Optional:
- **Mattermost instance** — for chat bot mode (interactive terminal mode works without it)
- **Web UI** — set `HTTP_ENABLED=true` and create a token, see [Web UI](web-ui.md)
- **Tabstack API key** — for web browsing/research tools ([tabstack.ai](https://tabstack.ai))
- **Embedding API** — for semantic search (default: `text-embedding-004` via the same LLM endpoint)

## Install

```bash
git clone https://github.com/lmorchard/decafclaw.git
cd decafclaw
uv sync
```

This installs all dependencies into a local `.venv`.

## Configure

Configuration lives in `data/{agent_id}/config.json`. Env vars (including `.env`) override config file values. See [Configuration Reference](config.md) for the full list of settings.

### LLM provider (required)

The fastest path is to put a provider config in `data/decafclaw/config.json`:

```json
{
  "providers": { "vertex": { "type": "vertex", "project": "my-project" } },
  "model_configs": { "gemini-flash": { "provider": "vertex", "model": "gemini-2.5-flash" } },
  "default_model": "gemini-flash"
}
```

See [LLM Providers](providers.md) for auth setup per provider (Vertex ADC, OpenAI, OpenAI-compatible).

### Quick env vars

Most common settings to put in `.env`:

```bash
# Mattermost (optional — omit for terminal-only mode)
MATTERMOST_URL=https://mattermost.example.com
MATTERMOST_TOKEN=xxx
MATTERMOST_BOT_USERNAME=decafclaw

# Web UI (optional)
HTTP_ENABLED=true
HTTP_SECRET=your-random-secret

# Tabstack (optional — for web tools)
TABSTACK_API_KEY=xxx

# Semantic search: set to "semantic" for embedding-based search
MEMORY_SEARCH_STRATEGY=semantic

# Logging
LOG_LEVEL=INFO
```

The full table of env vars and defaults is in [Configuration Reference](config.md).

### Semantic search caveat

Semantic search uses [sqlite-vec](https://github.com/asg017/sqlite-vec), which requires a Python/SQLite build with extension loading enabled. Homebrew Python and python.org installers work fine. If you see errors from `enable_load_extension` or `sqlite_vec.load()`, either use a compatible Python build or leave `MEMORY_SEARCH_STRATEGY` unset (defaults to substring).

## Run

```bash
make run          # Interactive terminal mode (no Mattermost needed)
make dev          # Auto-restart on file changes (development)
make debug        # With debug logging
make run-pro      # With gemini-2.5-pro model
```

If `MATTERMOST_URL` and `MATTERMOST_TOKEN` are set, the agent runs as a Mattermost bot. Otherwise it falls back to interactive terminal mode. The HTTP/web UI server runs alongside either mode when `HTTP_ENABLED=true`.

**Warning:** Only one bot instance can connect to Mattermost at a time. A second instance will silently miss websocket events.

## Test

```bash
make test     # Run pytest
make lint     # Compile-check all Python source files
make check    # Lint + type check (Python + JS)
```

## Next steps

- **[Web UI](web-ui.md)** — set up browser-based chat with vault editor
- **[Skills](skills.md)** — skills are discovered automatically from `data/{agent_id}/workspace/skills/`
- **[MCP Servers](mcp-servers.md)** — configure in `data/{agent_id}/mcp_servers.json`
- **[Heartbeat](heartbeat.md)** — set `HEARTBEAT_INTERVAL` and `HEARTBEAT_CHANNEL` or `HEARTBEAT_USER`
- **[Scheduled Tasks](schedules.md)** — cron-style tasks in `data/{agent_id}/schedules/`
- **[Semantic Search](semantic-search.md)** — run `make reindex` after first setup to build the embedding index
- **Prompt customization** — place custom `SOUL.md`, `AGENT.md`, or `USER.md` in `data/{agent_id}/` to override bundled prompts
- **[Deployment](deployment.md)** — systemd service for a persistent server install
