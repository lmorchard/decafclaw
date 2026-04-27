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

Legacy LLM endpoint settings. **Prefer `providers` + `model_configs` for new setups.** The `llm` section is still supported and auto-migrates to a "default" `openai-compat` provider when no `providers` section exists.

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
| `memory_sweep_enabled` | bool | `true` | `COMPACTION_MEMORY_SWEEP_ENABLED` | |
| `decisions_enabled` | bool | `true` | `COMPACTION_DECISIONS_ENABLED` | |
| `decisions_max_per_category` | int | `30` | `COMPACTION_DECISIONS_MAX_PER_CATEGORY` | |

`decisions_*` controls the structured decision slice that's threaded forward through every compaction (see [context-composer.md#decision-slice-through-compaction](context-composer.md#decision-slice-through-compaction) and #302). The slice persists at `{workspace}/conversations/{conv_id}.decisions.json`. `decisions_enabled: false` disables the prompt addendum, parse step, and sidecar write entirely. `decisions_max_per_category: 0` removes the FIFO cap.

### `notes`

Per-conversation scratchpad — always-loaded `notes_append` / `notes_read` tools backed by an append-only markdown file at `workspace/conversations/{conv_id}.notes.md`, colocated with the conversation archive and other sidecars. Recent entries auto-inject into context at turn start. See [notes.md](notes.md) and #299.

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `true` | `NOTES_ENABLED` |
| `max_entry_chars` | int | `1024` | `NOTES_MAX_ENTRY_CHARS` |
| `context_max_entries` | int | `20` | `NOTES_CONTEXT_MAX_ENTRIES` |
| `context_max_chars` | int | `4096` | `NOTES_CONTEXT_MAX_CHARS` |
| `max_total_entries` | int | `1000` | `NOTES_MAX_TOTAL_ENTRIES` |

`max_entry_chars` is the silent-truncation cap on individual notes. `context_max_entries` and `context_max_chars` together bound the auto-inject — at most N entries, dropping oldest until the total body fits the char cap. `max_total_entries` is the file-level cap: when an append would push the file over the limit, oldest entries get dropped via an atomic rewrite so long-running conversations don't accumulate unbounded read-cost per turn (the composer reads the file every interactive turn). `0` disables the file cap. `enabled: false` disables both tools and the auto-inject.

### `cleanup`

Tool-result clearing — a lightweight pre-compaction tier that replaces large old tool-message bodies with a short stub (`[tool output cleared: N chars]`) so the agent loop doesn't keep paying attention budget on raw tool output it has already synthesized. Runs every iteration (cheap, in-memory). The original tool body remains durably written to the per-conversation JSONL archive — only the in-memory copy is edited. See [context-composer.md#tool-result-clearing-lightweight-tier](context-composer.md#tool-result-clearing-lightweight-tier) and #298.

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `true` | `CLEANUP_ENABLED` |
| `min_turn_age` | int | `2` | `CLEANUP_MIN_TURN_AGE` |
| `min_size_bytes` | int | `1024` | `CLEANUP_MIN_SIZE_BYTES` |
| `preserve_tools` | list[str] | `["activate_skill", "checklist_create", "checklist_step_done", "checklist_abort", "checklist_status"]` | `CLEANUP_PRESERVE_TOOLS` |

`min_turn_age: 2` means tool messages from the current and previous user turn stay intact; older results are eligible for clearing. `min_size_bytes: 1024` is a floor — messages smaller than the stub itself wouldn't be worth clearing. `preserve_tools` is a hard allowlist for tools whose output is fundamentally load-bearing (e.g. `activate_skill` announces the tools the agent will use; `checklist_*` carries the per-conversation execution-loop state).

### `vault_retrieval`

