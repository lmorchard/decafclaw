# Configuration Reference

DecafClaw uses a layered configuration system. Values are resolved in this order (first wins):

1. **Environment variables** — highest priority, good for secrets and deployment overrides
2. **Config file** — `data/{agent_id}/config.json`, good for structured settings
3. **Dataclass defaults** — built-in fallbacks

The config file is optional. You can use env vars alone (via `.env` or real env), a config file alone, or both.

## Config file

Location: `data/{agent_id}/config.json` (default: `data/decafclaw/config.json`)

Only include settings you want to override — absent keys use defaults.

```json
{
  "providers": {
    "vertex": { "type": "vertex", "project": "my-project", "region": "us-central1" },
    "openai": { "type": "openai", "api_key": "sk-..." }
  },
  "model_configs": {
    "gemini-flash": { "provider": "vertex", "model": "gemini-2.5-flash" },
    "gpt-4o": { "provider": "openai", "model": "gpt-4o" }
  },
  "default_model": "gemini-flash",
  "mattermost": {
    "url": "https://comms.example.com",
    "bot_username": "decafclaw",
    "channel_blocklist": ["channel-id-1"]
  },
  "env": {
    "ANTHROPIC_API_KEY": "sk-..."
  }
}
```

## CLI tool

```bash
decafclaw config show              # all resolved values (secrets masked)
decafclaw config show llm          # filter by group
decafclaw config show --reveal     # show secret values
decafclaw config get llm.model     # single value
decafclaw config set llm.model gemini-2.5-pro   # write to config.json
decafclaw config import            # convert .env to config.json
decafclaw config import .env.prod  # from a specific file
```

`make config` is a shortcut for `decafclaw config show`.

## Config groups

### `llm` (legacy)

Legacy LLM endpoint settings. **Prefer `providers` + `model_configs` for new setups.** The `llm` section is still supported and auto-migrates to a "default" litellm provider when no `providers` section exists.

| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `url` | str | `http://192.168.0.199:4000/v1/chat/completions` | `LLM_URL` | |
| `model` | str | `gemini-2.5-flash` | `LLM_MODEL` | |
| `api_key` | str | `dummy` | `LLM_API_KEY` | yes |
| `streaming` | bool | `true` | `LLM_STREAMING` | |

### `mattermost`

Mattermost bot connection and behavior.

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
| `channel_blocklist` | list | `[]` | `MATTERMOST_CHANNEL_BLOCKLIST` | |
| `circuit_breaker_max` | int | `10` | `MATTERMOST_CIRCUIT_BREAKER_MAX` | |
| `circuit_breaker_window_sec` | int | `30` | `MATTERMOST_CIRCUIT_BREAKER_WINDOW_SEC` | |
| `circuit_breaker_pause_sec` | int | `60` | `MATTERMOST_CIRCUIT_BREAKER_PAUSE_SEC` | |
| `enable_emoji_confirms` | bool | `true` | `MATTERMOST_ENABLE_EMOJI_CONFIRMS` | |
| `stream_throttle_ms` | int | `200` | `MATTERMOST_STREAM_THROTTLE_MS` | |

List fields accept comma-separated (`a,b,c`) or JSON array (`["a","b"]`) format from env vars.

### `compaction`

History compaction settings. Empty `url`/`model`/`api_key` fall back to the `llm` group values via `config.compaction.resolved(config)`.

| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `url` | str | (from llm) | `COMPACTION_LLM_URL` | |
| `model` | str | (from llm) | `COMPACTION_LLM_MODEL` | |
| `api_key` | str | (from llm) | `COMPACTION_LLM_API_KEY` | yes |
| `max_tokens` | int | `100000` | `COMPACTION_MAX_TOKENS` | |
| `llm_max_tokens` | int | `0` | `COMPACTION_LLM_MAX_TOKENS` | |
| `preserve_turns` | int | `5` | `COMPACTION_PRESERVE_TURNS` | |

### `embedding`

Semantic search embedding settings. Empty `url`/`api_key` fall back to `llm` group via `config.embedding.resolved(config)`.

| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `model` | str | `text-embedding-004` | `EMBEDDING_MODEL` | |
| `url` | str | (from llm) | `EMBEDDING_URL` | |
| `api_key` | str | (from llm) | `EMBEDDING_API_KEY` | yes |
| `search_strategy` | str | `substring` | `MEMORY_SEARCH_STRATEGY` | |

### `reflection`

Self-reflection judge settings. Empty `url`/`model`/`api_key` fall back to the `llm` group values via `config.reflection.resolved(config)`. See [Self-Reflection](reflection.md) for full details.

| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `enabled` | bool | `true` | `REFLECTION_ENABLED` | |
| `url` | str | (from llm) | `REFLECTION_URL` | |
| `model` | str | (from llm) | `REFLECTION_MODEL` | |
| `api_key` | str | (from llm) | `REFLECTION_API_KEY` | yes |
| `max_retries` | int | `2` | `REFLECTION_MAX_RETRIES` | |
| `visibility` | str | `hidden` | `REFLECTION_VISIBILITY` | |

### `heartbeat`

Periodic wake-up settings.

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `interval` | str | `30m` | `HEARTBEAT_INTERVAL` |
| `user` | str | `""` | `HEARTBEAT_USER` |
| `channel` | str | `""` | `HEARTBEAT_CHANNEL` |
| `suppress_ok` | bool | `true` | `HEARTBEAT_SUPPRESS_OK` |

