# Notification Inbox — #292 Phase 1

Partial implementation of GitHub issue [#292](https://github.com/lmorchard/decafclaw/issues/292) — Notification infrastructure. Ships **Phase 1** only: the JSONL inbox, the `notify()` API, the web UI bell + dropdown panel, and wiring for five day-one producers. External-channel adapters (Mattermost DM, email via #231, vault page) are deferred to Phase 2+.

## Problem

The agent does work the user can't see. Heartbeat cycles run, scheduled tasks process bookmarks and posts, background processes exit, compaction summarizes history, reflection occasionally rejects responses. All of this happens invisibly — the only visible signal today is Mattermost posts from heartbeat, which don't reach users who live primarily in the web UI.

A dedicated notification inbox in the web UI gives the agent a place to surface these events where the user actually is.

## Goals

- Centralize agent-initiated notifications in a persistent inbox.
- Surface them in the web UI via a bell icon + dropdown panel with unread badge.
- Wire up five existing event types on day one so the inbox is immediately useful.
- Establish the `notify()` API as the forward-compat substrate for downstream features (#283 newsletter, #96 event-driven alerts, #241 background completions).

## Non-goals (deferred to Phase 2+)

- External channel adapters (Mattermost DM, Mattermost channel, email via #231, vault page).
- Per-category routing preferences.
- Multi-user support (single-user today; shape is obvious when needed — add `user_id` to records, split JSONLs per user).
- Live server push. Periodic polling is good enough for v1.
- "Clear" / permanent dismiss. Retention cleanup handles it.
- Pagination UI beyond an initial limit + "show older" button.
- Priority-based visual treatment in the UI. Field is stored but not rendered differently for v1.

## Design

### Storage

```
workspace/notifications/
  inbox.jsonl          # append-only notification records
  read.jsonl           # append-only read-state events
  archive/
    2026-04.jsonl      # rotated-out inbox records, month-bucketed
```

Both files are append-only under normal operation. The JSONL format is consistent with other persistent-state files in DecafClaw (conversation archives, checklist storage).

**Atomic writes on rotation.** When we rewrite a file during rotation, use the atomic write pattern (write to `{path}.tmp`, then `os.replace()`). Prevents corruption if the process crashes mid-rewrite. Normal appends (`O_APPEND`) are atomic at the OS level for small writes, no special handling needed.

**Concurrent writes.** Multiple async tasks may call `notify()` concurrently (heartbeat completes while a background job exits, for example). Use an `asyncio.Lock` guarding the append-to-inbox path so interleaved writes can't corrupt the file. Single lock per config object (module-level dict keyed by `config.agent.id` or similar).

**First write / missing files.** `notify()` creates `workspace/notifications/` and parent archive directory as needed. Missing files are treated as empty.

### Notification record shape

```json
{
  "id": "a1b2c3d4e5f6",
  "timestamp": "2026-04-22T10:15:00",
  "category": "heartbeat",
  "priority": "normal",
  "title": "Heartbeat completed",
  "body": "2 section(s) OK, 0 errors.",
  "link": "mm://channel/abc123",
  "conv_id": null
}
```

| Field | Type | Description |
|---|---|---|
| `id` | str | 12-char hex via `secrets.token_hex(6)` (48 bits, collision-free for realistic volumes). |
| `timestamp` | str | ISO-8601 in UTC (e.g. `2026-04-22T10:15:00Z`). Client converts for display. |
| `category` | str | Loose taxonomy: `heartbeat`, `schedule`, `background`, `compaction`, `reflection`. Additional categories added as producers are written. |
| `priority` | str | `low` / `normal` / `high`. Stored but unused in Phase 1 UI; preserved for Phase 2+ routing. |
| `title` | str | Short, shown prominently in the panel. |
| `body` | str | Markdown, shown as preview (first line/sentence) in the panel. |
| `link` | str \| null | Optional. Scheme-based: `conv://{id}`, `vault://{path}`, `mm://...`, `https://...`. Click behavior uses the scheme. |
| `conv_id` | str \| null | Correlation to a conversation if the event belongs to one. Auto-populated by `ctx.notify()`. |

### `notify()` API

Module-level function is the base:

```python
# src/decafclaw/notifications.py
async def notify(
    config,
    *,
    category: str,
    title: str,
    body: str = "",
    priority: str = "normal",
    link: str | None = None,
    conv_id: str | None = None,
) -> None:
    """Append a notification to the inbox.

    In Phase 1 the inbox is the only consumer. Phase 2+ will dispatch
    to external channel adapters here.
    """
```

`ctx.notify(...)` is a thin convenience wrapper that passes `config=self.config, conv_id=self.conv_id` through:

```python
# method on Context
async def notify(self, **kwargs) -> None:
    from .notifications import notify
    await notify(self.config, conv_id=self.conv_id, **kwargs)
```

Producers that have a `ctx` (reflection, compaction in a turn) call `ctx.notify(...)`. Producers that don't (heartbeat cycle completion, background process exit) call the module-level `notify(config, ...)` directly.

### Rotation

Opportunistic rotation on every append. Before writing a new record:

1. If the file is empty or the oldest line's timestamp is within `retention_days`, append normally.
2. Otherwise, partition the file into `(old, recent)` by timestamp. For `inbox.jsonl`: append `old` to `archive/YYYY-MM.jsonl` (bucketed by month of each record's timestamp). For `read.jsonl`: drop `old` (read events are metadata; the records they reference are already archived and irrelevant). Overwrite the file with just `recent`, then append the new record.

Rotation is a rare rewrite. Normal appends are O(1). Read-state reconstruction ignores read-events whose `id` isn't present in the live inbox, so timing drift between rotations doesn't cause bugs.

### Read-state log events

```json
{"event": "read", "id": "a1b2c3d4", "timestamp": "2026-04-22T10:20:00"}
{"event": "read-all", "timestamp": "2026-04-22T10:25:00"}
```

State reconstruction (for "is this notification unread?"):

1. Walk `read.jsonl` forward.
2. `read` events add the id to a set.
3. `read-all` events add *every currently-present inbox id at that timestamp* to the set (computed at reconstruction time by filtering the inbox).
4. A notification is unread iff its id is not in the set.

No `unread` action in Phase 1.

### Config

New nested dataclass under the top-level `Config`:

```python
@dataclass
class NotificationsConfig:
    retention_days: int = 30
    poll_interval_sec: int = 30
```

Accessed as `config.notifications.retention_days` / `config.notifications.poll_interval_sec`. Both configurable via `config.json`; no env-var aliases for v1 (defaults are fine for the common case).

### REST endpoints

- `GET /api/notifications?limit=20&before=<timestamp>` — returns records in reverse chronological order. Includes read state (per record: `"read": true|false`). Paginated by `before` timestamp cursor.
- `GET /api/notifications/unread-count` — returns `{"count": N}`. Polled by the UI every `poll_interval_sec`.
- `POST /api/notifications/{id}/read` — marks one notification read. Appends a `read` event.
- `POST /api/notifications/read-all` — marks all currently-visible notifications read. Appends a `read-all` event.

All endpoints require authentication (reuse existing auth layer).

### Web UI

**Bell icon:** top header bar, right-aligned near the existing context-usage indicator. Small unread-count badge overlays the icon when count > 0 (red/orange dot with a number; hides when count = 0).

**Polling:** a simple `setInterval` in the app shell hits `GET /api/notifications/unread-count` every `poll_interval_sec`. Bell badge updates based on the returned count.

**Dropdown panel:** click the bell to open. Click-outside-to-dismiss (same pattern as the context-inspector popover).

Panel layout (mock):

```
┌────────────────────────────────────┐
│ Notifications       [Mark all read]│
├────────────────────────────────────┤
│ ● Heartbeat completed        2m ago│
│   2 section(s) OK, 0 errors.       │
├────────────────────────────────────┤
│ ● Scheduled: linkding-ingest 15m   │
│   12 bookmarks processed.          │
├────────────────────────────────────┤
│   Compaction                    1h │
│   Conversation compacted: 142 → 7. │
├────────────────────────────────────┤
│            [Show older]            │
└────────────────────────────────────┘
```

- `●` marker for unread (removed after click / mark-all-read).
- Each row: bold title, relative time, one-line body preview.
- Click a row: mark read (POST + local state update), then navigate based on `link`:
  - `conv://{id}` — select that conversation in the sidebar
  - `vault://{path}` — open that vault page in the editor
  - `https://...` — open in new tab
  - `mm://...` — best effort; if the Mattermost deep-link format is known, use it; otherwise no-op navigation (notification still marks read)
  - No `link` — just mark read, close the panel
- "Mark all read" — calls `/api/notifications/read-all`, clears all badges locally.
- "Show older" — loads the next page (append to list).
- Empty state: "No notifications. The agent's quiet today."

### Day-one producers

Five event types wired in Phase 1. Each adds a single `notify()` call.

| Producer | Where | `category` | Example title / body |
|---|---|---|---|
| Heartbeat cycle completion | `heartbeat.py`, after `_run_heartbeat_to_channel` completes | `heartbeat` | "Heartbeat completed" / "2 section(s) OK, 0 errors." |
| Scheduled-task completion | `schedules.py`, after the task's `run_agent_turn` returns | `schedule` | "Scheduled: linkding-ingest" / "12 bookmarks processed." |
| Background process exit | `skills/background/tools.py`, end of `_run_reader()`. Requires storing `conv_id` on `BackgroundJob` at start time for correlation | `background` | "Background job completed" / "`npm run dev` exited with code 0." |
| Compaction events | `compaction.py`, after summary committed | `compaction` | "Conversation compacted" / "142 messages → 7-message summary." |
| Agent reflection rejection | `reflection.py`, when the judge rejects a response | `reflection` | "Reflection rejected a response" / critique excerpt |

Each producer sets `conv_id` when known so the UI can correlate / deep-link. For producers that use `ctx.notify()`, `conv_id` is auto-populated.

Priority guidance for Phase 1 producers:
- Heartbeat / scheduled / compaction: `normal`
- Background exit: `normal` if exit code 0, `high` otherwise
- Reflection rejection: `low` (informational; the agent continues)

### Interaction with existing mechanisms

- **Mattermost heartbeat posts:** unchanged. The heartbeat continues to post to Mattermost; we *additionally* emit a notification. Not mutually exclusive — users with both destinations get both.
- **`publish()` event bus:** separate concern. Event bus is for in-process, per-turn progress (tool_start, tool_end, chunk). Notifications are for out-of-band, user-directed summaries. No unification intended.

## Acceptance criteria

- `make check` (lint + type) passes.
- `make test` passes. New unit tests cover:
  - `notify()` appends well-formed records to `inbox.jsonl`.
  - Rotation moves old records to `archive/YYYY-MM.jsonl` on append.
  - Read-log rotation drops old events on append.
  - Read-state reconstruction: `read` + `read-all` logic, orphan-id filtering.
  - REST endpoints return correctly-structured responses with auth guard.
  - Each day-one producer emits the expected notification record when its trigger fires.
- Manual smoke in the web UI:
  - Bell icon visible in the header with a `0` badge (or hidden when zero).
  - Trigger a heartbeat cycle manually — within `poll_interval_sec`, badge count increments.
  - Click the bell — panel opens with the heartbeat notification at top, `●` unread marker visible.
  - Click the notification — marks read, `●` removes, panel stays open (or closes — TBD in execute).
  - "Mark all read" — all `●` markers clear.
  - Restart the server — notifications persist (JSONL-backed), read state persists.
  - Advance system clock or change `retention_days` to trigger rotation; verify `archive/` file appears.
- Docs updated: new `docs/notifications.md`, cross-references from `docs/config.md` (new config fields), `docs/web-ui.md`, `docs/context-map.md` (if applicable), `CLAUDE.md` key files list.

## Open questions for plan phase

- **Panel stays open vs closes after click:** if the user clicks a notification to read more, does the panel stay open (so they can see related notifications) or close (simpler)? Lean: stays open, so the user can quickly process a backlog.
- **Where exactly does the bell live in the header DOM?** Minor but affects placement. Look at the existing header component during plan phase.
- **Time formatting:** "2m ago" / "1h ago" / absolute past some threshold. Probably reuse an existing util if we have one; otherwise a small helper.
- **Mattermost link scheme:** is there an existing URL scheme for deep-linking to a specific post? If not, `mm://` can just be cosmetic / no-op navigation (mark read but don't navigate).
- **Background-job `conv_id` storage:** confirm `BackgroundJob` dataclass addition is clean; check if any existing jobs in flight would break (probably not — field has a default).
