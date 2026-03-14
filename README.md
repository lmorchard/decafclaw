# DecafClaw

A minimal AI agent in Python. Built to understand how agent frameworks
work by stripping away all the complexity.

## What it does

Connects to Mattermost as a chat bot, runs an LLM with tool-calling via
LiteLLM, executes tools, and responds. Features persistent memory with
semantic search, conversation archival and compaction, per-conversation
to-do lists, workspace-sandboxed file tools, shell with user confirmation,
and flood/DoS protection.

### Tools

| Tool | What it does |
|------|-------------|
| `web_fetch` | Fetch raw HTML from a URL |
| `think` | Internal reasoning scratchpad (hidden from user) |
| `debug_context` | Dump current conversation context |
| `compact_conversation` | Manually compact conversation history |
| `memory_save` | Save a persistent memory |
| `memory_search` | Search memories (semantic or substring) |
| `memory_recent` | Recall recent memories |
| `todo_add` | Add a to-do item |
| `todo_complete` | Mark a to-do item done |
| `todo_list` | Show the to-do list |
| `todo_clear` | Clear the to-do list |
| `workspace_read` | Read a file from the workspace (sandboxed) |
| `workspace_write` | Write a file to the workspace (sandboxed) |
| `workspace_list` | List files in the workspace |
| `shell` | Run a shell command (requires user confirmation) |
| `conversation_search` | Search past conversations semantically |
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
| `LOG_LEVEL` | No | `INFO` | Logging level (DEBUG, INFO, WARNING, etc.) |

### Embeddings / Semantic Search

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `EMBEDDING_MODEL` | No | `text-embedding-004` | Embedding model name |
| `EMBEDDING_URL` | No | `LLM_URL` (adjusted) | Embedding API endpoint |
| `EMBEDDING_API_KEY` | No | `LLM_API_KEY` | Embedding API key |
| `MEMORY_SEARCH_STRATEGY` | No | `substring` | `substring` or `semantic` |

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
User message → Build prompt (SOUL.md + AGENT.md + USER.md + history + tools)
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
                                     ↓
                              Archive + maybe compact
```

Key architectural pieces:
- **EventBus** (`events.py`) — in-process pub/sub, decouples tools from UI
- **Context** (`context.py`) — Go-inspired forkable runtime context
- **Async agent loop** — LLM calls, tool execution, and subscribers all async
- **Per-conversation state** — threads and channels are independent conversations
- **Memory** — file-based markdown + semantic search via embeddings
- **Conversation archive** — append-only JSONL, source of truth for compaction
- **Auto-compaction** — summarizes old history from archive when token budget exceeded
- **Conversation resume** — replays archive on restart
- **To-do lists** — markdown checkboxes on disk, per-conversation
- **Workspace sandbox** — file tools confined to `data/{agent_id}/workspace/`
- **Shell confirmation** — user must approve shell commands via reaction (Mattermost) or y/n (terminal)
- **Prompt files** — SOUL.md + AGENT.md bundled, USER.md as workspace override

## Data layout

```
data/{agent_id}/                    # Admin (read-only to agent)
├── SOUL.md                         # Identity/personality override
├── AGENT.md                        # Capability/tool guidance override
├── USER.md                         # User context override
├── COMPACTION.md                   # Compaction prompt override
└── workspace/                      # Agent read/write sandbox
    ├── memories/                   # Markdown memory files
    │   └── 2026/
    │       └── 2026-03-14.md
    ├── conversations/              # JSONL archives
    │   └── {conv_id}.jsonl
    ├── todos/                      # Per-conversation to-do lists
    │   └── {conv_id}.md
    └── embeddings.db               # Semantic search index (SQLite)
```

## Project structure

```
src/decafclaw/
├── __init__.py           Entry point, mode selection
├── agent.py              Async agent loop + interactive mode
├── archive.py            Conversation archive (JSONL)
├── compaction.py         History compaction via summarization
├── config.py             Env var loading
├── context.py            Forkable runtime context
├── embeddings.py         Semantic search index (SQLite + cosine similarity)
├── events.py             In-process pub/sub event bus
├── llm.py                Async HTTP to LLM endpoint
├── mattermost.py         WebSocket, REST, flood protection, progress, confirmation
├── memory.py             File-based memory read/write
├── todos.py              Per-conversation to-do lists
├── prompts/              System prompt assembly
│   ├── __init__.py       Prompt loader (bundled + workspace overrides)
│   ├── SOUL.md           Default identity prompt
│   └── AGENT.md          Default capability/tool prompt
├── eval/                 Eval harness
│   ├── __main__.py       CLI entry point
│   ├── runner.py         Test execution
│   └── reflect.py        Failure reflection via judge model
└── tools/
    ├── __init__.py       Tool registry (sync/async dispatch + allowed_tools)
    ├── core.py           web_fetch, debug_context, think, compact_conversation
    ├── memory_tools.py   memory_save, memory_search, memory_recent
    ├── todo_tools.py     todo_add, todo_complete, todo_list, todo_clear
    ├── workspace_tools.py workspace_read, workspace_write, workspace_list
    ├── shell_tools.py    shell (with user confirmation)
    ├── conversation_tools.py conversation_search
    └── tabstack_tools.py AsyncTabstack web tools

evals/                    Eval test cases (YAML)
scripts/                  Utility scripts
tests/                    pytest test suite (64 tests)
```

## What this is NOT

This is not a framework. It's a learning project — built to understand
how tools like OpenClaw, nanobot, and picoclaw work under the hood.
The code is intentionally simple, with minimal abstractions.

## License

MIT
