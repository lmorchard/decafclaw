# Configuration Reference

DecafClaw uses a layered configuration system. Values are resolved in this order (first wins):

1. **Environment variables** — highest priority, good for secrets and deployment overrides
2. **Config file** — `data/{agent_id}/config.json`, good for structured settings
3. **Dataclass defaults** — built-in fallbacks

The config file is optional. You can use env vars alone (via `.env` or real env), a config file alone, or both.

## Config file

Location: `data/{agent_id}/config.json` (default: `data/decafclaw/config.json`)

Only include settings you want to override — absent keys use defaults.

`load_config()` first imports the nearest `.env` (python-dotenv, searching
up from the package) without overriding variables that are already set.
Set `DECACLAW_LOAD_DOTENV=0` to skip that import entirely — the ephemeral
test instance ([ephemeral-instance.md](ephemeral-instance.md)) uses this so
a child process started from a checkout never imports the checkout's `.env`
(real credentials, real `DATA_HOME`).

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
  "auxiliary_model": "gemini-flash-8b",
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
decafclaw config show providers    # dict groups (providers, model_configs) print per-entry fields
decafclaw config show default_model  # filter by a top-level scalar field name
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

Mattermost bot connection and behavior. Set `enabled` to `false` (e.g.
`MATTERMOST_ENABLED=false`) to run the web gateway standalone without connecting
to Mattermost — useful for local web-only runs and the
[decafclaw client](client.md) without stealing a deployed bot's websocket.

| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `enabled` | bool | `true` | `MATTERMOST_ENABLED` | |
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

History compaction settings. Note: `url`, `model`, and `api_key` are deprecated in favor of setting `auxiliary_model` (which uses the modern `providers` / `model_configs` system).

| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `url` | str | (from llm) | `COMPACTION_LLM_URL` | |
| `model` | str | (from llm) | `COMPACTION_LLM_MODEL` | |
| `api_key` | str | (from llm) | `COMPACTION_LLM_API_KEY` | yes |
| `max_tokens` | int | `100000` | `COMPACTION_MAX_TOKENS` | |
| `llm_max_tokens` | int | `0` | `COMPACTION_LLM_MAX_TOKENS` | |
| `threshold_ratio` | float | `None` | `COMPACTION_THRESHOLD_RATIO` | |
| `preserve_turns` | int | `5` | `COMPACTION_PRESERVE_TURNS` | |
| `memory_sweep_enabled` | bool | `true` | `COMPACTION_MEMORY_SWEEP_ENABLED` | |
| `decisions_enabled` | bool | `true` | `COMPACTION_DECISIONS_ENABLED` | |
| `decisions_max_per_category` | int | `30` | `COMPACTION_DECISIONS_MAX_PER_CATEGORY` | |

`decisions_*` controls the structured decision slice that's threaded forward through every compaction (see [context-composer.md#decision-slice-through-compaction](context-composer.md#decision-slice-through-compaction) and #302). The slice persists at `{workspace}/conversations/{conv_id}/decisions.json`. `decisions_enabled: false` disables the prompt addendum, parse step, and sidecar write entirely. `decisions_max_per_category: 0` removes the FIFO cap.

`threshold_ratio` (e.g. `0.4`) is the compaction trigger as a fraction of the model's context window: when set and the model window is known, the effective threshold is `int(context_window_size * ratio)` instead of the fixed `max_tokens` cap; when unset (`None`) the fixed `max_tokens` default applies. Per-conversation overrides (`compaction_threshold` / `compaction_threshold_ratio`) ride the same resolution through conversation state and the PATCH API (#1099).

### `notes`

Per-conversation scratchpad — always-loaded `notes_append` / `notes_read` tools backed by an append-only markdown file at `workspace/conversations/{conv_id}/notes.md`, colocated with the conversation archive and other sidecars. Recent entries auto-inject into context at turn start. See [notes.md](notes.md) and #299.

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `true` | `NOTES_ENABLED` |
| `max_entry_chars` | int | `1024` | `NOTES_MAX_ENTRY_CHARS` |
| `context_max_entries` | int | `20` | `NOTES_CONTEXT_MAX_ENTRIES` |
| `context_max_chars` | int | `4096` | `NOTES_CONTEXT_MAX_CHARS` |
| `max_total_entries` | int | `1000` | `NOTES_MAX_TOTAL_ENTRIES` |

