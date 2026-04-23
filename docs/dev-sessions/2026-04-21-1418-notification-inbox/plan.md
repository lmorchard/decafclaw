# Implementation Plan

Source spec: `spec.md` in the same directory. Read that for design context.

## Strategy

Sequence work so each step is independently testable and commitable. Each step ends with `make check` + `make test` clean and a focused git commit. Order:

1. **Core notifications module** — `NotificationsConfig`, `notify()`, storage, rotation, read-state. No integration yet. Full unit test coverage.
2. **`ctx.notify()` convenience wrapper** — thin method on Context, trivial once step 1 is done. Folded into step 1's commit if small enough.
3. **REST endpoints** — four endpoints on the HTTP server, auth-guarded, paginated.
4. **Web UI bell + panel** — new component, polling, mark-read, navigation. Can be developed against mock data, then tested end-to-end after day-one producers land.
5. **Day-one producers** — wire all five event types (heartbeat, schedule, background, compaction, reflection). Requires `BackgroundJob.conv_id` field addition.
6. **Docs pass** — new `docs/notifications.md`, cross-refs in config.md / web-ui.md / CLAUDE.md.

Each step adds no functionality visible to the user until step 4+5 lands together. Safe ordering — I can ship step 1–3 and still land the UI+producers in the same PR.

---

## Step 1 — Core notifications module

**Goal:** pure-library module + config scaffolding. No integration, no UI, no producers. Full unit test coverage.

**Files:**
- `src/decafclaw/config_types.py` — new `NotificationsConfig` dataclass:
  ```python
  @dataclass
  class NotificationsConfig:
      retention_days: int = 30
      poll_interval_sec: int = 30
  ```
  Nested under top-level `Config` via `notifications: NotificationsConfig = field(default_factory=NotificationsConfig)`.
