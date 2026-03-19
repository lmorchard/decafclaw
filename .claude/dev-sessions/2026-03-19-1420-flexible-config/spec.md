# Flexible Config (JSON/YAML) — Spec

## Status: Ready

## Background

The `.env` file has grown to 50+ variables with a flat namespace. Lists are comma-separated strings, related settings are scattered, and defaults are duplicated between the `Config` dataclass and `load_config()`. Structured config (MCP servers, skill permissions) already uses separate JSON files. This redesign unifies configuration into a layered system with a config file, env var overrides, and grouped sub-configs.

Closes #1.

## Goals

1. Single config file at `data/{agent_id}/config.json` with grouped, nested structure
2. Layered resolution: defaults → config file → env vars (highest priority)
3. Sub-dataclasses grouped by concern, defined in `config_types.py`
4. All fields remain overridable via env var (flat underscore names)
5. CLI tool for get/set/show with secret masking
6. Backward compatible — existing `.env` files keep working

## Non-Goals

- Migrating `mcp_servers.json`, `skill_permissions.json`, or `shell_allow_patterns.json` into the unified config (they're managed by agent interactions or follow external conventions)
- YAML support (JSON first; YAML can be added later since the loader is format-agnostic internally)

## Config File Structure

Location: `data/{agent_id}/config.json`

The file is optional. Absent keys use defaults. Only populate overrides you need.

```json
{
  "llm": {
    "url": "http://192.168.0.199:4000/v1/chat/completions",
    "model": "gemini-2.5-flash",
    "streaming": true
  },
  "mattermost": {
    "url": "https://comms.lmorchard.com",
    "bot_username": "decafclaw",
    "channel_blocklist": ["channel-id-1", "channel-id-2"],
    "require_mention": true
  },
  "agent": {
    "data_home": "./data",
    "id": "decafclaw",
    "max_tool_iterations": 200
  },
  "compaction": {
    "max_tokens": 100000
  },
  "skills": {
    "tabstack": {
      "api_key": "sk-..."
    }
  }
}
```

## Config Groups

### `llm`
| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `url` | str | `http://192.168.0.199:4000/v1/chat/completions` | `LLM_URL` | |
| `model` | str | `gemini-2.5-flash` | `LLM_MODEL` | |
| `api_key` | str | `dummy` | `LLM_API_KEY` | yes |
| `streaming` | bool | `true` | `LLM_STREAMING` | |

### `mattermost`
| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `url` | str | `""` | `MATTERMOST_URL` | |
| `token` | str | `""` | `MATTERMOST_TOKEN` | yes |
| `bot_username` | str | `""` | `MATTERMOST_BOT_USERNAME` | |
| `ignore_bots` | bool | `true` | `MATTERMOST_IGNORE_BOTS` | |
| `ignore_webhooks` | bool | `false` | `MATTERMOST_IGNORE_WEBHOOKS` | |
| `debounce_ms` | int | `1000` | `MATTERMOST_DEBOUNCE_MS` | |
| `cooldown_ms` | int | `1000` | `MATTERMOST_COOLDOWN_MS` | |
| `require_mention` | bool | `true` | `MATTERMOST_REQUIRE_MENTION` | |
| `user_rate_limit_ms` | int | `500` | `MATTERMOST_USER_RATE_LIMIT_MS` | |
| `channel_blocklist` | list[str] | `[]` | `MATTERMOST_CHANNEL_BLOCKLIST` | |
| `circuit_breaker_max` | int | `10` | `MATTERMOST_CIRCUIT_BREAKER_MAX` | |
| `circuit_breaker_window_sec` | int | `30` | `MATTERMOST_CIRCUIT_BREAKER_WINDOW_SEC` | |
| `circuit_breaker_pause_sec` | int | `60` | `MATTERMOST_CIRCUIT_BREAKER_PAUSE_SEC` | |
| `enable_emoji_confirms` | bool | `true` | `MATTERMOST_ENABLE_EMOJI_CONFIRMS` | |
| `stream_throttle_ms` | int | `200` | `MATTERMOST_STREAM_THROTTLE_MS` | |

Note: `stream_throttle_ms` moves from `llm_stream_throttle_ms` to mattermost (it only controls Mattermost placeholder update frequency). `enable_emoji_confirms` default changes — currently auto-set to `not http_enabled` at load time; new behavior: default `true`, override explicitly if needed.

### `compaction`
| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `url` | str | (from llm) | `COMPACTION_LLM_URL` | |
| `model` | str | (from llm) | `COMPACTION_LLM_MODEL` | |
| `api_key` | str | (from llm) | `COMPACTION_LLM_API_KEY` | yes |
| `max_tokens` | int | `100000` | `COMPACTION_MAX_TOKENS` | |
| `llm_max_tokens` | int | `0` | `COMPACTION_LLM_MAX_TOKENS` | |
| `preserve_turns` | int | `5` | `COMPACTION_PRESERVE_TURNS` | |

Omitted fields resolve from `llm` group at load time. `0` for `llm_max_tokens` means "use `max_tokens`".

### `embedding`
| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `model` | str | `text-embedding-004` | `EMBEDDING_MODEL` | |
| `url` | str | (from llm) | `EMBEDDING_URL` | |
| `api_key` | str | (from llm) | `EMBEDDING_API_KEY` | yes |
| `search_strategy` | str | `substring` | `MEMORY_SEARCH_STRATEGY` | |

Omitted `url`/`api_key` resolve from `llm` group at load time (url with `/chat/completions` → `/embeddings` substitution).

### `heartbeat`
| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `interval` | str | `30m` | `HEARTBEAT_INTERVAL` | |
| `user` | str | `""` | `HEARTBEAT_USER` | |
| `channel` | str | `""` | `HEARTBEAT_CHANNEL` | |
| `suppress_ok` | bool | `true` | `HEARTBEAT_SUPPRESS_OK` | |

### `http`
| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `enabled` | bool | `false` | `HTTP_ENABLED` | |
| `host` | str | `0.0.0.0` | `HTTP_HOST` | |
| `port` | int | `18880` | `HTTP_PORT` | |
| `secret` | str | `""` | `HTTP_SECRET` | yes |
| `base_url` | str | `""` | `HTTP_BASE_URL` | |

### `agent`
| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `data_home` | str | `./data` | `DATA_HOME` | |
| `id` | str | `decafclaw` | `AGENT_ID` | |
| `user_id` | str | `user` | `AGENT_USER_ID` | |
| `max_tool_iterations` | int | `200` | `MAX_TOOL_ITERATIONS` | |
| `max_concurrent_tools` | int | `5` | `MAX_CONCURRENT_TOOLS` | |
| `max_message_length` | int | `50000` | `MAX_MESSAGE_LENGTH` | |
| `tool_context_budget_pct` | float | `0.10` | `TOOL_CONTEXT_BUDGET_PCT` | |
| `always_loaded_tools` | list[str] | `[]` | `ALWAYS_LOADED_TOOLS` | |
| `child_max_tool_iterations` | int | `10` | `CHILD_MAX_TOOL_ITERATIONS` | |
| `child_timeout_sec` | int | `300` | `CHILD_TIMEOUT_SEC` | |

### `skills`
Container for per-skill config. Each skill gets a sub-key.

#### `skills.tabstack`
| Field | Type | Default | Env Var | Alias | Secret |
|-------|------|---------|---------|-------|--------|
| `api_key` | str | `""` | `SKILLS_TABSTACK_API_KEY` | `TABSTACK_API_KEY` | yes |
| `api_url` | str | `""` | `SKILLS_TABSTACK_API_URL` | `TABSTACK_API_URL` | |

#### `skills.claude_code`
| Field | Type | Default | Env Var | Alias | Secret |
|-------|------|---------|---------|-------|--------|
| `model` | str | `""` | `SKILLS_CLAUDE_CODE_MODEL` | `CLAUDE_CODE_MODEL` | |
| `budget_default` | float | `2.0` | `SKILLS_CLAUDE_CODE_BUDGET_DEFAULT` | `CLAUDE_CODE_BUDGET_DEFAULT` | |
| `budget_max` | float | `10.0` | `SKILLS_CLAUDE_CODE_BUDGET_MAX` | `CLAUDE_CODE_BUDGET_MAX` | |
| `session_timeout` | str | `30m` | `SKILLS_CLAUDE_CODE_SESSION_TIMEOUT` | `CLAUDE_CODE_SESSION_TIMEOUT` | |

## Field Metadata

Dataclass fields use `field(metadata={...})` for loader hints:

- `secret: True` — masked in `config show` output unless `--reveal`
- `env_alias: "NAME"` — alternative env var name (checked after the systematic name)

```python
api_key: str = field(default="", metadata={"secret": True, "env_alias": "TABSTACK_API_KEY"})
```

## Resolution Order

### Bootstrap phase

`agent.data_home` and `agent.id` are resolved first (env vars → defaults only) to locate the config file at `{data_home}/{id}/config.json`. These two fields are NOT read from the config file — they determine where the file lives.

### Main resolution

For each remaining field, the loader checks (first non-empty wins):

1. **Env var** (systematic name, then alias if defined)
2. **Config file** value at the corresponding JSON path
3. **Dataclass default**

### List fields via env var

`list[str]` fields accept either format from env vars:
- Comma-separated: `MATTERMOST_CHANNEL_BLOCKLIST=id1,id2`
- JSON array: `MATTERMOST_CHANNEL_BLOCKLIST=["id1","id2"]`

The loader tries JSON parse first; if that fails, splits on commas. This preserves backward compat with existing comma-separated values.

### Fallback resolution

After all fields are populated:
- `compaction.url` empty → copy from `llm.url`
- `compaction.model` empty → copy from `llm.model`
- `compaction.api_key` empty → copy from `llm.api_key`
- `embedding.url` empty → derive from `llm.url` (replace `/chat/completions` with `/embeddings`)
- `embedding.api_key` empty → copy from `llm.api_key`

## Derived Properties

These stay as `@property` on the top-level `Config`:
- `agent_path` → `Path(agent.data_home) / agent.id`
- `workspace_path` → `agent_path / "workspace"`
- `http_callback_base` → `http.base_url` or `http://{http.host}:{http.port}`
- `tool_context_budget` → `int(compaction.max_tokens * agent.tool_context_budget_pct)`
- `compaction_context_budget` → `compaction.llm_max_tokens or compaction.max_tokens`

## Top-Level Config Dataclass

```python
@dataclass
class Config:
    llm: LlmConfig = field(default_factory=LlmConfig)
    mattermost: MattermostConfig = field(default_factory=MattermostConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)

    # Runtime-only (not in config file)
    system_prompt: str = ""
    discovered_skills: list = field(default_factory=list)

    @property
    def agent_path(self) -> Path: ...
    @property
    def workspace_path(self) -> Path: ...
```

## CLI Tool

Invoked as `python -m decafclaw config <command>`.

### `config show [group]`
Show resolved config (all sources merged). Secrets masked as `****` unless `--reveal`.

```
$ python -m decafclaw config show
llm.url = http://192.168.0.199:4000/v1/chat/completions
llm.model = gemini-2.5-flash
llm.api_key = ****
llm.streaming = true
mattermost.url = https://comms.lmorchard.com
mattermost.token = ****
...

$ python -m decafclaw config show mattermost
mattermost.url = https://comms.lmorchard.com
mattermost.token = ****
mattermost.bot_username = decafclaw
...

$ python -m decafclaw config show --reveal
llm.api_key = dummy
mattermost.token = xoxb-actual-token
```

### `config get <path>`
Get a single resolved value.

```
$ python -m decafclaw config get mattermost.url
https://comms.lmorchard.com
```

### `config set <path> <value>`
Write a value to `config.json`. Creates the file and parent keys if needed.

```
$ python -m decafclaw config set mattermost.require_mention false
$ python -m decafclaw config set mattermost.channel_blocklist '["id1","id2"]'
```

Type coercion based on the dataclass field type (bool, int, float, list, str).

## Migration Path

1. Existing `.env` files keep working — env vars are highest priority
2. All internal code updated to nested access (`ctx.config.mattermost.url` instead of `ctx.config.mattermost_url`) in a single pass — this is a codebase-internal breaking change but there are no external consumers
3. `.env.example` updated to document the config file as the preferred approach
4. `dataclasses.replace()` with nested configs: to override a sub-config field, build the sub-config first: `dataclasses.replace(config, mattermost=dataclasses.replace(config.mattermost, url="..."))`. This is more verbose but explicit. Consider a helper if the pattern appears often in tests.

## Files Changed

- **New**: `src/decafclaw/config_types.py` — all sub-dataclasses
- **Rewrite**: `src/decafclaw/config.py` — loader (JSON parse + env overlay + fallbacks), CLI entry point
- **Update**: every module that reads `ctx.config.*` — update to nested access
- **Update**: `tests/conftest.py` and test files — update Config construction
- **Update**: `.env.example` — add config file documentation
- **New**: `data/decafclaw/config.json.example` — example config file