`max_entry_chars` is the silent-truncation cap on individual notes. `context_max_entries` and `context_max_chars` together bound the auto-inject — at most N entries, dropping oldest until the total body fits the char cap. `max_total_entries` is the file-level cap: when an append would push the file over the limit, oldest entries get dropped via an atomic rewrite so long-running conversations don't accumulate unbounded read-cost per turn (the composer reads the file every interactive turn). `0` disables the file cap. `enabled: false` disables both tools and the auto-inject.

### `recent_journal`

Auto-surface recently written journal entries at interactive turn start on a small fixed budget, separate from the dynamic semantic-retrieval pool so it doesn't compete with it. Recency is a strong signal as a retrieval *mode* — surfacing "what was I just thinking about?" — where it's weak folded into a composite similarity score. See [context-composer.md#recent-journal-surfacing](context-composer.md#recent-journal-surfacing) and #306.

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `true` | `RECENT_JOURNAL_ENABLED` |
| `max_hours` | int | `24` | `RECENT_JOURNAL_MAX_HOURS` |
| `max_entries` | int | `5` | `RECENT_JOURNAL_MAX_ENTRIES` |
| `max_tokens` | int | `1024` | `RECENT_JOURNAL_MAX_TOKENS` |

`max_hours` and `max_entries` bound the window — journal entries written within the last N hours, capped at the most recent K, whichever is tighter. Only entries newer than the newest one already surfaced this conversation are injected (a high-water mark on `ComposerState`), so each entry appears at most once and a long conversation doesn't keep re-surfacing the same block. `max_tokens` is a soft cap on the injected body: if the window overflows it, the oldest entries are dropped first (the newest is always kept). Skipped for heartbeat / scheduled / child-agent modes. `enabled: false` disables the auto-inject. Overlaps intentionally with the `dream` skill (which distills journal → curated pages over time): dream is long-term consolidation, this is short-term working memory.

### `cleanup`

Tool-result clearing — a lightweight pre-compaction tier with three in-memory passes. The **size/age clear** replaces large old tool-message bodies with a short stub (`[tool output cleared: N chars]`) so the agent loop doesn't keep paying attention budget on raw tool output it has already synthesized. The **resolved-error pruning** pass (#540) stubs a tool error a later same-tool success has resolved (`[tool error superseded by later success]`). The **superseded-read pruning** pass (#1146) stubs a successful full-file read whose path a later successful full read re-read (`[tool output superseded by a later successful read]`); all three passes run every iteration (cheap, in-memory). The original tool body remains durably written to the per-conversation JSONL archive — only the in-memory copy is edited. See [context-composer.md#tool-result-clearing-lightweight-tier](context-composer.md#tool-result-clearing-lightweight-tier) and #298.

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `true` | `CLEANUP_ENABLED` |
| `min_turn_age` | int | `2` | `CLEANUP_MIN_TURN_AGE` |
| `min_size_bytes` | int | `1024` | `CLEANUP_MIN_SIZE_BYTES` |
| `preserve_tools` | list[str] | `["activate_skill", "checklist_create", "checklist_step_done", "checklist_abort", "checklist_status"]` | `CLEANUP_PRESERVE_TOOLS` |
| `prune_resolved_errors` | bool | `true` | `CLEANUP_PRUNE_RESOLVED_ERRORS` |
| `prune_resolved_same_args_only` | bool | `false` | `CLEANUP_PRUNE_RESOLVED_SAME_ARGS_ONLY` |
| `prune_superseded_reads` | bool | `true` | `CLEANUP_PRUNE_SUPERSEDED_READS` |

`min_turn_age: 2` means tool messages from the current and previous user turn stay intact; older results are eligible for clearing. `min_size_bytes: 1024` is a floor — messages smaller than the stub itself wouldn't be worth clearing. `preserve_tools` is a hard allowlist for tools whose output is fundamentally load-bearing (e.g. `activate_skill` announces the tools the agent will use; `checklist_*` carries the per-conversation execution-loop state). `prune_resolved_errors: true` enables the resolved-error pruning pass (disabled = errors are left to the size/age clear alone); `prune_resolved_same_args_only: true` makes that pass conservative, pruning only when the resolving success used the same arguments as the failed call (default `false` = match on tool name alone). `prune_superseded_reads: true` enables the superseded-read pruning pass (default `true`), which stubs an older successful full-file read once a later successful full read of the same path supersedes it.

### `vault`

Vault location and write-gate settings. See [vault.md](vault.md).

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `vault_path` | str | `workspace/vault/` | `VAULT_VAULT_PATH` |
| `agent_folder` | str | `agent/` | `VAULT_AGENT_FOLDER` |
| `recent_changes_limit` | int | `50` | `VAULT_RECENT_CHANGES_LIMIT` |
| `user_writable_paths` | list[str] | `[]` | `VAULT_USER_WRITABLE_PATHS` |

`agent_folder` is resolved relative to `vault_path`. Set `vault_path` to an absolute path to point the agent at an existing Obsidian vault.

`user_writable_paths` lists vault-relative folder paths that bypass the user-page write confirmation. Prefix match (no globs); leading `/` is stripped and a trailing `/` is enforced, so `creative` and `creative/` are equivalent. Entries containing `..` are skipped with a warning. Empty by default — opt-in. Example: `["creative/", "notes/"]`.

### `importance`

Weights for garden's deterministic weekly importance recompute (#197 Phase 5). See [Importance recompute](vault.md#importance-recompute-197).

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `w_retrieval` | float | `0.6` | `IMPORTANCE_W_RETRIEVAL` |
| `w_inbound` | float | `0.4` | `IMPORTANCE_W_INBOUND` |
| `w_reference` | float | `0.0` | `IMPORTANCE_W_REFERENCE` |

`w_reference` is reserved for a future explicit-reference signal (not yet implemented) and contributes nothing to the score while it's 0.

### `vault_retrieval`

Controls auto-retrieval injection at turn start. See [context-composer.md#memory-retrieval-modes](context-composer.md#memory-retrieval-modes) and #301.

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `true` | `VAULT_RETRIEVAL_ENABLED` |
| `similarity_threshold` | float | `0.3` | `VAULT_RETRIEVAL_SIMILARITY_THRESHOLD` |
| `max_results` | int | `5` | `VAULT_RETRIEVAL_MAX_RESULTS` |
| `max_tokens` | int | `4000` | `VAULT_RETRIEVAL_MAX_TOKENS` |
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

Self-reflection judge settings. Empty `url`/`model`/`api_key` fall back to the `llm` group values via `config.reflection.resolved(config)`. `verifier_model` is a [`model_configs`](#model_configs) key; when it is set **and** names an entry in `model_configs` it outranks every other judge-model setting, and otherwise it is ignored and resolution continues down the chain — see [Judge model](reflection.md#judge-model) for the full order. See [Self-Reflection](reflection.md) for full details.

| Field | Type | Default | Env Var | Secret |
|-------|------|---------|---------|--------|
| `enabled` | bool | `true` | `REFLECTION_ENABLED` | |
| `url` | str | (from llm) | `REFLECTION_URL` | |
| `model` | str | (from llm) | `REFLECTION_MODEL` | |
| `verifier_model` | str | `""` | `REFLECTION_VERIFIER_MODEL` | |
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

### `schedules`

Global switch for the [scheduled-task](schedules.md) timer (#957). When
`enabled` is `false`, the schedule timer does not start at all — no
schedule file is scanned or run, including bundled ones (`dream`,
`garden`). Per-task `enabled` in SCHEDULE.md frontmatter still applies when
the global switch is on.

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `true` | `SCHEDULES_ENABLED` |

### `pre_script`

Pre-agent scripts for scheduled tasks (#450). A schedule declaring `pre_script:` runs it with the project interpreter before the turn and injects stdout into the prompt, so mechanical work costs no LLM round-trips. See [schedules.md#pre-agent-scripts](schedules.md#pre-agent-scripts).

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `true` | `PRE_SCRIPT_ENABLED` |
| `timeout_sec` | float | `60.0` | `PRE_SCRIPT_TIMEOUT_SEC` |

`timeout_sec` is deliberately **not** charged against `agent.max_tool_iterations` — the script runs before any reasoning, so a slow fetch must not shrink the budget it is gathering data for. Setting `enabled = false` makes every task's `pre_script` a no-op without editing schedule files.

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

### `shell`

Settings for shell command execution and pre-approval logic (see #473).

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `aux_approval_enabled` | bool | `false` | `SHELL_AUX_APPROVAL_ENABLED` |
| `aux_approval_model` | str | `""` | `SHELL_AUX_APPROVAL_MODEL` |
| `aux_approval_guidance` | str | `""` | `SHELL_AUX_APPROVAL_GUIDANCE` |
| `aux_approval_presets` | dict | `{}` | `SHELL_AUX_APPROVAL_PRESETS` |
| `active_aux_approval_presets` | list[str] | `[]` | `SHELL_ACTIVE_AUX_APPROVAL_PRESETS` |
| `foreground_wait_budget_sec` | float \| None | `None` | `SHELL_FOREGROUND_WAIT_BUDGET_SEC` |
| `max_runtime_sec` | float | `30.0` | `SHELL_MAX_RUNTIME_SEC` |

When `aux_approval_enabled` is `true`, `check_shell_approval()` invokes the auxiliary LLM to screen unrecognized commands. If the aux LLM deems the command low-risk (e.g. `ls -la`, `git status`), it is auto-approved without prompting the user. If the aux LLM deems it risky or errors out, it falls through to standard interactive confirmation. Auto-approved patterns are temporarily cached per-conversation so repeated benign commands don't trigger redundant LLM calls.

#### Foreground time budget (#1136)

`foreground_wait_budget_sec` is opt-in (`None` = today's blocking behavior). When set (> 0), `shell` starts the command through the background skill's shared runner and awaits up to the budget: a command that finishes in time returns an ordinary result, while one that exceeds it hands off — the **same process** continues as a managed background job and the tool returns its handle (job ID, PID). `max_runtime_sec` is the command's total runtime limit: it bounds the legacy blocking path and becomes a handed-off job's `max_lifetime` (enforced by the existing background-job expiry policy). The explicit-background default lifetime (`shell_background_start`, 600s) is a separate knob and unchanged. See [Tools — Shell](tools.md#auto-backgrounding-on-a-foreground-time-budget-1136).

#### Dedicated auto-approval judge model

`shell.aux_approval_model` (env `SHELL_AUX_APPROVAL_MODEL`) names a dedicated model for the auto-approval judge; empty (`""`) keeps the historical behavior of riding on the legacy aux chain (`auxiliary_model` → active conversation model → default). When set, it must name a `model_configs` key (enabled discovered models materialize as keys); a hand-written raw name is not routable — the shell group has no legacy endpoint of its own — and is treated as unset with a warning. Resolution: the `data/{agent_id}/model_catalog.json` sidecar's `aux_approval_model` pointer → `shell.aux_approval_model` → `auxiliary_model` → the active conversation model. The sidecar pointer is the UI-managed path (the **Auto-approval model** picker in **⚙ Agent Config → Models**); an empty pointer or empty seed means "ride on the aux chain". See [Model selection](model-selection.md#shell-auto-approval-model).

#### Guidance and Situational Presets

- **`aux_approval_guidance`**: Free-form guidance string (or path to a guidance text file relative to the agent directory). Workspace files are not resolved because the workspace is agent-writable. Appended to the aux evaluator prompt to permit specific categories of commands (e.g. `Auto-approve pytest and ruff invocations inside the workspace.`).
- **`aux_approval_presets`**: Custom named presets mapping preset name to guidance text. Built-in presets:
  - `developer`: Auto-approves test runners (`pytest`, `npm test`, `vitest`), linters/formatters (`ruff`, `prettier`, `eslint`), build commands, and non-destructive local git operations (`git add`, `git commit`, `git status`, `git diff`, `git branch`, `git checkout`, `git switch`) within workspace repositories. (Note that `git push` is always intercepted for confirmation by the security monitor).
  - `github`: Auto-approves GitHub CLI (`gh`) commands for issue/PR inspection and safe workflow management (`gh issue`, `gh pr`, `gh run`).
- **`active_aux_approval_presets`**: List of preset names activated by default. In addition, presets and guidance can be activated conversationally via session `ToolState`.

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
| `tool_timeout_sec` | int | `180` | `TOOL_TIMEOUT_SEC` |
| `confirmation_timeout_sec` | int \| None | `null` | `CONFIRMATION_TIMEOUT_SEC` |
| `max_parallel_delegates` | int | `3` | `MAX_PARALLEL_DELEGATES` |
| `max_tasks_per_delegate_call` | int | `10` | `MAX_TASKS_PER_DELEGATE_CALL` |

`confirmation_timeout_sec` sets the wall-clock timeout in seconds for user confirmation prompts. Set to `null` (default) or `<= 0` to disable the timeout completely so the agent waits indefinitely for approval.

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
| `type` | str | all | `"vertex"`, `"openai-responses"`, `"openai"`, or `"openai-compat"` (alias: `"litellm"`) |
| `api_key` | str | openai-responses, openai, openai-compat | API key (secret) |
| `url` | str | openai-responses, openai, openai-compat | Base URL or API endpoint |
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
  "default_model": "gemini-flash",
  "auxiliary_model": "gemini-flash-8b"
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | str | required | Key into `providers` dict |
| `model` | str | required | Model name for the provider |
| `context_window_size` | int | `0` | Context window tokens (0 = use compaction_max_tokens) |
| `timeout` | int | `300` | HTTP timeout in seconds |
| `streaming` | bool | `true` | Use streaming responses |
| `reasoning_effort` | str or null | `null` | OpenAI Responses only: reasoning effort; omitted/null uses model default |

`default_model` (top-level string) sets which model config to use when none is explicitly selected.

`auxiliary_model` (top-level string) sets the model config to use for background operations like compaction or scoring, where speed or cost might be prioritized over reasoning capability. If unset, these operations fall back to the active conversation model or `default_model`.

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

### `telemetry`

Instrumentation sidecars — append-only JSONL under `workspace/`, metadata only (never tool args/returns, reflection bodies, or prompt contents). Producers are fail-open EventBus subscribers. See [tools.md#tool-usage-telemetry-310](tools.md#tool-usage-telemetry-310) (#310), [reflection.md#metrics-409](reflection.md#metrics-409) (#409), and [context-composer.md#retrieval-telemetry-197](context-composer.md#retrieval-telemetry-197) (#197). `metrics_enabled` gates the Prometheus subscriber, which writes no sidecar — see [metrics.md](metrics.md) (#10).

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `tool_usage_enabled` | bool | `true` | `TELEMETRY_TOOL_USAGE_ENABLED` |
| `tool_usage_path` | str | `tool_usage.jsonl` | `TELEMETRY_TOOL_USAGE_PATH` |
| `reflection_metrics_enabled` | bool | `true` | `TELEMETRY_REFLECTION_METRICS_ENABLED` |
| `reflection_metrics_path` | str | `reflection/metrics.jsonl` | `TELEMETRY_REFLECTION_METRICS_PATH` |
| `retrieval_enabled` | bool | `true` | `TELEMETRY_RETRIEVAL_ENABLED` |
| `retrieval_path` | str | `telemetry/retrieval.jsonl` | `TELEMETRY_RETRIEVAL_PATH` |
| `loop_breaker_enabled` | bool | `true` | `TELEMETRY_LOOP_BREAKER_ENABLED` |
| `loop_breaker_path` | str | `telemetry/loop_breaker.jsonl` | `TELEMETRY_LOOP_BREAKER_PATH` |
| `metrics_enabled` | bool | `true` | `TELEMETRY_METRICS_ENABLED` |
| `otlp_endpoint` | str \| None | `null` | `TELEMETRY_OTLP_ENDPOINT` |
| `otlp_service_name` | str | `"decafclaw"` | `TELEMETRY_OTLP_SERVICE_NAME` |

Paths are workspace-relative. Enabled by default so a deployed agent starts collecting without a config edit — the intent is a week of real data. No rotation yet (append-only); retention is a follow-up. Reports: `make tool-usage-report`, `make reflection-stats`, `make retrieval-report`.

For OpenTelemetry configuration, see [Observability](observability.md).

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
- Config hot-reload (#149) re-applies an updated env section via
  `Config.apply_env_update(previous_env)`, which updates/unsets only
  variables the config owns (the live value still matches what the previous
  section set; real env vars and `.env` keep winning)

## Config hot-reload (#149)

While the server process is running, the agent watches `data/{agent_id}/config.json`
(and `mcp_servers.json`) and reacts to changes:

- **Require a process restart**: `http`, `mattermost`, `telemetry`, `schedules`,
  and any change to `mcp_servers.json` (MCP servers load once at startup,
  outside the `Config` dataclass). The watcher publishes a `config_changed`
  event with `requires_restart: true` and stops the process after a short
  grace delay. Under `make dev`, the dev supervisor watches the agent data
  dir too and restarts the process automatically; under a bare `make run`
  the process exits and you restart it by hand. `telemetry` and `schedules`
  wire event subscribers and timer spawning once in `runner.run_all` and
  cannot be rewired mid-run.
- **Safe to apply in place** (no restart): every other section. Changed
  sections are applied onto the live config and announced with a
  `config_changed` event (`requires_restart: false`). Sections take effect
  where their values are read at use time — which for most is every agent
  turn. A few get an explicit re-application so the claim is honest:
  - `env`: keys are updated/unset in the environment, but only where the
    config owns the variable (see `apply_env_update` above).
  - `providers`: the LLM provider registry is rebuilt (`init_providers`).
  - `extra_skill_paths` / `skills_always_loaded`: the skill catalog is
    re-scanned and the system prompt rebuilt (`rediscover_skills`).
  - `heartbeat.interval`: re-read by the heartbeat timer each poll.
- **Applied in place, best-effort**: a few safe sections are consumed only
  at startup, so the updated value waits for the next restart — e.g.
  `agent.data_home` / `agent.id`, which define where the config itself
  lives. The event still announces them.
- A file that changed but does not parse as JSON (a torn or partial write) is
  skipped until it parses again — `load_config` falls back to defaults on
  malformed JSON, and those defaults must never reach the live config.
- Every handled change publishes a `config_changed` event of the shape
  `{"type": "config_changed", "requires_restart": <bool>, "keys": [<section>, ...]}`.

The classification lives in `src/decafclaw/config_watcher.py`; the `make dev`
filter shares it via `src/decafclaw/_dev_filter.py`. Both resolve the agent
data dir from `DATA_HOME`/`AGENT_ID` (which `.env` may point outside the
checkout), so worktrees and relocated data homes are watched correctly.

## File-operation ledger through compaction (#924)

Compaction's prose summary is lossy for concrete file operations, and the
artifact list was LLM-supplied. The agent now derives a deterministic
file-operation ledger from the built-in workspace file-tool calls and carries
it through compaction alongside the structured decision slice:

- Operations are recorded as ``"<operation> <path> :: call <id> :: ok|failed"``
  with the canonical workspace-relative path, operation, tool-call identity,
  and outcome.
- Successful reads/writes/edits survive at least two compactions with the
  path, call identity, and outcome intact even when prose summaries omit
  them; failed writes are never represented as successful modifications.
- Repeated compaction does not duplicate the same operation record (merge
  dedups by operation string and FIFO-caps at the configured per-category
  limit).
- Existing decisions/open questions/artifacts remain intact and compatible
  with older conversations lacking a ledger.

The ledger lives in the per-conversation `decisions.json` sidecar next to the
decision slice and renders as a `### File Operations` section inside the
`<decision_slice>` block.

## Secrets and secret references (#1032)

Secret values (LLM provider API keys, Mattermost bot tokens, email SMTP
credentials, the HTTP callback secret, …) are **not** stored in
`data/{agent_id}/config.json`. The file holds `${VAR_NAME}` references; the
values live in an external `.env` store outside the agent dir and are resolved
at config-load time by `decafclaw.secrets` (default driver: environment
variables / the external `.env`).

- Literal secret values in `config.json` are still accepted during the
  transition and log a deprecation warning at load.
- `decafclaw config show` / `make config` display `${VAR_NAME}` references as
  plain text and mask resolved secret values unless `--reveal`.
- Admin tools redact secret fields via the `secret` field annotation and refuse
  agent access to the external secret store.

### Migrating literals to references

```bash
make migrate-secrets        # extract literal secrets into the external .env and rewrite config.json to ${VAR} refs
make migrate-secrets-dry    # preview only
```

The migration is idempotent and safe to re-run; secrets already holding
`${VAR_NAME}` refs, empty values, and vars already staged in the external store
are skipped.

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
