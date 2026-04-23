# Spec — Vault Page Notification Channel

## Context

Part of [#292](https://github.com/lmorchard/decafclaw/issues/292) — Phase 4 of the channel-adapter work that started with the Phase 1 inbox. Third external surface after Mattermost DM (#315) and email (#231/#320).

Channel adapters are already `EventBus` subscribers wired via `init_notification_channels(config, event_bus, **deps)` in `src/decafclaw/notification_channels/__init__.py`. This session adds a **vault page adapter** that appends notifications to a daily rollup page under the agent's vault.

Motivation: complement the two push channels (MM DM, email) with a persistent, local audit trail. Notifications become part of the vault filesystem — `vault_list` can browse them, filename-as-date gives free chronological ordering, and the dream/garden skills can consolidate or summarize them later if useful.

## Goals

- Ship a vault-page adapter that appends each matching notification to a daily page at `<folder>/YYYY-MM-DD.md`.
- Same adapter shape as MM DM / email — factory closure, per-event filter, fire-and-forget dispatch.
- Zero external deps / credentials; operates purely on local vault files.
- Concurrent-append safe via `asyncio.Lock` keyed on the page path.
- Sandboxed folder path — configured folder must resolve under the vault root.

## Non-goals

- Rollup / coalescing — each notification → one append entry. Dream/garden can consolidate later if daily volume warrants.
- Embedding / semantic-search indexing. Notifications are a rolling log, not reference material; the embedding cost isn't worth the marginal search value.
- Auto-generated index / landing page. Vault folder browsing + `vault_list` cover this.
- Modifying `agent/journal/` by default — keeping notifications separate from `vault_journal_append`-authored observations. Different semantics: system events vs agent-authored journal entries.
- Idempotency on agent restart — inbox is the source of truth; duplicate appends if something replays are acceptable (and unlikely in practice).
- WebSocket push, newsletter composer, multi-user partitioning, per-adapter health counters — all deferred to follow-on issues.

---

## Architecture

New module: `src/decafclaw/notification_channels/vault_page.py`

```python
def make_vault_page_adapter(config: Any) -> Callable[[dict], Awaitable[None]]:
    """Factory. Returns an async event-bus handler."""

    async def _deliver(record: NotificationRecord) -> None:
        """Background-task delivery — fire-and-forget from handler."""
        try:
            async with _page_lock(path):
                _ensure_page_exists(path, record)
                _append_entry(path, record, base_url)
        except Exception as exc:
            log.warning("Vault page delivery failed: ...", exc)

    async def handle(event: dict) -> None:
        if event.get("type") != "notification_created":
            return
        ch = config.notifications.channels.vault_page
        if not ch.enabled:
            return
        record = NotificationRecord.from_dict(event["record"])
        if not _meets_priority(record.priority, ch.min_priority):
            return
        path = _daily_page_path(config, record.timestamp)
        if path is None:  # folder sandbox rejected it
            return
        asyncio.create_task(_deliver(record))

    return handle
```

### Entry format (per Q1 decision)

Subheading + metadata block + body per entry. Every entry is appended to the end of the daily page:

```markdown
## 14:32 UTC · ⚠️ [heartbeat] Heartbeat: 2 alert(s)

- priority: high
- conv_id: —
- link: —

1 OK, 2 alert(s) across 3 section(s).

```

With a populated `conv_id` and `link`:

```markdown
## 09:00 UTC · 🔔 [schedule] Scheduled: weekly-digest

- priority: normal
- conv_id: schedule-weekly-digest-20260423-0900
- link: http://agent.local/#conv=schedule-weekly-digest-20260423-0900

HEARTBEAT_OK

```

Priority glyphs: `·` low / `🔔` normal / `⚠️` high — matches MM DM and email body formatting.

**Empty-field handling:** dashes (`—`) for absent conv_id / link / body keep the format consistent and machine-parseable.

**Timestamp:** `HH:MM UTC` (date is in the filename, full timestamp would be redundant). Records already carry UTC ISO-8601 timestamps — parsed with the existing `notifications._parse_iso`.

### Page initialization

When the daily page doesn't exist yet, write a small header + empty body:

```markdown
---
title: "Notifications 2026-04-23"
tags: [notifications, system]
---

# Notifications — 2026-04-23

```

Frontmatter is minimal — this page is not intended for embedding / vault_search (per Q2). The title + tags let an Obsidian user filter / find the file naturally.

### Daily page path

```python
def _daily_page_path(config, iso_timestamp: str) -> Path | None:
    """Compute the daily page path under the configured folder.

    Returns None when the configured folder escapes the vault root
    (same sandboxing rules the vault tools use). Caller skips delivery
    with a log.warning.
    """
    ch = config.notifications.channels.vault_page
    vault = config.vault_root.resolve()
    folder = (vault / ch.folder).resolve()
    if not folder.is_relative_to(vault):
        return None
    date_part = _parse_iso(iso_timestamp).strftime("%Y-%m-%d")
    return folder / f"{date_part}.md"
```

### Concurrency guard

Per-path `asyncio.Lock`, module-level `dict[Path, Lock]` matching the existing `notifications._locks` pattern. Lock is acquired around both the "does the file exist, and create-with-header if not" check AND the append write, so concurrent creates at midnight don't race.

---

## Configuration

New typed dataclass in `config_types.py`:

```python
@dataclass
class VaultPageChannelConfig:
    """Vault page notification channel — appends to a daily rollup file."""
    enabled: bool = False
    min_priority: str = "low"                    # capture everything by default
    folder: str = "agent/pages/notifications"    # vault-root-relative
```

Added to `NotificationsChannelsConfig` alongside `mattermost_dm` and `email`:

```python
@dataclass
class NotificationsChannelsConfig:
    mattermost_dm: MattermostDMChannelConfig = field(default_factory=MattermostDMChannelConfig)
    email: EmailChannelConfig = field(default_factory=EmailChannelConfig)
    vault_page: VaultPageChannelConfig = field(default_factory=VaultPageChannelConfig)
```

### Startup guard

In `init_notification_channels`, 2-way guard:

```python
vp_cfg = config.notifications.channels.vault_page
if vp_cfg.enabled and vp_cfg.folder:
    from .vault_page import make_vault_page_adapter
    event_bus.subscribe(make_vault_page_adapter(config))
    log.info(
        "Notifications: vault page adapter subscribed "
        "(folder=%s, min_priority=%s)",
        vp_cfg.folder, vp_cfg.min_priority,
    )
```

No extra transport deps needed (compare to Mattermost DM needing `mm_client`). That's the whole point of "local channel."

### Path sandboxing fail-loud vs fail-silent

If `folder` is misconfigured (contains `..` or resolves outside vault root), the adapter is still subscribed at startup — but every delivery attempt returns early after `_daily_page_path` returns `None`. We log a `warning` on the first rejected path and then throttle, so we don't spam per notification. Alternative: reject at startup. But I'd rather subscribe + fail-open at delivery because a config mutation mid-run could go either way; consistent error path is nicer.

---

## Scope

- [ ] `VaultPageChannelConfig` dataclass in `config_types.py`; `NotificationsChannelsConfig.vault_page` field.
- [ ] `src/decafclaw/notification_channels/vault_page.py` — factory + handler + formatters + path helper + per-path lock.
- [ ] `init_notification_channels` updated with the new channel block.
- [ ] Tests:
  - `test_notification_channels_vault_page.py`:
    - Priority filter
    - Enabled / disabled gate
    - Empty-folder gate
    - Path sandbox (folder resolves outside vault → skipped)
    - New-page creation (creates file with header+frontmatter)
    - Append to existing page (preserves prior content)
    - Entry format: heading line, metadata block, body (plus em-dash handling for empty fields)
    - Concurrent appends on the same page (spawn N handlers, assert N entries present, no corruption)
    - Errors during write → warning logged, no crash
  - `test_config.py`: new defaults + nested JSON loading for `vault_page`
  - `test_notification_channels_init.py`: init subscribes vault_page when enabled, skips otherwise
- [ ] Docs:
  - New "Vault page channel" subsection in `docs/notifications.md` (twin of MM DM + email subsections)
  - New `notifications.channels.vault_page` entry in `docs/config.md`
  - CLAUDE.md: key-files entry for the new module (if we're being consistent — probably not needed since `notification_channels/` is already listed as a package)

## Deferred to follow-on sessions

- Mattermost channel post (vs DM)
- `#283` — Periodic newsletter composer (can now consume vault pages as a source)
- Multi-user partitioning
- WebSocket push to web UI bell
- `health_status` per-adapter aggregation

---

## Brainstorm decisions (for the record)

See `notes.md` for the Q&A trail.

1. **Entry format = Option B** — subheading + metadata block + body per entry. Each notification becomes an addressable section; Obsidian's outline acts as a daily index.
2. **Embeddings = Option B (skip)** — notifications are rolling audit log, not knowledge. Avoids re-indexing a growing day-page on every append. If semantic search over past notifications becomes useful, adding embedding later is a small forward change.
3. **Concurrency = Option A** — `asyncio.Lock` keyed on the page path, same pattern as `notifications._locks`. Atomic POSIX append isn't safe for entries > PIPE_BUF.
4. **Folder = Option A (configurable with sensible default)** — `VaultPageChannelConfig.folder: str = "agent/pages/notifications"`. Validated at use-time (path sandbox), same pattern as the vault tools.
5. **Default `min_priority` = Option A (`low`)** — the channel's job is completeness. Users can raise the threshold if a future producer gets chatty.
6. **Index page = Option B (none)** — vault folder navigation + `vault_list` cover discoverability; garden skill can build a digest later if useful.

Pre-resolved:
- **Cross-referencing** — include `record.link` as-is in the metadata block. No speculative wiki-link construction.
- **Idempotency** — out of scope. Inbox is the source of truth; duplicates are acceptable.