- `src/decafclaw/config.py` — ensure `load_sub_config` recursion added in PR #263 already handles the new nested config. Verify in testing.
- `src/decafclaw/notifications.py` — new module:
  - `NotificationRecord` dataclass (mirrors spec's record shape, converts via `.to_dict()` / `.from_dict()`)
  - `notify(config, *, category, title, body="", priority="normal", link=None, conv_id=None)` — appends record, triggers rotation if needed
  - `read_inbox(config, limit=None, before=None)` — returns records (newest first), respecting pagination
  - `mark_read(config, record_id)` — appends `read` event
  - `mark_all_read(config)` — appends `read-all` event
  - `unread_count(config)` — returns count of unread records in current inbox
  - `get_read_ids(config)` — reconstructs read-id set from `read.jsonl`, filters against live inbox
  - Internal helpers: `_rotate_inbox_if_needed()`, `_rotate_read_log_if_needed()`, `_atomic_rewrite(path, lines)`, `_inbox_path(config)`, `_read_log_path(config)`, `_archive_dir(config)`
  - Module-level `asyncio.Lock` keyed on `config.agent.id` (single-process; if multi-agent-instance ever becomes a concern, can switch to a file lock later)
- `src/decafclaw/context.py` — add `notify()` async method on `Context` as a thin wrapper forwarding to the module function with `config` + `conv_id` auto-populated.
- `tests/test_notifications.py` — new. Covers:
  - `notify()` appends well-formed records (id is hex, timestamp is UTC ISO-8601)
  - Rotation moves old inbox records to `archive/YYYY-MM.jsonl`
  - Read-log rotation drops old events
  - Atomic rewrite uses `os.replace` semantics (verify no partial file on crash simulation — use a patched write that raises mid-way)
  - Read-state reconstruction: single-read events, read-all events, mixed, orphan-id filtering
  - `unread_count` correctness across all the above
  - Concurrent `notify()` calls under `asyncio.Lock` don't interleave (spawn 10 tasks, assert 10 records with distinct ids)
  - Empty-file / first-write initialization creates parent dirs
- `tests/test_context.py` — extend with a test that `ctx.notify()` populates `conv_id` from the ctx.
- `tests/test_config.py` — add test for `NotificationsConfig` defaults + loading from JSON.

**Commit:** `feat(notifications): core module, config, and ctx.notify()`

**State after:** module exists with full test coverage. Nothing else in the codebase calls `notify()` yet.

---

## Step 2 — REST endpoints

**Goal:** expose the inbox via the existing HTTP server with auth guards. UI work in step 4 consumes these.

**Files:**
- `src/decafclaw/http_server.py` (or wherever conversation-API routes live — check during execution) — add four routes:
  - `GET /api/notifications?limit=20&before=<iso>` — returns `{"records": [...], "has_more": bool}`. Each record includes a `"read": bool` field joined from the read-state set. Paginated by `before` timestamp cursor (exclusive).
  - `GET /api/notifications/unread-count` — returns `{"count": N}`. Fast path; called every poll interval.
  - `POST /api/notifications/{id}/read` — appends a `read` event; returns `{"ok": true}`. Idempotent.
  - `POST /api/notifications/read-all` — appends a `read-all` event; returns `{"ok": true}`.
- Reuse existing auth wrapper from other routes (e.g. `/api/conversations/*`). Unauthenticated requests get 401.
- `tests/test_web_notifications.py` — new. Covers:
  - Auth guard rejects unauthenticated requests
  - GET returns records with `read` field populated correctly
  - Pagination via `before` cursor
  - Empty inbox returns empty list, not error
  - `unread-count` counts correctly
  - POST read-one marks read; subsequent GET shows `"read": true`
  - POST read-all marks everything read

**Commit:** `feat(notifications): REST endpoints for inbox + read-state`

**State after:** a curl-able API over the inbox. Still no UI, no producers.

---

## Step 3 — Web UI bell + panel

**Goal:** the user-visible piece. Bell in header, dropdown panel, polling, click-to-navigate.

**Files:**
- `src/decafclaw/web/static/components/notification-inbox.js` — new Lit component:
  - Renders a bell icon with an unread-count badge (hidden at count = 0).
  - Manages a `setInterval` for polling `GET /api/notifications/unread-count` every `config.notifications.poll_interval_sec` (served to the client via existing config endpoint or a new small one).
  - On bell click: fetches `/api/notifications?limit=20`, opens dropdown panel.
  - Panel: list of rows (`●` marker + title + relative time + body preview), "Mark all read" button at top, "Show older" button at bottom.
  - Row click: POST mark-read, update local state (`●` removed, badge decremented), navigate per `link` scheme.
  - `conv://{id}` → dispatch a navigate event the app shell handles
  - `vault://{path}` → same, to vault page
  - `https://...` → `window.open(url, "_blank")`
  - `mm://...` → no-op (just mark read) for v1
  - No link → close the panel (or stay open — see plan-phase open question)
  - Click outside the panel closes it (existing pattern from `context-inspector`).
  - Uses `createRenderRoot() { return this; }` per project convention.
- Web app shell component (check existing file) — embed `<notification-inbox>` in the header, near the context-usage indicator.
- Small relative-time helper (`formatRelativeTime(iso) -> "2m ago"`) — reuse if there's an existing one in the codebase, else add a small function in `src/decafclaw/web/static/lib/utils.js` (or similar).
- Client config plumbing: the UI needs `poll_interval_sec`. If the existing conv-load payload already includes config, extend it; otherwise add a small `GET /api/config/ui` that returns the subset of config the UI cares about.
- CSS: bell-icon sizing, badge styling, panel layout. Reuse existing token variables.
- `tsc --noEmit` must pass. JSDoc comments for types where the component has non-trivial shape.

**Commit:** `feat(notifications-web): bell icon + dropdown panel + polling`

**State after:** the UI works against the API. With zero notifications, the bell is visible but empty. Ready for producers to populate the inbox.

**Verification (manual, before commit):** hit `curl -X POST` on a test-authenticated endpoint to inject a record; verify the bell badge updates within one poll cycle; click and verify the panel contents render.

---

## Step 4 — Day-one producers

**Goal:** five event types emit notifications. The inbox starts filling up in realistic use.

**Files and hooks:**

### 4a — Heartbeat cycle completion
- `src/decafclaw/heartbeat.py` — after `_run_heartbeat_to_channel` completes (all sections done), call `await notify(config, category="heartbeat", title="Heartbeat completed", body=f"{ok_count} section(s) OK, {err_count} error(s).")`. No `conv_id` (cycle is cross-conversation).
- Existing Mattermost posting remains unchanged.

### 4b — Scheduled-task completion
- `src/decafclaw/schedules.py` — after each task's `run_agent_turn` returns, `await notify(config, category="schedule", title=f"Scheduled: {task.name}", body=<short summary>)`.
- Summary: take the response's first line or first ~120 chars.

### 4c — Background process exit
- `src/decafclaw/skills/background/tools.py`:
  - Add `conv_id: str = ""` field to `BackgroundJob` dataclass.
  - Populate at `BackgroundJobManager.start()` call site by passing `ctx.conv_id` from `tool_shell_background_start`.
  - At the end of `_run_reader()` — after process.wait() returns — emit a notification:
    - Title: `"Background job completed"` (exit 0) or `"Background job failed"` (non-zero)
    - Body: `f"{job.command[:80]} — exit code {job.exit_code}"` plus last line of stdout if non-trivial
    - Priority: `normal` on success, `high` on non-zero
    - `conv_id=job.conv_id` for correlation
- Note: `_run_reader` doesn't have a direct `config` reference. Pass it in at task creation (store `config` on the job or on the manager).

### 4d — Compaction events
- `src/decafclaw/compaction.py` — after compaction commits its summary, emit:
  - Title: `"Conversation compacted"`
  - Body: `f"{before_msgs} messages → {after_msgs}-message summary."`
  - `conv_id=<the compacted conversation's id>`
- Fires for both manual (`conversation_compact` tool) and automatic compactions.

### 4e — Agent reflection rejection
- `src/decafclaw/reflection.py` — in the rejection path (when `reflect()` returns `passed=False`), emit:
  - Title: `"Reflection rejected a response"`
  - Body: first ~160 chars of the critique
  - Priority: `low`
  - `conv_id=ctx.conv_id`

**Tests:**
- `tests/test_heartbeat.py` — mock a cycle, assert a notification record is appended with expected shape.
- `tests/test_schedules.py` — same for a scheduled task.
- `tests/test_background_tools.py` — start + wait for a job, assert notification.
- `tests/test_compaction.py` — trigger compaction, assert notification.
- `tests/test_reflection.py` — mock a rejection, assert notification.

**Commit:** `feat(notifications): wire five day-one producers`

**State after:** in normal operation, the inbox populates as the agent does background work.

**Manual smoke (before commit):**
- Start `make dev`, trigger a heartbeat manually (via `!heartbeat` or scheduled fire), see the notification land in the bell within a poll cycle.
- Start a background job, wait for it to exit, verify the notification.
- Force a compaction (long conversation or `!compact` tool), verify.
- Reflection rejection is harder to trigger on demand — accept unit-test coverage for that case.

---

## Step 5 — Documentation

**Goal:** bring docs in line. This is the last step before opening the PR.

**Files:**
- `docs/notifications.md` (new) — full user-facing doc: what the bell does, what shows up where, retention defaults, how to use `notify()` / `ctx.notify()` from new producers, link scheme handling, config options, and a "coming in Phase 2+" note that lists the deferred channels and features (with links to #292 for tracking).
- `docs/config.md` — new `notifications` section with the two config fields.
- `docs/web-ui.md` — brief note about the new bell + inbox, with a screenshot if we want to include one.
- `docs/context-map.md` — optional; add a small note about the inbox as an out-of-band output channel distinct from the event bus. Evaluate if it fits the doc's framing.
- `docs/index.md` — index the new page under "Agent Behavior" (or wherever fits).
- `CLAUDE.md`:
  - Key files: add `src/decafclaw/notifications.py`.
  - Conventions: add a bullet summarizing `notify()` / `ctx.notify()` and the inbox JSONL shape (short; the doc link does the heavy lifting).

**Commit:** `docs(notifications): inbox mechanism, producer patterns, config`

**State after:** ready for PR.

---

## Verification gates

At each step:
1. `uv run ruff check src/ tests/`
2. `uv run pyright`
3. `uv run pytest` (focus on relevant files during the step; full run before commit)
4. For steps 3–4: manual smoke in the web UI before commit.
5. Stage specific files; focused commit message.

Before opening the PR:
- Full `make check` + `make test`.
- Manual end-to-end smoke: fresh browser, see the bell, trigger each producer, verify each category appears in the panel, mark-read works, mark-all-read works, click-to-navigate works for `conv://` and `vault://` links.

## Risks and rollback

- **Background-job `config` reference:** `_run_reader` as a detached task doesn't currently have `config`. Requires passing it at start time. Small change but touches the BackgroundJobManager surface. Plan accounts for this.
- **Client config endpoint:** UI needs `poll_interval_sec` from config. If adding a new endpoint is annoying, fall back to hardcoding a 30s default in the UI and loading the config value "best effort" (if the endpoint exists).
- **Archive directory growth:** monthly JSONL archives persist indefinitely. Not a Phase 1 concern (30-day retention keeps the active file small), but a janitor sweep for truly-ancient archives is a follow-up.
- **Rotation mid-write on cold start:** unlikely but possible edge case where the first `notify()` sees an inbox with stale content. Atomic-rewrite + lock cover it.
- **Rollback:** each step is a focused commit. If step 3 or 4 surfaces problems, revert that commit — the core module (step 1) and REST (step 2) are dead weight but harmless.