### `http`

HTTP server for interactive buttons and web UI.

| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `enabled` | bool | `false` | `HTTP_ENABLED` | |
| `host` | str | `0.0.0.0` | `HTTP_HOST` | |
| `port` | int | `18880` | `HTTP_PORT` | |
| `secret` | str | `""` | `HTTP_SECRET` | yes |
| `base_url` | str | `""` | `HTTP_BASE_URL` | |

### `agent`

Agent identity, loop limits, tool loading, and delegation.

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `data_home` | str | `./data` | `DATA_HOME` |
| `id` | str | `decafclaw` | `AGENT_ID` |
| `user_id` | str | `user` | `AGENT_USER_ID` |
| `max_tool_iterations` | int | `200` | `MAX_TOOL_ITERATIONS` |
| `max_concurrent_tools` | int | `5` | `MAX_CONCURRENT_TOOLS` |
| `max_message_length` | int | `50000` | `MAX_MESSAGE_LENGTH` |
| `tool_context_budget_pct` | float | `0.10` | `TOOL_CONTEXT_BUDGET_PCT` |
| `always_loaded_tools` | list | `[]` | `ALWAYS_LOADED_TOOLS` |
| `child_max_tool_iterations` | int | `10` | `CHILD_MAX_TOOL_ITERATIONS` |
| `child_timeout_sec` | int | `300` | `CHILD_TIMEOUT_SEC` |

`data_home` and `id` are resolved from env vars only (not from the config file) since they determine where the config file lives.

### `providers`

Named LLM provider connections. See [LLM Providers](providers.md) for full details.

```json
{
  "providers": {
    "vertex": { "type": "vertex", "project": "my-project", "region": "us-central1" },
    "openai": { "type": "openai", "api_key": "sk-..." }
  }
}
```

| Field | Type | Providers | Description |
|-------|------|-----------|-------------|
| `type` | str | all | `"vertex"`, `"openai"`, or `"litellm"` |
| `api_key` | str | openai, litellm | API key (secret) |
| `url` | str | openai, litellm | Base URL for the API endpoint |
| `project` | str | vertex | GCP project ID |
| `region` | str | vertex | GCP region (default: `us-central1`) |
| `service_account_file` | str | vertex | Path to service account JSON key file |

No env var overrides per provider — configure `api_key`, `url`, `project`, etc. in the config file. The one exception is `GOOGLE_APPLICATION_CREDENTIALS`, which is read by the Google Auth SDK for Vertex ADC auth.

### `model_configs`

Named model configurations referencing a provider. See [Model Selection](model-selection.md).

```json
{
  "model_configs": {
    "gemini-flash": { "provider": "vertex", "model": "gemini-2.5-flash" },
    "gpt-4o": { "provider": "openai", "model": "gpt-4o" }
  },
  "default_model": "gemini-flash"
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | str | required | Key into `providers` dict |
| `model` | str | required | Model name for the provider |
| `context_window_size` | int | `0` | Context window tokens (0 = use compaction_max_tokens) |
| `timeout` | int | `300` | HTTP timeout in seconds |
| `streaming` | bool | `true` | Use streaming responses |

`default_model` (top-level string) sets which model config to use when none is explicitly selected.

**Migration:** If no `providers`/`model_configs` sections exist but the `llm` section is present, a "default" litellm provider and model config are auto-generated from the `llm` values.

### `skills`

Per-skill configuration. Each skill owns its own config schema via a `SkillConfig` dataclass in its `tools.py`. The `skills` section in config.json is a freeform dict — each key is a skill name, and its value is passed to the skill's config resolver at activation time.

```json
{
  "skills": {
    "tabstack": {
      "api_key": "..."
    },
    "claude_code": {
      "model": "claude-opus-4",
      "budget_default": 5.0
    }
  }
}
```

Skill config fields support env var overrides via the skill's `SkillConfig` metadata. For example, `TABSTACK_API_KEY` overrides `skills.tabstack.api_key`. See each skill's `tools.py` for available fields.

Config CLI shows skill values as raw JSON (`config show skills`). Use `--reveal` to unmask values.

### `env`

Arbitrary environment variables set at startup. Useful for API keys and tool-specific config that doesn't have a dedicated config field.

```json
{
  "env": {
    "ANTHROPIC_API_KEY": "sk-...",
    "CUSTOM_TOOL_ENDPOINT": "https://..."
  }
}
```

- Only sets vars not already in the environment (real env vars and `.env` take priority)
- Displayed as secrets in `config show` (masked unless `--reveal`)
- `config set env.MY_VAR value` and `config get env.MY_VAR` work
- `reload_env(config)` re-reads the env section at runtime

## Migrating from .env

```bash
decafclaw config import        # reads .env, writes config.json
```

Known env var names are mapped to their config paths (e.g. `LLM_MODEL` → `llm.model`). Unknown vars are placed in the `env` section. After import, you can review with `decafclaw config show` and optionally remove the `.env` file.

## Separate config files

These files remain separate from config.json (managed by agent interactions or following external conventions):

- `data/{agent_id}/mcp_servers.json` — MCP server definitions (Claude Code compatible format)
- `data/{agent_id}/skill_permissions.json` — per-skill activation permissions
- `data/{agent_id}/shell_allow_patterns.json` — shell command allow list
