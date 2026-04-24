# Newsletter — spec

Tracks: [#283](https://github.com/lmorchard/decafclaw/issues/283)

## Goal

A scheduled "newsletter" report that summarizes autonomous agent activity the user didn't directly participate in — work done by scheduled skills (`dream`, `garden`, `linkding-ingest`, `mastodon-ingest`, etc.). One narrative-voiced report per cycle, delivered via configured channels, turning a trickle of background work into a scannable story.

## Design principles

- **Pull / observer model.** Contributing skills remain unaware of the newsletter. The publisher is a retrospective observer that reads existing state (scheduled-task conversations + vault changes) and synthesizes. Zero coupling — adding a new scheduled skill automatically gets it observed.
- **Skill-only implementation.** The entire feature lives in a single bundled skill package. No new core subsystems, no `newsletter_channels/` package, no new event bus events, no `NewsletterConfig` in `config_types.py`, no `runner.py` changes.
- **Complementary to, distinct from, notifications.** A newsletter is a narrative document, not a small notification record. Routing through `notify()` would abuse the schema; the publisher owns its own delivery.
- **Complementary to, distinct from, heartbeat.** Heartbeat is status-focused ("is everything OK?"); newsletter is narrative-focused ("what happened?").

## Scope — Phase 1

- Scheduled publisher skill running daily (7am default).
- Email delivery (using existing `mail.py`).
- Vault-page delivery (one file per newsletter at `{vault_root}/{agent_folder}/journal/newsletters/YYYY-MM-DD.md`).
- `!newsletter` / `/newsletter` user-invocable for on-demand peek (inline reply; does not dispatch through delivery targets; does not touch scheduled cadence state).
- Sources mined: scheduled-task conversation archives + vault page changes (created / modified) in the window.

## Out of scope

- Mattermost channel delivery (deferred; belongs alongside a reusable `mattermost_channel` notification adapter in a later PR).
- Hourly or weekly cadences (Phase 3).
- Topic-based sections / personalization (Phase 3).
- Time-range arguments on `!newsletter` (e.g., `!newsletter since yesterday`) — Phase 3.
- Any opt-in mechanism on contributing skills (no `newsletter:` frontmatter field, no `newsletter_contribute` tool) — explicitly rejected in favor of the pull model.
- Retry-on-delivery-failure logic — archive is the canonical record; re-delivery isn't worth the complexity in Phase 1.

## Architecture

Entire feature lives in **`src/decafclaw/skills/newsletter/`**.

### `SKILL.md`

Frontmatter:
- `name: newsletter`
- `description: Compose and deliver a narrative newsletter summarizing autonomous agent activity in the window.`
- `schedule: "0 7 * * *"` (7am daily)
- `user-invocable: true`
- `allowed-tools`: the skill's own three tools plus whatever read tools the composition prompt needs (details in plan).

Body (the composition prompt): editorial instructions in SOUL voice. Tells the agent to:
1. Call `newsletter_list_scheduled_activity(hours)` and `newsletter_list_vault_changes(hours)` to gather material.
2. Group related entries, prune boring items, apply SOUL.md voice, include stats ("N pages touched, M bookmarks ingested").
3. Include links to vault pages created/modified in the window.
4. Keep the tone conversational — not bullet-point-corporate.
5. Call `newsletter_publish(markdown, subject_hint=None)` as the final step.

### `tools.py`

Three tools, plus supporting dataclasses.

**`SkillConfig`:**

```python
@dataclass
class SkillConfig:
    window_hours: int = 24                                    # env: NEWSLETTER_WINDOW_HOURS
    email_enabled: bool = False                               # env: NEWSLETTER_EMAIL_ENABLED
    email_recipients: list[str] = field(default_factory=list) # env: NEWSLETTER_EMAIL_RECIPIENTS
    email_subject_prefix: str = "[decafclaw newsletter]"      # env: NEWSLETTER_EMAIL_SUBJECT_PREFIX
    vault_page_enabled: bool = True                           # env: NEWSLETTER_VAULT_PAGE_ENABLED
    vault_folder: str = "agent/journal/newsletters"           # env: NEWSLETTER_VAULT_FOLDER
```

Flattened (not nested) to stay compatible with the existing `SkillConfig` loading mechanism
(env vars + `config.skills.newsletter.*` dict + defaults). The nested-dataclass design
described in the initial spec was simplified during implementation to avoid extra loader
complexity that wasn't needed.

Loaded via the existing `SkillConfig` pattern (env vars + `config.skills.newsletter.*` dict + defaults).

**`init(config, skill_config)`:** validates config, prepares `mail.py` handle if email is enabled.

**Tools:**

1. **`newsletter_list_scheduled_activity(hours: int = 24) -> list[dict]`**
   - Lists `workspace/conversations/schedule-*.jsonl` whose timestamp (parsed from the `conv_id` suffix) falls within `[last_run, now)` — or within the explicit `hours` window if `last_run.json` is absent.
   - For each matched archive, parses the JSONL to extract:
     - `skill_name` (from `conv_id`)
     - `conv_id`
     - `started_at`
     - `final_message` (last assistant text message)
     - `vault_pages_touched` (page names from `vault_write` tool-call arguments; journal appends are episodic records, not curated page references, so `vault_journal_append` calls are intentionally excluded)
   - Returns a list of dicts.

2. **`newsletter_list_vault_changes(hours: int = 24) -> list[dict]`**
   - Lists vault page additions / modifications in the window.
   - Mechanism TBD in plan (candidates: `git log` on vault root if it's a git repo; filesystem mtime diff otherwise).
   - Returns `[{path, action: "created"|"modified", size}, ...]`.

3. **`newsletter_publish(markdown: str, subject_hint: str | None = None, has_content: bool = True) -> ToolResult`**
   - Inspects `ctx.task_mode` to choose behavior.
   - **If `ctx.task_mode == "scheduled"`:**
     1. Writes `workspace/newsletter/archive/YYYY-MM-DD.md` (UTC date; if a file already exists for today, suffix with `-N`). This always happens, even when `has_content=False`, so we have a durable record that the run executed.
     2. If `has_content=True`, delivers to each enabled target:
        - Email: builds `Subject: {subject_prefix} {subject_hint or "YYYY-MM-DD"}` and sends markdown body to all recipients via `mail.py`. Links in the body use standard markdown.
        - Vault page: writes `{vault_root}/{agent_folder}/journal/newsletters/YYYY-MM-DD.md` (conflict suffix similarly). Links to other vault pages use Obsidian `[[wiki-link]]` style so they resolve in the vault.
     3. Updates `workspace/newsletter/last_run.json` with `{last_run_utc, window_end_utc}` regardless of `has_content`.
     4. Returns short status text summarizing which targets received the newsletter (or "nothing to report; window advanced" when `has_content=False`).
   - **Else (interactive):**
     - Returns the markdown verbatim as the tool result. No archive, no delivery, no last_run update. `has_content` is ignored.

### Dependencies on existing code

- `src/decafclaw/mail.py` — shared async SMTP core, consumed directly by the tool.
- `src/decafclaw/vault/` helpers — for writing the vault page (path resolution, frontmatter).
- Workspace path resolution from `config` — `workspace_path`, `vault_path`, `agent_folder`.
- `parse_skill_md` / skill discovery / command dispatch — already supports bundled skills with both `schedule:` and `user-invocable: true`. No changes needed.

### No core changes

Scheduled-task conversations already use a deterministic `conv_id` prefix (`schedule-{task_name}-{timestamp}` per `schedules.py`), so filtering scheduled archives needs no core change. The `ctx.task_mode` value we need for behavior-switching in `newsletter_publish` is already set by the scheduled-task runner (`task_mode="scheduled"`) and available on the context at tool-call time.

## Data flow

### Scheduled path (7am)

1. Scheduler fires → newsletter skill runs as a `task_mode="scheduled"` agent turn.
2. Agent follows SKILL.md body: gathers activity via list tools, composes narrative, calls `newsletter_publish`.
3. `newsletter_publish` (scheduled branch): archives locally, dispatches to enabled targets inline, updates `last_run.json`.

### Interactive path (`!newsletter`)

1. User types `!newsletter` in a chat → command dispatch runs the same SKILL.md body.
2. Agent gathers activity (same tool calls), composes narrative, calls `newsletter_publish`.
3. `newsletter_publish` (interactive branch): returns markdown as tool result so the reply shows it. No archive, no delivery, no state change. Scheduled cadence is unaffected.

## Error handling

- **Composition failure / LLM error (scheduled):** `last_run.json` is not updated (the tool was never called). The next scheduled run uses the same fixed `window_hours` window regardless — missed activity falls into the gap and is skipped in the newsletter (still visible in conversation archives). Dynamic windows are a Phase 3 consideration.
- **Per-target delivery failure:** targets are attempted independently; failures are logged but don't block other targets. Archive file is always written first, so there's always a local record.
- **All targets fail:** `last_run.json` is still advanced — the archive is the canonical record; re-delivery isn't worth the complexity.
- **Empty window (nothing to report):** SKILL.md body instructs the agent to still call `newsletter_publish(markdown="", has_content=False)` as the final step. The tool writes a minimal archive stub, skips all delivery, advances `last_run.json`. We get a durable "ran and found nothing" record without spamming inboxes.
- **Window calculation (Phase 1):** always a fixed `[now - window_hours, now)` in UTC. `last_run.json` is written and advanced, but the window is NOT derived from it — running more often than the schedule (e.g., `!newsletter` interactively) simply looks back the same 24h. A missed scheduled run does not retroactively extend the next run's window, so the gap's activity is skipped in the newsletter (still visible in the archive files and conversation transcripts). Dynamic "since last run" windows are a Phase 3 consideration.
- **First-ever run (`last_run.json` absent):** same as above — window is `[now - window_hours, now)`.

## Config shape

Skill-namespaced config only; no global config dataclass.

```
config.skills.newsletter = {
  "window_hours": 24,
  "email_enabled": false,
  "email_recipients": [],
  "email_subject_prefix": "[decafclaw newsletter]",
  "vault_page_enabled": true,
  "vault_folder": "agent/journal/newsletters"
}
```

Env var overrides: `NEWSLETTER_WINDOW_HOURS`, `NEWSLETTER_EMAIL_ENABLED`,
`NEWSLETTER_EMAIL_RECIPIENTS`, `NEWSLETTER_EMAIL_SUBJECT_PREFIX`,
`NEWSLETTER_VAULT_PAGE_ENABLED`, `NEWSLETTER_VAULT_FOLDER`.

## Storage layout

- `workspace/newsletter/archive/YYYY-MM-DD.md` — canonical local archive per scheduled run.
- `workspace/newsletter/last_run.json` — publisher state (`last_run_utc`, `window_end_utc`).
- `{vault_root}/{agent_folder}/journal/newsletters/YYYY-MM-DD.md` — vault-page deliveries (when that target is enabled).

## Testing

### Unit

- `newsletter_list_scheduled_activity` — fixture dir of fake `schedule-*.jsonl` files; assert correct filtering by window, skill-name extraction, final-message extraction, vault-pages-touched detection.
- `newsletter_list_vault_changes` — fixture vault with known changes; assert correct window + action + path reporting.
- `newsletter_publish` (scheduled branch) — mocked `mail.py`, temp workspace + vault; assert archive write + per-target delivery + `last_run.json` advancement.
- `newsletter_publish` (interactive branch) — assert no archive, no delivery, no state change; markdown returned as tool result.
- `newsletter_publish` (scheduled + `has_content=False`) — assert archive stub written, no delivery dispatched, `last_run.json` advances.
- `SkillConfig` loading — defaults, env override, `config.skills.newsletter` dict override.
- Edge cases: empty window, first-ever run, all-targets-disabled, single-target-fail isolation.

### Integration-ish

- Full run of the scheduled skill with a stub LLM that invokes the three tools in sequence. Verify a newsletter lands in `workspace/newsletter/archive/` and the configured vault-page target file exists.

## Phase 2 (future)

- Mattermost channel delivery — probably lands alongside a reusable `mattermost_channel` notification adapter in core, since both concerns need the same "post to MM channel" primitive.

## Phase 3 (future)

- Hourly / weekly cadences (multiple publisher skill instances with different schedules + window sizes).
- Time-range arguments on `!newsletter` (e.g., `!newsletter since yesterday`, `!newsletter 48h`).
- Topic-based sections / personalization hints.
- Opt-in cadence frontmatter on contributing skills (if/when we want skill authors to influence categorization — currently nothing in Phase 1 needs this).

## Open items to resolve in plan

- Exact mechanism for `newsletter_list_vault_changes`: `git log` vs filesystem mtime scan. Dependent on whether the vault is always a git repo (likely yes in practice, but not guaranteed).
- Final env var naming for `SkillConfig` overrides (e.g., `NEWSLETTER_EMAIL_ENABLED`, `NEWSLETTER_EMAIL_RECIPIENTS`).
- Conflict handling when multiple newsletters run on the same calendar day (suffix policy: `-1`, `-2`, etc., vs timestamp suffix).
- Whether `!newsletter` and the scheduled run use identical SKILL.md body with ctx-based branching, or the skill exposes two slightly different prompt surfaces (plan will decide based on how `context:` fork vs inline interacts with user-invocable commands).
