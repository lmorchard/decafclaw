# Spec — Notification Channel Adapters (#292 Phase 2)

## Context

Phase 1 of [#292](https://github.com/lmorchard/decafclaw/issues/292) shipped the notification inbox — a persistent JSONL log under `workspace/notifications/`, a `notify()` / `ctx.notify()` API, a web UI bell + dropdown in the sidebar, and three day-one producers (heartbeat, scheduled-task, background-job exit). That's the durable record.

Phase 2 adds **delivery to channels outside the web UI** — starting with Mattermost DM (#96). The core of this phase isn't any single adapter, it's the **dispatch abstraction** that lets future adapters (email #231, vault summary page, newsletter #283) slot in without rewiring producers.

## Goals

- Unified "dispatch to channels" pipeline that runs after every inbox append.
- Ship at least **one adapter** (Mattermost DM — original motivation) in this session.
- Clear convention so future channels slot in without interface changes.
- No regression to Phase 1 behavior — inbox stays authoritative, fail-open semantics preserved.

## Non-goals

- Multi-user partitioning (Phase 1 is single-user; still is).
- Per-user preference storage.
- Newsletter / coalesced reports (#283 — separate session).
- Agent-facing background-exit delivery (#241 — separate session; this is user-facing only).
- Retry / durable delivery. Inbox is the record; channels are best-effort.

---

## Architecture

**No new abstraction.** Channel adapters are just `EventBus` subscribers.

### Flow

```python
# src/decafclaw/notifications.py
async def notify(config, ...) -> NotificationRecord:
    record = NotificationRecord(...)
    async with lock:
        _rotate_inbox_if_needed(config)
        _append_line(_inbox_path(config), record.to_dict())  # durable, synchronous
    await config.event_bus.publish(
        {"type": "notification_created", "record": record.to_dict()}
    )
    return record
```

**Inbox write is synchronous and authoritative.** It happens before the event fan-out. If the JSONL write raises, nothing else runs. `notify()` returning implies "the record is durably persisted" — the same contract Phase 1 producers and tests rely on today.

Note: `notify()` doesn't currently take an `event_bus`. The function will need the bus passed in, or a ctx-based variant. `ctx.notify()` already has a ctx reference; plain `notify(config, ...)` needs the bus via `config` or as an explicit arg.

**Event payload carries the full record** (`record.to_dict()`) so adapters don't have to read from the inbox to know what to deliver.

### Adapter convention

- An adapter is an `async def handle(event: dict) -> None` callable.
- Subscribed via `event_bus.subscribe(handle)` at startup in `runner.py`.
- On entry, the adapter:
  1. Returns early if `event["type"] != "notification_created"`.
  2. Deserializes the record (`NotificationRecord.from_dict(event["record"])`).
  3. Applies its own filter (priority, category, conv_id shape, etc.).
  4. **Immediately `asyncio.create_task(self._deliver(record))` and returns.** Delivery happens in a background task so `notify()` doesn't block on slow channels.
- Errors inside `_deliver` are caught and `log.warning`-ed. Inbox is the source of truth; channel failures never bubble to producers.

### Routing

**Decentralized — each adapter filters its own events.** No central policy map. Each adapter reads its own `config.notifications.channels.<name>` subsection and decides.

### Config

Typed dataclasses in `config_types.py`, mirroring the `providers` / `model_configs` pattern:

```python
@dataclass
class MattermostDMChannelConfig:
    enabled: bool = False
    recipient_username: str = ""      # Mattermost username to DM; empty = disabled
    min_priority: str = "high"        # "low" | "normal" | "high"

@dataclass
class NotificationsChannelsConfig:
    mattermost_dm: MattermostDMChannelConfig = field(default_factory=MattermostDMChannelConfig)
    # email: EmailChannelConfig = ...  (#231, later)

@dataclass
class NotificationsConfig:
    retention_days: int = 30
    poll_interval_sec: int = 30
    channels: NotificationsChannelsConfig = field(default_factory=NotificationsChannelsConfig)
```

Wired into `load_config()` using the same nested-dataclass pattern already used by `agent.preemptive_search`.

### Module layout

New package:

```
src/decafclaw/notification_channels/
  __init__.py
  mattermost_dm.py    # MattermostDMChannel class + factory
```

Each channel module exposes a factory like:

```python
def make_mattermost_dm_adapter(config, mm_client) -> Callable[[dict], Awaitable[None]]:
    ...
```

The factory closes over `config` and the `MattermostClient` reference.

### Startup wiring

In `runner.py`, after the event bus and Mattermost client are initialized:

```python
from .notification_channels.mattermost_dm import make_mattermost_dm_adapter

mm_cfg = config.notifications.channels.mattermost_dm
if mm_cfg.enabled and mm_cfg.recipient_username and mm_client:
    adapter = make_mattermost_dm_adapter(config, mm_client)
    event_bus.subscribe(adapter)
    log.info("Notifications: Mattermost DM adapter subscribed (recipient=%s, min_priority=%s)",
             mm_cfg.recipient_username, mm_cfg.min_priority)
```

Graceful degradation: if Mattermost isn't configured or the client isn't running, the adapter simply isn't wired — no errors, no warnings at `notify()` time.

### Mattermost DM message shape

Simple markdown body. Example:

```
🔔 **Heartbeat: 2 alert(s)**
1 OK, 2 alert(s) across 3 section(s).

→ <https://agent.example.com/#conv=heartbeat-20260423-1201-0>
```

- Priority glyph + title on the header line.
- Body on the next line(s).
- Optional link at the bottom: only included if `config.http.base_url` is set AND the record has a `conv_id` or `link` we can map. For Phase 2, link to `<base_url>/#conv=<conv_id>` when `conv_id` is set.

Delivered via `MattermostClient.post_direct_message(username, text)` (or equivalent — we'll use whatever method the existing client exposes for DMs; if it doesn't have one, we add a thin helper).

### Observability

Log-only for this session. Adapter errors go through `log.warning("Mattermost DM delivery failed: %s", exc)` with context (adapter name, category, conv_id). Revisit `health_status` integration once a second adapter lands.

### Idempotency / retry

None. Fire once, let it fail. Inbox is the ground truth; if a DM gets lost, the user still has the record in the bell.

---

## Scope

- [ ] `NotificationsChannelsConfig` + `MattermostDMChannelConfig` dataclasses in `config_types.py`; `NotificationsConfig.channels` field.
- [ ] Wire new nested config through `config.py` `load_config()`.
- [ ] Refactor `notifications.notify()` to publish `notification_created` event after inbox append. Requires `event_bus` access — prefer passing via `config` (add `config.event_bus` attribute set in `runner.py` / main) or an explicit arg.
- [ ] Update `ctx.notify()` to pass the bus through.
- [ ] Update producers that call module-level `notify()` directly (e.g. `heartbeat.py`, `schedules.py`, `skills/background/tools.py`) to pass the bus.
- [ ] New package `src/decafclaw/notification_channels/` with `mattermost_dm.py`.
- [ ] `make_mattermost_dm_adapter(config, mm_client)` factory returning an async handler. Handler:
  - Filters by `min_priority` and `enabled`.
  - Formats the DM body.
  - `asyncio.create_task`s `mm_client.post_direct_message(...)`.
  - Catches + logs exceptions in the detached task.
- [ ] Wire adapter registration in `runner.py`.
- [ ] Check whether `MattermostClient` has a `post_direct_message` method or equivalent. If not, add a thin helper.
- [ ] Tests:
  - `tests/test_notifications.py`: confirm `notify()` publishes the event after the inbox append.
  - `tests/test_notification_channels_mattermost.py` (new): adapter filter correctness, message formatting, create_task dispatch, exception swallowing. Mock `MattermostClient`.
  - `tests/test_config.py`: new channels config loading from JSON + defaults.
- [ ] Docs:
  - `docs/notifications.md` — new "Channel adapters" section explaining the EventBus model, the adapter convention, and the Mattermost DM channel specifically.
  - `docs/config.md` — `notifications.channels.mattermost_dm.*` entries.
  - `CLAUDE.md` — one-line bullet under the existing notification convention mentioning the channel-adapter extension point.

## Deferred to follow-on sessions

- **#231** — Email adapter (SMTP)
- **#283** — Periodic newsletter (composer layer on top of channels)
- **Mattermost channel adapter** (vs. DM — e.g. post heartbeats to `#agent-status`)
- **Vault summary page adapter** (daily/weekly rollup page)
- **Multi-user routing** / per-user channel preferences
- **`health_status` integration** — surface per-adapter last-error once 2+ adapters exist
- **WebSocket push to web UI** — the same `notification_created` event could drive real-time badge updates, eliminating the 30s polling loop. Small additional scope once the event exists.

---

## Brainstorm decisions (for the record)

See `notes.md` for the Q&A trail. In short:

1. **Adapters = EventBus subscribers**, not a new Protocol/registry.
2. **Inbox write stays synchronous** in `notify()`; event publishes after.
3. **Event payload is the full record** (`record.to_dict()`).
4. **Per-adapter internal filtering** (no central router).
5. **Fire-and-forget dispatch** via `asyncio.create_task` inside each adapter.
6. **Typed config dataclasses** in `config_types.py`, following the providers/models pattern.
7. **`notification_channels/` package** with one file per adapter, factory closes over `MattermostClient`.
8. **Mattermost DM defaults to `min_priority: high`**.
9. **Log-only observability** for this session; health integration later.
10. **Skip if not configured** — no `notify()`-time error path for a misconfigured channel.
