# Separate scheduled prompts from skills — Spec

**Goal:** Decouple schedule discovery from skill frontmatter. Skills ship a `SCHEDULE.md` sidecar with default state; user edits land in a copy-on-write overlay under the agent dir. Surface schedule management in a new sidebar tab so the user can list, toggle, edit, and reset entries without touching source.

**Source:** [issue #254](https://github.com/lmorchard/decafclaw/issues/254) + brainstorm 2026-05-19.

## Current state

Schedule discovery has two parallel code paths that converge on the same `ScheduleTask` runtime:

- **File-based** (`schedules.py:99–170`, `discover_schedules()`): admin `data/{agent_id}/schedules/*.md` (precedence 1) and workspace `workspace/schedules/*.md` (precedence 2). Parsed via `parse_schedule_file()` (`schedules.py:38–93`) which accepts `schedule`, `channel`, `enabled`, `model`/`effort`, `allowed-tools`, `required-skills`, `email-recipients`.
- **Skill-as-schedule** (`schedules.py:121–168`, inline within `discover_schedules`): scans bundled / admin / extra-path skill dirs, reads each SKILL.md, builds a `ScheduleTask` when `skill.schedule` is non-empty + valid cron. File-based wins on name collision (`schedules.py:154–156`).

Both paths feed `run_schedule_task()` (`schedules.py:226–328`) which applies per-task `allowed_tools`, `preapproved`, `shell_patterns`, `model`, and pre-activates `required_skills` via `setup_schedule_ctx()` (`schedules.py:257–283`). So the runtime mechanism is symmetric — only discovery diverges.

Skill frontmatter (`skills/__init__.py:69–128`) currently carries `schedule` (`line 125`) and `enabled` (`line 126`) fields that *only* matter for scheduling. Three bundled skills depend on this today: `dream` (`0 3 * * *`), `garden` (`0 3 * * 0`), `newsletter` (`0 7 * * *`).

The web UI has no schedule management surface — no `/api/schedules*` endpoint, no sidebar tab. Sidebar tabs follow a well-established pattern (`research.md` §4): a Lit component with an `active` property, `/api/...` fetch on activate, domain-event listeners for refresh, registered in `conversation-sidebar.js`.

## Desired end state

### Discovery layout

- **Skill SCHEDULE.md sidecar.** Any discovered skill (bundled / admin / contrib) may ship a `SCHEDULE.md` alongside `SKILL.md`. It uses the same frontmatter shape as a standalone schedule file (`schedule`, `enabled`, `channel`, `model`/`effort`, `allowed-tools`, `required-skills`, `email-recipients`) plus a markdown body that is the prompt.
- **Bundled + admin tier:** SCHEDULE.md is honored verbatim. If `enabled: true` (default), the task is active on next discovery tick.
- **Contrib tier (`extra_skill_paths`):** SCHEDULE.md is discovered but treated as `enabled: false` regardless of what the file says. User opts in via the UI (which writes an overlay with `enabled: true`).
- **Standalone schedule files** still supported: `data/{agent_id}/schedules/*.md` and `workspace/schedules/*.md`. Unchanged from today.
- **Workspace skill SCHEDULE.md** is disallowed (parallels the existing rule that workspace skills can't self-schedule).

### Overlay (copy-on-write)

- **Overlay store:** `data/{agent_id}/schedules/{name}.md` — a full markdown file that shadows any same-named SCHEDULE.md from a skill source tier. Living next to standalone admin schedules.
- **Resolution:** when discovery finds a SCHEDULE.md named `dream` and also a file at `data/{agent_id}/schedules/dream.md`, the overlay wins fully — original is not merged, just shadowed.
- **Reset to defaults:** delete the overlay file.
- **First edit semantics:** the UI's "save" action writes the *current effective state* (original + user's pending change) as a full file to the overlay path. After that, the overlay is the authoritative source for that schedule until reset.

### Skill frontmatter cleanup

- Remove `schedule` and `enabled` fields from `parse_skill_md()`. Both only mattered for scheduling.
- Keep `model`/`effort` on SKILL.md — still used as fork context for non-scheduled skill invocations (`research.md` §2 confirms this).
- Migrate bundled skills `dream`, `garden`, `newsletter`: move their `schedule:`/`effort:`/`allowed-tools:`/`required-skills:` from SKILL.md into a new SCHEDULE.md sidecar.

### Schedule discovery code

- Delete the skill-as-schedule branch in `discover_schedules()` (`schedules.py:121–168`).
- Add SCHEDULE.md discovery as a separate function `_discover_skill_schedule_files()` that walks the same skill dirs (`_iter_skill_dirs`) and reads SCHEDULE.md instead of inspecting SKILL.md. Returns the same `ScheduleTask` shape.
- Apply tier-based default-enabled adjustment for SCHEDULE.md *before* overlay resolution: contrib tier originals are forced to `enabled: false` regardless of file contents. Bundled / admin SCHEDULE.md uses the file's own `enabled` value.
- Apply overlay resolution: an "overlay" is simply a file at `data/{agent_id}/schedules/{name}.md` with the same name as a discovered SCHEDULE.md. The admin standalone file wins fully — original SCHEDULE.md is shadowed (no field-level merge).
- **Final precedence** (after the refactor, highest wins on name collision):
  1. `data/{agent_id}/schedules/{name}.md` (admin standalone — also acts as overlay for skill SCHEDULE.md)
  2. `workspace/schedules/{name}.md` (workspace standalone, agent-written)
  3. Skill SCHEDULE.md (any tier — bundled / admin / contrib)

  This collapses the old admin/workspace/skill precedence into a single rule: any file in a standalone schedules dir beats any skill SCHEDULE.md. The "overlay" concept is just a special case of admin standalone.

### HTTP API

New routes under `/api/schedules`:

- `GET /api/schedules` → list of `{name, source_tier, source_path, has_overlay, enabled, schedule, channel, model, allowed_tools, required_skills, body, next_run_iso, last_run_iso}`. `source_tier` ∈ {`bundled`, `admin`, `extra`, `workspace`} (matches the existing `skills/__init__.py` tier vocabulary; "extra" = contrib / `extra_skill_paths`); `has_overlay` true iff source is a skill SCHEDULE.md *and* an admin file shadows it.
- `PUT /api/schedules/{name}` → body `{enabled?, schedule?, body?, channel?, allowed_tools?, required_skills?, model?}`. Writes the full effective state (current resolved values + patch) to `data/{agent_id}/schedules/{name}.md`. If the source was a skill SCHEDULE.md, this creates the overlay. If the source was already an admin standalone file, this is an in-place edit. Workspace-tier entries return 403 (agent-owned, not user-editable in v1). Returns the updated entry.
- `DELETE /api/schedules/{name}/overlay` → only valid when `has_overlay: true` (overlay is shadowing a skill SCHEDULE.md). Deletes the admin file, reverts to the skill's SCHEDULE.md value. 404 if no overlay or if source is admin/workspace standalone (no original to revert to). Returns the post-reset entry.

Existing `enqueue_turn`-style "run now" is out of scope (see What we're NOT doing).

### Web UI: schedules sidebar tab

New Lit component `schedules-sidebar.js` registered in `conversation-sidebar.js` alongside `vault-sidebar`, `files-sidebar`, conversation list, context-inspector. Follows the established pattern (see `research.md` §4):

- `active` property triggers `/api/schedules` fetch.
- Listens for a `schedules-changed` event for refresh after edits.
- List view: name, source tier badge (bundled / admin / contrib / workspace), "overridden" marker when `has_overlay: true`, enabled toggle, next-run timestamp, expand-to-edit affordance.
- Edit panel (v1 fields only): enabled toggle, cron string field, channel text field, prompt body textarea, save / cancel buttons. "Reset to defaults" button visible when `has_overlay: true`. Workspace-tier entries render read-only.
- Fields the overlay round-trips but the v1 UI does not expose for editing: `allowed_tools`, `required_skills`, `model`. They're preserved verbatim from the original SCHEDULE.md so future edits don't drop them.
- No "create new schedule" affordance in v1 (see What we're NOT doing).

### Test changes

- Update bundled skill SKILL.md fixtures + tests to drop the `schedule` field. Migrate dream/garden/newsletter test expectations to assert SCHEDULE.md discovery.
- Delete tests that exercise the skill-as-schedule discovery path; replace with tests for SCHEDULE.md discovery + overlay resolution.
- New tests: overlay precedence, contrib tier default-disabled, edits-write-full-overlay, reset-deletes-overlay, name collisions between SCHEDULE.md and admin standalone files.
- HTTP API tests for the three new endpoints (list / put / delete-overlay).
- A live-rendered smoke test via Playwright for the sidebar tab is out of scope; the JS component will be exercised via component tests where the existing test infrastructure supports them, and verified manually in the dev UI before merge.

### Docs

- `docs/schedules.md`: rewrite for the new layout. New sections: skill SCHEDULE.md sidecar, overlay store, sidebar UI.
- `docs/skills.md`: remove the `schedule:` / `enabled:` frontmatter mentions. Add a pointer to docs/schedules.md for skills that ship a SCHEDULE.md.
- `docs/web-ui.md`: add the new sidebar tab.
- `CLAUDE.md`: update the skills bullet that mentions `schedule:` frontmatter; add `schedules-sidebar.js` to key files if it grows beyond a thin wrapper.
- `README.md`: spot-check for any schedule reference.

## Design decisions

- **Decision:** Skills ship SCHEDULE.md sidecars; SCHEDULE.md is discovered as a schedule source.
  - **Why:** Keeps the "skill brings its own schedule" property (so dream/garden/newsletter still work zero-config) without entangling schedule fields with the skill data model. The two artifacts have different lifecycles (skill = always-loadable component; schedule = a specific cron entry the user can disable).
  - **Rejected:** "On first-run installer copies bundled SCHEDULE.md to data/{agent_id}/schedules/" — adds a mandatory setup step and creates an unsightly initial state where every new agent's admin schedules dir is pre-populated.

- **Decision:** Full-file markdown overlay at `data/{agent_id}/schedules/{name}.md`, not a JSON delta sidecar.
  - **Why:** Browsable / editable in any editor; matches the existing schedule-file format so the overlay can be inspected, version-controlled, hand-edited. The "future bundled improvements don't flow through once an overlay exists" footgun is acceptable — explicit user customization should not be silently overwritten by upstream prompt edits.
  - **Rejected:** JSON delta sidecar with field-level merges. Smaller files but introduces a second format and a merge layer; harder to inspect; "what's effective right now" requires merge resolution.

- **Decision:** Contrib (`extra_skill_paths`) SCHEDULE.md is treated as `enabled: false` regardless of file contents.
  - **Why:** Installing a third-party skill should not silently activate a cron job. Different trust posture from bundled / admin (which the operator owns).
  - **Rejected:** Auto-active for contrib too. Symmetric but unsafe — a user installing a contrib skill via `npx skills add` would not expect a new cron job.

- **Decision:** Overlay lives in `data/{agent_id}/schedules/` (admin dir), not in workspace.
  - **Why:** Parallels `skill_permissions.json` which lives outside workspace (per CLAUDE.md: "Permissions at `data/{agent_id}/skill_permissions.json` — outside the workspace, so the agent can't grant itself permission"). The overlay is *user-edited state*, not agent-edited. Agent self-scheduling stays at `workspace/schedules/`.
  - **Rejected:** Overlay in workspace — would let the agent edit its own scheduled prompts via tools, which is a privilege escalation.

- **Decision:** Drop `schedule` and `enabled` from SKILL.md frontmatter. Keep `model`/`effort`.
  - **Why:** Issue mandate. `model`/`effort` is still useful for forked skill invocations outside of scheduling.
  - **Rejected:** Keep `enabled` as a "is this skill discoverable" flag — research didn't show any non-schedule use of `enabled` in current code, so it's dead weight after the migration.

- **Decision:** One PR for refactor + sidebar tab (per user choice in brainstorm).
  - **Why:** The sidebar tab is the smoke test for the overlay store — keeping them together means the API surface gets exercised end-to-end before merge.
  - **Rejected:** Split into two PRs. Cleaner reviews but the overlay store would land without a real consumer.

## Patterns to follow

- **Schedule frontmatter parsing:** mirror `parse_schedule_file()` (`schedules.py:38–93`) — SCHEDULE.md uses identical fields.
- **Skill directory iteration:** reuse `_iter_skill_dirs()` (`schedules.py:240–257` / `skills/__init__.py`) for SCHEDULE.md discovery. Don't reimplement.
- **Trust-tier resolution:** mirror skill tier assignment (`skills/__init__.py:300`) — SCHEDULE.md inherits its skill's tier.
- **Sidebar tab pattern:**
  - Vault sidebar `src/decafclaw/web/static/components/vault-sidebar.js` (lines 36–39 for event-driven refresh, 62–64 for `/api/vault?folder=...` fetch).
  - Files sidebar `src/decafclaw/web/static/components/files-sidebar.js` (lines 60, 76–77 for `turn-complete` listener, 82–85 for silent-refresh option).
  - Registration in `conversation-sidebar.js` next to existing tabs.
- **HTTP route registration:** see existing `/api/workspace/*` and `/api/vault*` in `http_server.py`. Schedules endpoints follow the same Starlette route style.
- **WebSocket message type:** if push notifications for schedule changes are needed in v1, add `SCHEDULES_CHANGED` per the centralized wire-types convention (`message_types.json` + `make gen-message-types`); otherwise rely on activation-driven polling.

## What we're NOT doing

- **"Run now" trigger.** The sidebar will not have a button to fire a schedule immediately. The cron timer + enabled toggle is sufficient for v1. Follow-up issue can be filed if desired.
- **"Create new schedule" UI affordance.** v1 only edits / toggles / resets discovered schedules. Creating from scratch can still be done by dropping a markdown file into the admin schedules dir manually. Add UI later.
- **Schedule history / run log surface.** Scheduled conversations are already discoverable via the system conversations list (`research.md` §5); no new history widget here.
- **Migration tool for agents with existing skill-frontmatter schedules in admin tier.** Bundled skills are the only place `schedule:` lives today. If a user has hand-written SKILL.md `schedule:` entries in `data/{agent_id}/skills/`, they'll need to migrate to SCHEDULE.md sidecars manually. Add a one-liner to release notes.
- **UI editing of `allowed_tools` / `required_skills` / `model`.** These are round-tripped via the overlay but the v1 UI does not show them. Reason: low-frequency edits with complex value shapes; out of scope for the first cut.
- **WebSocket push for schedule changes from server.** v1 uses activation-driven refresh + a `schedules-changed` event dispatched by the sidebar after its own writes. Cross-tab sync via WebSocket is a follow-up.
- **Renaming SCHEDULE.md to something else** (e.g., `schedule.md`, `cron.md`). SCHEDULE.md parallels SKILL.md naming.
- **Generalized "files alongside SKILL.md" pattern** (e.g., DOCS.md, EXAMPLES.md). This change is scoped to SCHEDULE.md only.
- **Eval coverage.** No new LLM behavior is introduced — this is a storage / API / UI refactor. No tool descriptions change. `make eval-tools` should still pass unchanged.

## Open questions

- **Q: Does the overlay capture full `allowed-tools` / `required-skills` / `email-recipients`, or only a stable subset?**
  - **Default:** Full capture — when the overlay is written, the entire effective frontmatter is serialized to the overlay file. Editing those fields via UI is a follow-up (not in v1), but the overlay must round-trip them faithfully so a future edit doesn't drop fields.

- **Q: What's the schedule's "name" when SCHEDULE.md is inside a skill dir?**
  - **Default:** The skill's name (its directory basename / SKILL.md `name` field). The overlay file at `data/{agent_id}/schedules/{skill_name}.md` is the join key. Collision with a same-named standalone admin schedule is a config error — admin standalone wins, log a warning.

- **Q: Should the sidebar tab show standalone admin/workspace schedule files alongside skill SCHEDULE.md entries?**
  - **Default:** Yes. Single unified list with a `source_tier` badge so the user can see whether they're editing a skill-bundled, standalone-admin, workspace, or overlay-shadowed entry. Workspace-tier entries (agent-written) are read-only in the UI for v1 (no overlay support there — they're the agent's domain).

- **Q: WebSocket message type for `schedules-changed`?**
  - **Default:** Not needed for v1. The sidebar self-refreshes after its own writes (dispatches a local DOM event). Cross-tab consistency is a known small gap.