Controls auto-retrieval injection at turn start. See [context-composer.md#memory-retrieval-modes](context-composer.md#memory-retrieval-modes) and #301.

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `true` | `VAULT_RETRIEVAL_ENABLED` |
| `similarity_threshold` | float | `0.3` | `VAULT_RETRIEVAL_SIMILARITY_THRESHOLD` |
| `max_results` | int | `5` | `VAULT_RETRIEVAL_MAX_RESULTS` |
| `max_tokens` | int | `500` | `VAULT_RETRIEVAL_MAX_TOKENS` |
| `show_in_ui` | bool | `true` | `VAULT_RETRIEVAL_SHOW_IN_UI` |
| `mode` | str | `always` | `VAULT_RETRIEVAL_MODE` |
| `headline_summary_max_chars` | int | `120` | `VAULT_RETRIEVAL_HEADLINE_SUMMARY_MAX_CHARS` |

`mode` selects retrieval strategy: `always` (full bodies, current default), `headlines` (compact `file_path · summary · score` lines for the agent to scan; pulls bodies via `vault_read` on demand), or `on_demand` (skip auto-retrieval entirely; agent drives via `vault_search` / `vault_read`). `@[[Page]]` mentions inject regardless of mode. Unknown values fall back to `always` with a warning.

### `embedding`

Semantic search embedding settings. Empty `url`/`api_key` fall back to `llm` group via `config.embedding.resolved(config)`.

| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `model` | str | `text-embedding-004` | `EMBEDDING_MODEL` | |
| `provider` | str | `""` | `EMBEDDING_PROVIDER` | |
| `url` | str | (from llm) | `EMBEDDING_URL` | |
| `api_key` | str | (from llm) | `EMBEDDING_API_KEY` | yes |
| `search_strategy` | str | `substring` | `MEMORY_SEARCH_STRATEGY` | |
| `dimensions` | int | `768` | `EMBEDDING_DIMENSIONS` | |

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

### `notifications`

Inbox settings for the notification system (see [Notifications](notifications.md)).

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `retention_days` | int | `30` | `NOTIFICATIONS_RETENTION_DAYS` |

`retention_days` controls how long inbox records stay in the live inbox
before opportunistic rotation moves them into monthly archives under
`workspace/notifications/archive/`. The web UI bell no longer polls — it
receives `notification_created` / `notification_read` pushes over the
WebSocket, so there's no poll-interval tunable.

#### `notifications.channels.mattermost_dm`

Fan-out channel that DMs matching notifications to a Mattermost user via
the already-running bot client. See
[notifications.md#channel-adapters](notifications.md#channel-adapters).

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `false` | `NOTIFICATIONS_CHANNELS_MATTERMOST_DM_ENABLED` |
| `recipient_username` | str | `""` | `NOTIFICATIONS_CHANNELS_MATTERMOST_DM_RECIPIENT_USERNAME` |
| `min_priority` | str | `high` | `NOTIFICATIONS_CHANNELS_MATTERMOST_DM_MIN_PRIORITY` |

The adapter is only wired at startup when `enabled` is `true`,
`recipient_username` is non-empty, **and** the Mattermost client is
running (`mattermost.url` + `mattermost.token` set). Any missing piece →
the adapter isn't subscribed at all; no errors at `notify()` time.
`min_priority` accepts `low` / `normal` / `high`; records below the
threshold are dropped silently.

#### `notifications.channels.email`

Fan-out channel that emails matching notifications to a fixed recipient
list via the shared SMTP core. See
[email.md](email.md#notification-channel).

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `false` | `NOTIFICATIONS_CHANNELS_EMAIL_ENABLED` |
| `recipient_addresses` | list[str] | `[]` | `NOTIFICATIONS_CHANNELS_EMAIL_RECIPIENT_ADDRESSES` |
| `min_priority` | str | `high` | `NOTIFICATIONS_CHANNELS_EMAIL_MIN_PRIORITY` |

Startup guard is 4-way: channel `enabled` **and** non-empty
`recipient_addresses` **and** `email.enabled` **and** non-empty
`email.smtp_host`. Missing any piece → adapter not wired.
`recipient_addresses` IS the trust boundary — the channel does NOT
consult `email.allowed_recipients` (that applies only to the
`send_email` agent tool).

#### `notifications.channels.vault_page`

Fan-out channel that appends each matching notification to a daily
markdown file under the configured folder in the agent's vault —
persistent local audit trail. See
[notifications.md#vault-page-adapter](notifications.md#vault-page-adapter).

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `true` | `NOTIFICATIONS_CHANNELS_VAULT_PAGE_ENABLED` |
| `min_priority` | str | `low` | `NOTIFICATIONS_CHANNELS_VAULT_PAGE_MIN_PRIORITY` |
| `folder` | str | `agent/pages/notifications` | `NOTIFICATIONS_CHANNELS_VAULT_PAGE_FOLDER` |

**Enabled by default** — the channel is purely local (no external
delivery, no credentials, no cost) and an always-on audit trail is
useful out of the box. Disable by setting `enabled: false` in
`config.json` if you don't want the vault pages.

**Startup guard is just `enabled`.** No transport dep to check — pure
local file writes. All folder validation happens at use time in the
adapter's `_daily_page_path`, which emits **one warning per bad
folder** (covers empty, absolute, `..`-containing, and outside-vault
paths) and then returns silently for the rest of the process.
Effective outcome: a misconfigured folder disables delivery without
log spam, and you see the warning exactly once.

Default `min_priority: low` means the channel captures everything;
raise the threshold if a producer gets chatty. Notification pages are
NOT added to the embedding index — they're rolling audit log, not
reference material.

### `email`

SMTP settings for the `send_email` agent tool and the email
notification channel. See [email.md](email.md).

| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `enabled` | bool | `false` | `EMAIL_ENABLED` | |
| `smtp_host` | str | `""` | `EMAIL_SMTP_HOST` | |
| `smtp_port` | int | `587` | `EMAIL_SMTP_PORT` | |
| `smtp_username` | str | `""` | `EMAIL_SMTP_USERNAME` | yes |
| `smtp_password` | str | `""` | `EMAIL_SMTP_PASSWORD` | yes |
| `use_tls` | bool | `true` | `EMAIL_USE_TLS` | |
| `sender_address` | str | `""` | `EMAIL_SENDER_ADDRESS` | |
| `allowed_recipients` | list[str] | `[]` | `EMAIL_ALLOWED_RECIPIENTS` | |
| `max_attachment_bytes` | int | `10485760` (10 MB) | `EMAIL_MAX_ATTACHMENT_BYTES` | |

Supports STARTTLS on port 587 with plain SMTP AUTH — the modern
default. Implicit TLS on port 465 and OAuth2 are out of scope; use
app-specific passwords with Gmail / M365. `allowed_recipients` accepts
exact addresses (`alice@example.com`) or `@domain.com` suffix patterns
(strict — subdomains are not matched). Entries that match bypass
confirmation for the `send_email` tool; non-matching sends require
interactive confirmation. Scheduled tasks can add per-task entries via
the `email-recipients` frontmatter field — see [schedules.md](schedules.md).

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
| `critical_tools` | list | `[]` | `CRITICAL_TOOLS` |
| `max_active_tools` | int | `30` | `MAX_ACTIVE_TOOLS` |
| `preemptive_search.enabled` | bool | `true` | *(no env var)* |
| `preemptive_search.max_matches` | int | `10` | *(no env var)* |
| `child_max_tool_iterations` | int | `10` | `CHILD_MAX_TOOL_ITERATIONS` |
| `child_timeout_sec` | int | `300` | `CHILD_TIMEOUT_SEC` |
| `max_parallel_delegates` | int | `3` | `MAX_PARALLEL_DELEGATES` |
| `max_tasks_per_delegate_call` | int | `10` | `MAX_TASKS_PER_DELEGATE_CALL` |

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
| `type` | str | all | `"vertex"`, `"openai"`, or `"openai-compat"` (alias: `"litellm"`) |
| `api_key` | str | openai, openai-compat | API key (secret) |
| `url` | str | openai, openai-compat | Base URL for the API endpoint |
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

**Migration:** If no `providers`/`model_configs` sections exist but the `llm` section is present, a "default" `openai-compat` provider and model config are auto-generated from the `llm` values.

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
