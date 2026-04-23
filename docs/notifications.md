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
    config, category="my-category", title="..."
)
```

Failures inside `notify()` raise normally, so wrap the call in `try/except`
if the emission is best-effort — see any of the producers for the pattern.

## Configuration

See [config.md#notifications](config.md#notifications) for the two tunables:

- `notifications.retention_days` — how long records stay in the live inbox
  (default 30).
- `notifications.poll_interval_sec` — web UI poll interval in seconds
  (default 30). The JS client currently uses a 30s hardcoded value; changing
  the config only affects Phase 2+ producers that check it directly. See
  [issue #292](https://github.com/lmorchard/decafclaw/issues/292) for the
  plumbing follow-up.

## Coming in Phase 2+

Deferred to later phases (tracked on [#292](https://github.com/lmorchard/decafclaw/issues/292)):

- Priority-based routing (e.g. high-priority → Mattermost DM, normal →
  inbox only).
- Delivery channel adapters: Mattermost DM/channel, email, vault summary
  page.
- Multi-user inbox partitioning (currently single-agent, single-user).
- Periodic report composers (daily/hourly) that collect contributions from
  scheduled tasks into a single delivered summary.
- WebSocket push so the web UI gets real-time updates without polling.
- JSON schema for the inbox files, versioning, and migration tooling.
