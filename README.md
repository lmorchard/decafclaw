# DecafClaw

A minimal AI agent in Python. Built to understand how agent frameworks
work by stripping away all the complexity.

## What it does

Connects to Mattermost, receives messages, runs an LLM with tool-calling
via LiteLLM, executes tools, and responds. Features an event-driven
architecture with async agent loop, live tool progress in chat, persistent
user memory, and flood/DoS protection.

### Tools

| Tool | What it does |
|------|-------------|
| `shell` | Run a shell command |
| `read_file` | Read a local file |
| `web_fetch` | Fetch raw HTML from a URL |
| `debug_context` | Dump current conversation context |
| `memory_save` | Save a memory about the user |
| `memory_search` | Search memories (substring match) |
| `memory_recent` | Recall recent memories |
| `compact_conversation` | Manually compact conversation history |
| `tabstack_extract_markdown` | Read a page or PDF as clean Markdown |
| `tabstack_extract_json` | Extract structured data with a JSON schema |
| `tabstack_generate` | Transform content with LLM instructions |
| `tabstack_automate` | Multi-step browser automation |
| `tabstack_research` | Multi-source web research with citations |

## Quick start

```bash
# Clone and install
git clone https://github.com/lmorchard/decafclaw.git
cd decafclaw
uv sync

# Configure
cp .env.example .env
# Edit .env with your LLM endpoint and optional Mattermost/Tabstack keys

# Run interactively (no Mattermost needed)
make run

# Run with auto-restart on file changes
make dev

# Or run as a Mattermost bot (set MATTERMOST_* vars in .env)
uv run decafclaw
```

## Configuration

All via environment variables (`.env` file supported):

### LLM

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_URL` | Yes | `http://192.168.0.199:4000/v1/chat/completions` | LLM endpoint (OpenAI-compatible) |
| `LLM_MODEL` | Yes | `gemini-2.5-flash` | Model name |
| `LLM_API_KEY` | Yes | `dummy` | API key for the LLM endpoint |

### Mattermost

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MATTERMOST_URL` | No | — | Server URL (enables bot mode) |
| `MATTERMOST_TOKEN` | No | — | Bot access token |
| `MATTERMOST_BOT_USERNAME` | No | — | Bot username for @mention stripping |
| `MATTERMOST_REQUIRE_MENTION` | No | `true` | Require @-mention in public/private channels |
| `MATTERMOST_IGNORE_BOTS` | No | `true` | Ignore messages from bot accounts |
| `MATTERMOST_IGNORE_WEBHOOKS` | No | `false` | Ignore messages from webhooks |
| `MATTERMOST_DEBOUNCE_MS` | No | `1000` | Batch messages within this window |
| `MATTERMOST_COOLDOWN_MS` | No | `1000` | Min time between agent turns per conversation |
| `MATTERMOST_USER_RATE_LIMIT_MS` | No | `500` | Min time between messages per user |
| `MATTERMOST_CHANNEL_BLOCKLIST` | No | — | Comma-separated channel IDs to ignore |
| `MATTERMOST_CIRCUIT_BREAKER_MAX` | No | `10` | Max turns per conversation in window |
| `MATTERMOST_CIRCUIT_BREAKER_WINDOW_SEC` | No | `30` | Circuit breaker sliding window |
| `MATTERMOST_CIRCUIT_BREAKER_PAUSE_SEC` | No | `60` | Pause duration after breaker trips |

### Tabstack

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TABSTACK_API_KEY` | No | — | Tabstack API key (enables web tools) |
| `TABSTACK_API_URL` | No | SDK default | Override for dev/stage environments |

### Agent / Workspace

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATA_HOME` | No | `./data` | Base data directory |
| `AGENT_ID` | No | `decafclaw` | Agent identity |
| `AGENT_USER_ID` | No | `user` | Configured user ID (single user for now) |

### Compaction

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `COMPACTION_LLM_URL` | No | `LLM_URL` | Compaction LLM endpoint |
| `COMPACTION_LLM_MODEL` | No | `LLM_MODEL` | Compaction model name |
| `COMPACTION_LLM_API_KEY` | No | `LLM_API_KEY` | Compaction API key |
| `COMPACTION_MAX_TOKENS` | No | `100000` | Compact when prompt_tokens exceeds this |
| `COMPACTION_LLM_MAX_TOKENS` | No | `COMPACTION_MAX_TOKENS` | Compaction LLM's context budget |
| `COMPACTION_PRESERVE_TURNS` | No | `5` | Recent turns to keep uncompacted |

Without Mattermost configured, runs in interactive terminal mode.

## Architecture

```
User message → Build prompt (system + history + tools)
                    ↓
               Call LLM (async)
                    ↓
            ┌── Tool calls? ──→ Execute tools → Publish events → Loop back
            │                         ↑
            │                    Event bus notifies subscribers
            │                    (Mattermost edits placeholder,
            │                     terminal prints progress)
            │
            └── Text response → Send to user
```

Key architectural pieces:
- **EventBus** (`events.py`) — in-process pub/sub, decouples tools from UI
- **Context** (`context.py`) — Go-inspired forkable runtime context
- **Async agent loop** — LLM calls, tool execution, and subscribers all async
- **Per-conversation state** — threads and channels are independent conversations
- **User memory** — file-based markdown memories in `data/workspace/`
- **Conversation archive** — append-only JSONL per conversation
- **Auto-compaction** — summarizes old history when token budget exceeded

## Project structure

```
src/decafclaw/
├── __init__.py         Entry point, mode selection
├── agent.py            Async agent loop + interactive mode
├── config.py           Env var loading
├── context.py          Forkable runtime context
├── events.py           In-process pub/sub event bus
├── llm.py              Async HTTP to LLM endpoint
├── mattermost.py       WebSocket, REST, flood protection, progress
├── memory.py           File-based memory read/write
├── archive.py          Conversation archive (JSONL)
├── compaction.py       History compaction via summarization
└── tools/
    ├── __init__.py     Tool registry (sync/async dispatch)
    ├── core.py         shell, read_file, web_fetch, debug_context
    ├── memory_tools.py memory_save, memory_search, memory_recent
    └── tabstack_tools.py  AsyncTabstack web tools
```

## What this is NOT

This is not a framework. It's a learning project — built to understand
how tools like OpenClaw, nanobot, and picoclaw work under the hood.
The code is intentionally simple, with minimal abstractions.

## License

MIT
