# Plan — Vault Page Notification Channel

Source spec: `spec.md` in this directory.

## Strategy

4 implementation steps + 1 PR step. Each step ends in a clean commit with tests + `make check` passing. The channel-adapter scaffolding already exists (Mattermost DM and email established the pattern + the `init_notification_channels` dispatch), so the work is mostly applying the template to a new delivery mode. Steps kept small and independent — any one can be reverted without breaking the others.

State after each step is described so the work can be paused and resumed cleanly.

---

## Step 1 — Config dataclass + nested wiring

**Goal:** typed config exists; no behavior change yet.

**Files:**
- `src/decafclaw/config_types.py`:
  - New `VaultPageChannelConfig` dataclass:
    ```python
    @dataclass
    class VaultPageChannelConfig:
        """Vault page notification channel — appends to a daily rollup file."""
        enabled: bool = False
        min_priority: str = "low"
        folder: str = "agent/pages/notifications"
    ```
  - Add `vault_page: VaultPageChannelConfig = field(default_factory=VaultPageChannelConfig)` to `NotificationsChannelsConfig`.
- `tests/test_config.py`:
  - Extend `test_notifications_defaults` with the new channel's default assertions (enabled False, min_priority "low", folder string).
  - Extend `test_loads_nested_channels_from_json` (or add a twin test) with a JSON block setting the vault_page channel, asserting it loads correctly.

**Done when:** `make check` + `make test` clean. No production code reads the new config yet.

**Commit message:** `feat(notifications-channel): VaultPageChannelConfig dataclass`

---

## Step 2 — `vault_page.py` module (pure logic, no wiring)

**Goal:** the adapter module exists and works in isolation. Nothing in `runner.py` / `init_notification_channels` subscribes it yet.

**Files:**
- `src/decafclaw/notification_channels/vault_page.py` — new module:
  - Private helpers:
    - `_PRIORITY_GLYPH`, `_PRIORITY_ORDER` — same semantics as the other adapters.
    - `_meets_priority(record_priority, min_priority) -> bool`.
    - `_daily_page_path(config, iso_timestamp) -> Path | None`:
      - Rejects if `channel.folder` is empty, absolute, or contains `..` — an absolute `folder` would silently discard the vault root in `Path.__truediv__`, so this has to be an explicit check.
      - Resolves `(vault_root / channel.folder).resolve()` and verifies it `is_relative_to(vault_root.resolve())`.
      - Returns `None` on any failure; caller logs once per bad folder and bails.
      - On success, returns `folder / f"{YYYY-MM-DD}.md"` using `notifications._parse_iso(iso_timestamp)`.
    - `_format_entry(record, base_url) -> str`:
      - Produces the `## HH:MM UTC · <glyph> [<category>] <title>` + metadata block + body, ending with a trailing blank line.
      - Em-dashes (`—`) for empty `conv_id` / `link` / `body`.
      - Link resolution mirrors the email channel: explicit `http(s)://` link wins; else `base_url + '#conv=' + conv_id` if both present; else None/`—`.
    - `_format_new_page_header(date_str) -> str` — frontmatter + H1 for a freshly-created daily page.
    - `_get_lock(path) -> asyncio.Lock` — module-level `dict[Path, Lock]` keyed on resolved path. Same shape as `notifications._locks`.
  - Public factory:
    - `make_vault_page_adapter(config) -> Callable[[dict], Awaitable[None]]`.
    - Closes over `config`. Returns async `handle(event)`.
    - `handle` checks event type + enabled + min_priority, resolves the daily path (bails silently on sandbox rejection after a one-time warning logged per bad folder), then `asyncio.create_task(_deliver(record, path))`.
    - Per-event re-read of config (same pattern as MM DM / email — in-memory mutations take effect immediately).
  - `async _deliver(record, path, base_url)`:
    - Acquires the per-path lock.
    - If file doesn't exist: write header.
    - Append formatted entry with `"a"` mode.
    - Catches + logs any `Exception` at warning level — the inbox is the source of truth, deliveries are best-effort.
- `tests/test_notification_channels_vault_page.py` — new:
  - Filter tests: enabled/disabled, folder empty, min_priority threshold.
  - Sandbox tests: folder with `..` returns no path; absolute-path folder returns no path.
  - Formatter tests: entry shape with/without body/conv_id/link, all three priority glyphs, URL construction from base_url + conv_id.
  - Page-creation test: first notification creates the file with header; file contents match expected shape.
  - Append-to-existing: second notification appends without clobbering.
  - Concurrency test: fire N handlers concurrently against the same page, assert N `## ` headings present and no partial entries.
  - Error swallowing: force a write exception, verify warning logged, no exception propagates.
  - End-to-end via real `EventBus`: `notify()` → adapter → page written.

