# Installation & Setup

DecafClaw is a Python project managed with [uv](https://docs.astral.sh/uv/). It requires Python 3.13+ and an OpenAI-compatible LLM endpoint.

## Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** — Python package/project manager
- **An LLM endpoint** — any OpenAI-compatible API (LiteLLM, ollama, vLLM, OpenRouter, etc.)

Optional:
- **Mattermost instance** — for chat bot mode (interactive terminal mode works without it)
- **Tabstack API key** — for web browsing/research tools ([tabstack.ai](https://tabstack.ai))
- **Embedding API** — for semantic search (default: text-embedding-004 via the same LLM endpoint)

## Install

```bash
git clone https://github.com/lmorchard/decafclaw.git
cd decafclaw
uv sync
```

This installs all dependencies into a local `.venv`.

## Configure

```bash
cp .env.example .env
```

Edit `.env` with your settings:

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `LLM_URL` | OpenAI-compatible chat completions endpoint | `http://localhost:4000/v1/chat/completions` |
| `LLM_MODEL` | Model name | `gemini-2.5-flash` |
| `LLM_API_KEY` | API key (use `dummy` if not needed) | `your-key` |

### Mattermost (optional — for chat bot mode)

| Variable | Description |
|----------|-------------|
| `MATTERMOST_URL` | Mattermost server URL |
| `MATTERMOST_TOKEN` | Bot account token |
| `MATTERMOST_BOT_USERNAME` | Bot username |
| `MATTERMOST_REQUIRE_MENTION` | Only respond when @-mentioned in channels (default: `true`) |

### Tabstack (optional — for web tools)

| Variable | Description |
|----------|-------------|
| `TABSTACK_API_KEY` | Tabstack API key |
| `TABSTACK_API_URL` | Override API URL (default: production) |

### Semantic search (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_MODEL` | `text-embedding-004` | Embedding model name |
| `EMBEDDING_URL` | Falls back to `LLM_URL` | Embedding API endpoint |
| `EMBEDDING_API_KEY` | Falls back to `LLM_API_KEY` | Embedding API key |
| `MEMORY_SEARCH_STRATEGY` | `substring` | `substring` or `semantic` |

### Other settings

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_HOME` | `./data` | Root directory for agent data |
| `AGENT_ID` | `decafclaw` | Agent identifier (used in data paths) |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG` for verbose) |
| `MAX_TOOL_ITERATIONS` | `30` | Max tool calls per agent turn |

### Streaming

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_STREAMING` | `true` | Stream tokens as they arrive. `false` = wait for complete response. |
| `LLM_SHOW_TOOL_CALLS` | `true` | Show tool call names during streaming. |
| `LLM_STREAM_THROTTLE_MS` | `200` | Min interval between Mattermost placeholder edits (ms). |

See also [streaming.md](streaming.md), [conversations.md](conversations.md) for compaction settings, and [heartbeat.md](heartbeat.md) for heartbeat settings.

## Run

```bash
# Interactive terminal mode (no Mattermost needed)
make run

# With auto-restart on file changes (development)
make dev

# With debug logging
make debug

# With a specific model
make run-pro
```

If `MATTERMOST_URL` and `MATTERMOST_TOKEN` are set, the agent runs as a Mattermost bot. Otherwise it falls back to interactive terminal mode.

**Warning:** Only one bot instance can connect to Mattermost at a time. A second instance will silently miss websocket events.

## Test

```bash
make test     # Run pytest
make lint     # Compile-check all Python source files
```

## Additional setup

- **[Skills](skills.md)** — skills are discovered automatically from `data/{agent_id}/workspace/skills/`
- **[MCP Servers](mcp-servers.md)** — configure in `data/{agent_id}/mcp_servers.json`
- **[Heartbeat](heartbeat.md)** — set `HEARTBEAT_INTERVAL` and `HEARTBEAT_CHANNEL` or `HEARTBEAT_USER`
- **[Semantic Search](semantic-search.md)** — run `make reindex` after first setup to build the embedding index
- **Prompt customization** — place custom `SOUL.md`, `AGENT.md`, or `USER.md` in `data/{agent_id}/` to override bundled prompts
