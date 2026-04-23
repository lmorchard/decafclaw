# Notifications

DecafClaw maintains an **inbox** of noteworthy agent-initiated events so you
don't have to babysit the bot. Heartbeats, scheduled tasks, background
processes, compaction, and reflection rejections all append a record. You see
them in the web UI as a bell-icon badge in the sidebar footer, and click
through to the associated conversation or vault page.

This page covers the Phase 1 design: the inbox primitive + the web UI bell.
Phase 2+ will add delivery channels (Mattermost DM, email, vault summary
pages). See the [issue #292](https://github.com/lmorchard/decafclaw/issues/292)
tracker for deferred work.

## What the bell does

The web UI sidebar footer renders a `<notification-inbox>` component next to
the config gear. It polls `GET /api/notifications/unread-count` every 30
seconds (config plumbing for `notifications.poll_interval_sec` is deferred).
When the count is non-zero it renders a small red badge.

Clicking the bell opens a dropdown panel with the 20 most recent records,
newest first. Each row shows the title, a two-line body preview, the
category, and a relative timestamp ("just now", "2m ago"). Clicking a row:

1. POSTs to `/api/notifications/{id}/read` (idempotent).
2. Navigates according to the record's `link` field:
   - `conv://<id>` — opens the linked conversation in the chat view.
   - `vault://<path>` — opens the linked vault page in the sidebar editor.
   - `http://...` or `https://...` — opens in a new tab.
   - no link — just closes the panel.
3. Decrements the badge.

The **Mark all read** button POSTs to `/api/notifications/read-all`, which
appends a `read-all` event. Records with a timestamp at or before that event
are treated as read on subsequent reads.

## Storage

All notification state lives as JSONL files under
`{workspace}/notifications/`:

```
notifications/
  inbox.jsonl                 # live records (newest appended at end)
  read.jsonl                  # read and read-all events
  archive/
    YYYY-MM.jsonl             # rotated-out records, grouped by month
```

Rotation is **opportunistic**: every call to `notify()` (and
`mark_read` / `mark_all_read`) checks whether the first record in the file is
older than `retention_days` and, if so, partitions the file — old records go
to `archive/YYYY-MM.jsonl`, recent records stay. The read-log uses the same
retention window but simply drops old events (they're metadata, not content).

Concurrent appends are guarded by a module-level `asyncio.Lock` keyed on the
agent id. Atomic rewrites use tmp-file + `os.replace` so a crash mid-rotation
can't corrupt the inbox.

## Record shape

```python
@dataclass
class NotificationRecord:
    id: str                    # 12-char hex, unique per record
    timestamp: str             # ISO-8601 UTC, e.g. "2026-04-22T10:15:00Z"
    category: str              # "heartbeat" | "schedule" | "background" | ...
    title: str
    priority: str = "normal"   # "low" | "normal" | "high"
    body: str = ""
    link: str | None = None    # "conv://<id>", "vault://<path>", "https://..."
    conv_id: str | None = None # correlation — set when the event is tied to a conversation
```

## Producers (Phase 1)

Three day-one producers emit notifications during normal operation:

| Category | Source | Priority | Link |
|----------|--------|----------|------|
| `heartbeat` | `run_heartbeat_cycle` in `heartbeat.py` — after all sections finish | `high` if any section alerts, else `normal` | none (cycle spans conversations) |
| `schedule` | `run_schedule_task` in `schedules.py` — after each task turn | `high` on failure, else `normal` | `conv://<id>` (the task's run) |
| `background` | `_run_reader` in `skills/background/tools.py` — after `process.wait()` | `high` on non-zero exit, else `normal` | originating `conv_id` populated |

Compaction and reflection are intentionally *not* producers — both are
mid-turn events visible in-line in the conversation UI, so emitting a
separate async notification would just be noise.

All producers are **fail-open**: any exception during `notify()` is logged
at warning level and discarded. The producer's primary job finishes regardless
of inbox state.

## REST API

All endpoints are guarded by the session cookie auth used elsewhere in the
web UI (401 on unauthenticated requests).

> **Single-user scope.** Phase 1 is single-user by design: the inbox is
> per-agent, not per-authenticated-user. All authenticated callers see the
> same records. Multi-user partitioning (separate inbox + read-state per
> `username`) is tracked with the other multi-user concerns under Phase 2+.
> Don't expose these endpoints across tenants until partitioning lands.

### `GET /api/notifications?limit=20&before=<iso>`

List records newest first. Each record is the raw JSONL payload with a joined
`read: bool` field derived from the current read-state.

- `limit` — 1..200, default 20.
- `before` — optional ISO-8601 timestamp, exclusive upper bound for
  pagination.

Response:

```json
{
  "records": [
    {"id": "...", "timestamp": "...", "category": "heartbeat",
     "title": "Heartbeat completed", "body": "...", "priority": "normal",
     "link": null, "conv_id": null, "read": false}
  ],
  "has_more": false
}
```

### `GET /api/notifications/unread-count`

Cheap counter for badge polling. Response: `{"count": N}`.

### `POST /api/notifications/{id}/read`

Idempotent — appends a `read` event. Response: `{"ok": true}`.

### `POST /api/notifications/read-all`

Appends a `read-all` event. Response: `{"ok": true}`.

## Adding a new producer

From anywhere with a `Context`:

```python
await ctx.notify(
    category="my-category",
    title="Short headline",
    body="Optional longer description (~160 chars).",
    priority="normal",          # "low" | "normal" | "high"
    link="conv://<conv_id>",    # or vault://<path>, or full URL
)
```

`ctx.notify()` auto-populates `conv_id` from the context. Producers without a
`Context` (cron-style cycle runners, detached reader tasks) call the module
function directly:

```python
from decafclaw import notifications
await notifications.notify(
    config, event_bus, category="my-category", title="..."
)
```

The second positional arg is the event bus. It's optional — without it,
the record still gets written to the inbox (durable), just nothing fans
out to channel adapters. Pass it whenever you have it.

Failures inside `notify()` raise normally, so wrap the call in `try/except`
if the emission is best-effort — see any of the producers for the pattern.

## Channel adapters

Beyond the inbox, notifications can fan out to external delivery channels
(Mattermost DM, email, vault summary page, etc.). Phase 2 ships one
adapter — **Mattermost DM** — and establishes the extension point for
more without any further producer-side changes.

### How dispatch works

Every call to `notify()` that carries an event bus publishes a
`notification_created` event **after** the durable inbox append:

```python
{"type": "notification_created", "record": record.to_dict()}
```

Channel adapters are just `EventBus.subscribe(handler)` callables. All
startup wiring lives in a single
`notification_channels.init_notification_channels(config, event_bus, **deps)`
function; `runner.py` calls it once after the Mattermost client is
ready. Adding a new channel means editing that function — not
`runner.py` — along with the new channel module and its
`<name>ChannelConfig` dataclass. There's no new Protocol or registry,
just the event bus.

**Inbox stays authoritative.** The JSONL write happens synchronously
under the per-agent lock; the event publish runs after. A failed inbox
write raises (source of truth must be durable); a failed adapter is
caught, logged, and dropped (delivery is best-effort).

**Dispatch is fire-and-forget.** Each adapter's handler inspects the
event, filters against its own config, and then kicks off its real
delivery work via `asyncio.create_task(self._deliver(record))`. This
means a slow Mattermost post or an SMTP timeout **never blocks
`notify()`** or the producer that called it.

**Filtering is per-adapter.** There's no central router. Each adapter
reads its own config section (`config.notifications.channels.<name>`)
and decides whether a given record matches its priority threshold,
category allow-list, recipient rules, etc. The handler re-reads the
in-memory `config` object on every event, so any in-process mutation
(e.g. a future REST config endpoint) takes effect on the next
notification. **Editing `config.json` on disk still requires an agent
restart** — there's no file-reload mechanism today.

### Mattermost DM adapter

`src/decafclaw/notification_channels/mattermost_dm.py`.

Subscribed at startup iff:
- `config.notifications.channels.mattermost_dm.enabled` is `true`
- `config.notifications.channels.mattermost_dm.recipient_username` is non-empty
- The Mattermost client is configured and running
  (`config.mattermost.url` + `config.mattermost.token`)

If any of those are missing, the adapter isn't wired — no `notify()`-
time errors, no log spam.

Per-event filter: records at or above
`config.notifications.channels.mattermost_dm.min_priority` (default
`high`) are delivered; others are dropped silently.

DM body shape:

```
⚠️ **Heartbeat: 2 alert(s)**
1 OK, 2 alert(s) across 3 section(s).
→ <http://agent.local/#conv=heartbeat-20260423-1201-0>
```

- Priority glyph (`·` low / `🔔` normal / `⚠️` high) plus bolded title.
- Body on the next line(s) (only when non-empty).
- Link line at the bottom — present only when either the record has an
  explicit `http(s)://` link **or** `config.http.base_url` is set and
  the record carries a `conv_id` (in which case the link is
  `<base_url>/#conv=<conv_id>`).

Delivery failures log at `warning` level with category, priority, and
conv_id for diagnosis. The inbox record is the source of truth, so the
DM is best-effort; we don't retry.

### Email adapter

`src/decafclaw/notification_channels/email.py`. Full user-facing guide
in [email.md](email.md); this section covers the channel-adapter
specifics only.

Configured under `config.notifications.channels.email`:

- `enabled: bool` — master switch.
- `recipient_addresses: list[str]` — plain-text addresses the email is
  sent to (this list IS the trust boundary; the channel does NOT
  consult the tool's `email.allowed_recipients`).
- `min_priority: "low" | "normal" | "high"` (default `high`).

**Startup guard** — the adapter is only subscribed when all of:

- `notifications.channels.email.enabled` is true
- `recipient_addresses` is non-empty
- `email.enabled` is true (the core SMTP config)
- `email.smtp_host` is non-empty
- `email.sender_address` is non-empty

Missing any piece → adapter not wired; no runtime errors.

**Body format:** plain text only. Priority glyph + title on the header
line, body on the next lines, optional link line at the bottom (same
shape as the Mattermost DM body, minus the markdown bold since mail
clients render literal text). Subject is `[<agent_id>] [<category>] <title>` — the agent-id prefix makes it easy to filter in a shared inbox.

**Dispatch:** fire-and-forget via `asyncio.create_task(_deliver(...))`.
Errors in `_deliver` log at warning level and are swallowed.

### Adding a new adapter

1. Add a typed channel-config dataclass to
   `config_types.py::NotificationsChannelsConfig`.
2. Create `src/decafclaw/notification_channels/<name>.py` with a
   `make_<name>_adapter(config, ...deps) -> handler` factory.
3. Add an `if <name>_cfg.enabled and ...: event_bus.subscribe(...)` block
   to `init_notification_channels` in
   `src/decafclaw/notification_channels/__init__.py`. If the adapter
   needs a runtime dep (e.g. an SMTP client or a bot connection), add a
   keyword arg to `init_notification_channels` and pass it from
   `runner.py`.
4. Follow the established pattern: filter → format → `asyncio.create_task(deliver)` → catch + log in `_deliver`.

The Mattermost DM and email adapters are ~90 lines each and both are
good templates.

## Configuration

See [config.md#notifications](config.md#notifications) for the two tunables:

- `notifications.retention_days` — how long records stay in the live inbox
  (default 30).
- `notifications.poll_interval_sec` — web UI poll interval in seconds
  (default 30). The JS client currently uses a 30s hardcoded value; changing
  the config only affects Phase 2+ producers that check it directly. See
  [issue #292](https://github.com/lmorchard/decafclaw/issues/292) for the
  plumbing follow-up.

## Coming in later phases

Still deferred (tracked on [#292](https://github.com/lmorchard/decafclaw/issues/292)):

- **More channel adapters** — Mattermost channel post (vs DM), vault
  summary page.
- **Periodic newsletters** (#283) — composer layer on top of channels
  that coalesces scheduled-task activity into daily/weekly rollups.
- **Multi-user inbox partitioning** — currently single-agent,
  single-user.
- **WebSocket push** so the web UI bell gets real-time updates without
  the 30s polling loop. The `notification_created` event already exists;
  a WebSocket subscriber is all that's missing.
- **`health_status` integration** — aggregated per-adapter last-error
  counters once 2+ adapters are in play.
- **JSON schema** for the inbox files, versioning, and migration
  tooling.
