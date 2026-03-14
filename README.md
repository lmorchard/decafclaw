# DecafClaw

A minimal AI agent in Python. Built to understand how agent frameworks
work by stripping away all the complexity.

## What it does

Connects to Mattermost, receives messages, runs an LLM with tool-calling
via LiteLLM, executes tools, and responds. That's it.

### Tools

| Tool | What it does |
|------|-------------|
| `shell` | Run a shell command |
| `read_file` | Read a local file |
| `web_fetch` | Fetch raw HTML from a URL |
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

# Or run as a Mattermost bot (set MATTERMOST_* vars in .env)
uv run decafclaw
```

## Configuration

All via environment variables (`.env` file supported):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_URL` | Yes | `http://192.168.0.199:4000/v1/chat/completions` | LLM endpoint (OpenAI-compatible) |
| `LLM_MODEL` | Yes | `gemini-2.5-flash` | Model name |
| `LLM_API_KEY` | Yes | `dummy` | API key for the LLM endpoint |
| `MATTERMOST_URL` | No | — | Mattermost server URL (enables bot mode) |
| `MATTERMOST_TOKEN` | No | — | Bot access token |
| `MATTERMOST_BOT_USERNAME` | No | — | Bot username for @mention stripping |
| `TABSTACK_API_KEY` | No | — | Tabstack API key (enables web tools) |
| `TABSTACK_API_URL` | No | SDK default | Override for dev/stage environments |

Without Mattermost configured, runs in interactive terminal mode.

## How it works

```
User message → Build prompt (system + history + tools)
                    ↓
               Call LLM
                    ↓
            ┌── Tool calls? ──→ Execute tools → Loop back to LLM
            │
            └── Text response → Send to user
```

The core loop is about 50 lines in `src/decafclaw/agent.py`. Everything
else is plumbing.

## Project structure

```
src/decafclaw/
├── __init__.py         Entry point, mode selection
├── agent.py            The agent loop (~50 lines of core logic)
├── config.py           Env var loading
├── llm.py              Raw HTTP to LLM endpoint
├── mattermost.py       WebSocket receive, REST send, placeholder UX
└── tools/
    ├── __init__.py     Tool registry
    ├── core.py         shell, read_file, web_fetch
    └── tabstack_tools.py  5 Tabstack API tools via Python SDK
```

## What this is NOT

This is not a framework. It's a learning project — built to understand
how tools like OpenClaw, nanobot, and picoclaw work under the hood.
The code is intentionally simple, with no abstractions beyond plain
functions.

## License

MIT