**Done when:** new test file passes; no existing tests regress. Module is importable but nothing subscribes to it in production.

**Commit message:** `feat(notifications): vault page channel adapter`

---

## Step 3 — Wire into `init_notification_channels`

**Goal:** the adapter is actually subscribed at startup.

**Files:**
- `src/decafclaw/notification_channels/__init__.py::init_notification_channels`:
  - Add a `vault_page` block after the email block. 2-way guard: `vp_cfg.enabled` and `vp_cfg.folder`.
  - Import the factory inside the guard (keeps import light for disabled-channel cases).
  - `log.info` on subscribe with folder + min_priority.
- `tests/test_notification_channels_init.py`:
  - New tests: `test_vault_page_subscribed_when_configured`, `test_vault_page_skipped_when_disabled`, `test_vault_page_skipped_when_folder_empty`.
  - One "all three channels" test that enables all of MM DM, email, vault_page and asserts `subscribe.call_count == 3`.

**Done when:** the adapter is in the dispatch path. Full test suite green.

**Commit message:** `feat(notifications): wire vault page channel in init_notification_channels`

---

## Step 4 — Docs

**Goal:** doc drift closed.

**Files:**
- `docs/notifications.md`:
  - New "Vault page channel" subsection after the email one, covering the 2-way startup guard, daily page layout, append format, concurrency + embedding decisions, link-resolution rules.
  - Update "Coming in later phases" — remove "vault summary page adapter" since it's shipping in this PR.
- `docs/config.md`:
  - New `notifications.channels.vault_page` sub-section with the three fields (`enabled`, `min_priority`, `folder`) + env vars.
  - Note on sandboxing (folder must resolve under the vault root).
- `CLAUDE.md`:
  - Update the "Notification channel adapters" convention bullet — MM DM, email, vault page now all exist. Nothing structurally new.
- No entry in `docs/index.md` — still lives within the notifications doc.

**Done when:** `make check` clean (no code changes in this step, just docs). Self-review reading pass: docs match code.

**Commit message:** `docs(notifications): vault page channel`

---

## Step 5 — PR + Copilot review

**Goal:** PR merged.

1. Squash the 4 commits to one clean commit using the scripted rebase pattern from the email session. Use a comprehensive message covering design + test + doc changes, referencing closes-issue `#292` (Phase 4).
2. Rebase onto `origin/main` to pick up any drift since branch creation.
3. `git push --force-with-lease origin vault-page-channel`.
4. `gh pr create` with Closes reference, design-highlights section, test-plan checklist.
5. `gh pr edit <N> --add-reviewer copilot-pull-request-reviewer`.
6. Poll `gh api repos/.../pulls/<N>/reviews` in the background.
7. Triage each Copilot comment — real bugs and legitimate edge cases: fix, push, loop back; style nitpicks: skip with a brief "why" in the response.
8. Final squash-and-force-push if review rounds added commits.

---

## Verification gates

After each step:
1. `uv run ruff check src/ tests/` (via `make check` — also runs pyright + tsc)
2. `uv run pytest` (focused on the step's files during development; full run before commit)
3. Stage files by name, not `git add -A`.
4. One commit per step.

Before opening the PR:
- Full `make check` + `make test` clean.
- Self-review reading pass on the commit diff vs `origin/main` — catch anything obvious before Copilot does.

## Risks and rollback

- **Concurrency bug on the per-path lock.** The existing `notifications._locks` pattern is well-trodden; we're reusing it verbatim. Main risk: forgetting to hold the lock across `exists-check + create-header + append`. Mitigation: single `_deliver` function takes the lock once at top, does all three ops under it.
- **Sandbox bypass via symlinks.** Theoretically someone could create an `agent/pages/notifications` symlink that points outside the vault. `Path.resolve()` follows symlinks, and the `is_relative_to` check catches the escape. Covered by the existing vault-tools pattern.
- **High-volume log bloat.** Default `min_priority: "low"` captures everything. If a producer starts spamming and daily pages get huge, user raises threshold in config; takes effect on next append (per-event config read). No code change needed.
- **Rollback granularity:** each step is a focused commit. If Step 3 (wiring) surfaces an issue, reverting it leaves Step 1 (config) and Step 2 (module) as dead weight but harmless — the adapter is simply not subscribed.
