# Schedule Discovery & Skill System Research

## 1. Schedule Discovery and Execution Flow

**File discovery:** `src/decafclaw/schedules.py:99–170` (`discover_schedules()`)

### Admin & Workspace Files
- **Admin source** (precedence 1): `config.agent_path / "schedules"` (line 106–108)
- **Workspace source** (precedence 2): `config.workspace_path / "schedules"` (line 106–108)
- Both scanned for `*.md` files via `glob("*.md")` (line 112)
- Parsed via `parse_schedule_file()` (line 38–93)

### Skill-as-Schedule Discovery
Skills are discovered and filtered if they have a `schedule` field in `SKILL.md` frontmatter (line 121–168):
- **Scan order (precedence):** admin > extra > bundled (line 137–141)
- Scans via `_iter_skill_dirs()` (line 144) which handles both directory-of-skills and single-skill entries (line 240–257)
- Each skill's `SKILL.md` checked at `skill_dir / "SKILL.md"` (line 145)
- If `skill.schedule` is non-empty and valid cron, converted to `ScheduleTask` (line 157–168)
- **File-based schedules override skill schedules** (line 154–156): if name exists in `tasks_by_name`, skill is skipped

### Frontmatter Fields Parsed
`parse_schedule_file()` accepts (line 38–93):
- `schedule` (required): cron expression; validated via `croniter.is_valid()` (line 51–58)
- `channel`: dispatch channel (line 86, default "")
- `enabled`: boolean; coerced from YAML string (line 61–63)
- `model`/`effort`: named model config (line 88, merged as `model`)
- `allowed-tools`: comma-separated tool names + scoped shell patterns (line 65–70)
- `required-skills`: list of skill names to pre-activate (line 72–74)
- `email-recipients`: list of emails/domain patterns (line 76–78)

### Cron & Due Detection
- `is_due()` (line 212–220): compares last-run timestamp to `croniter.get_next(float)` using UTC
- Last-run tracked in `.schedule_last_run/{safe_task_name}` (line 185–191)
- Never-run tasks return `True` immediately (line 215–216)

### Execution Entry Point
`run_schedule_task()` (line 226–328):
- Routes through `ConversationManager.enqueue_turn()` with `TurnKind.SCHEDULED_TASK` (line 293–302)
- Creates conv_id as `"schedule-{task.name}-{YYYYMMDD-HHMMSS}"` (line 234)
- Invokes `setup_schedule_ctx()` closure (line 257–283) to apply per-task settings:
  - Override `ctx.active_model` if `task.model` set (line 259–260)
  - Set `ctx.tools.allowed` and `ctx.tools.preapproved` (line 261–263)
  - Set `ctx.tools.preapproved_shell_patterns` if scoped patterns exist (line 264–265)
  - Pre-activate required skills via `activate_skill_internal()` (line 272–283)
  - Override `ctx.channel_id` and `ctx.channel_name` (line 269–270)
- Calls `substitute_body()` to expand variables in task body (line 289)
- Returns dict with `task_name`, `channel`, `response`, `is_ok`, `context_id` (line 309–315)

**Timer Loop** `run_schedule_timer()` (line 358–425):
- Polls every 60s (default, configurable via `poll_interval`, line 366)
- Discovers tasks on each tick (line 373) so edits take effect without restart
- Skips disabled tasks (line 378–379)
- Prevents concurrent runs of same task (line 380–382)
- Writes last-run timestamp before execution (line 391)

---

## 2. Skill Frontmatter Fields

**Parsing:** `src/decafclaw/skills/__init__.py:69–128` (`parse_skill_md()`)

### Fields Parsed from SKILL.md
- `name` (required): skill identifier
- `description` (required): one-line description
- `user-invocable`: boolean; controls visibility in CLI/UI (line 117, default `True`)
- `allowed-tools`: comma-separated; parsed into tool names + shell patterns (line 107–108)
- `required-skills`: list of skills this depends on (line 123)
- `model`/`effort`: named model config; inherited by scheduled runs (line 122)
- `always-loaded`: boolean; trusted tiers only (line 124); blocked for workspace tier (line 313–319)
- `enabled`: boolean; can disable via frontmatter (line 126); schedule discovery respects this (line 149)
- `schedule`: cron expression; triggers skill-as-schedule discovery (line 125)
- `context`: execution mode — "inline" (default) or "fork" (line 120)
- `argument-hint`: CLI hint text (line 121)
- `requires`: dict with `env` key listing required env vars (line 102–103)
- `auto-approve`: boolean; trusted tiers only; blocks workspace (line 306–312)

### Bundled Skills with `schedule` Field
- `dream` (line 55 in tests): `schedule: "0 3 * * *"`, `effort: strong`, `required-skills: [vault]`
- `garden` (line 55): `schedule: "0 3 * * 0"`, `effort: strong`, `required-skills: [vault]`
- `newsletter` (line 54): `schedule: "0 7 * * *"`, `allowed-tools: [newsletter_*, current_time]`, `required-skills: [newsletter]`
- `ingest`: no schedule (user-invocable only); `allowed-tools: [tabstack_*, web_fetch, workspace_*, vault_*]`

### Field Consumption Downstream
- `schedule` consumed at discovery (line 149 in schedules.py:149) — non-empty + valid cron → ScheduleTask
- `model` applied in `run_schedule_task()` at setup time (line 259–260 in schedules.py)
- `allowed-tools` → `task.allowed_tools` → `ctx.tools.allowed` (line 240–243 in schedules.py)
- `required-skills` → `task.required_skills` → pre-activated via `activate_skill_internal()` (line 272–283 in schedules.py)
- `enabled` checked in timer loop (line 378 in schedules.py:378)
- `trust_tier` recorded at discovery (line 300 in skills/__init__.py) — governs `auto-approve` / `always-loaded` validation

---

## 3. Scheduled Task → Tool Availability

**Setup phase in `run_schedule_task()`** (line 257–283 in schedules.py):

### Tool Allowlist Assembly
1. If `task.allowed_tools` or `task.shell_patterns` present:
   - `ctx.tools.allowed` ← `set(task.allowed_tools)` (line 240)
   - `ctx.tools.preapproved` ← `set(task.allowed_tools)` (line 243; pre-approves without confirmation prompt)
   - If shell patterns exist, ensures `"shell"` in `allowed` set (line 241–242)
   - Shell patterns stored in `ctx.tools.preapproved_shell_patterns` (line 265)

2. If `task.required_skills` present (line 272–283):
   - Looks up skill in `config.discovered_skills` (line 273)
   - Calls `activate_skill_internal()` for each (line 280)
   - Failure logged but doesn't block task (line 281–283)

### Field-to-Context Mapping
- Schedule file `allowed-tools` → parsed to tools + patterns (line 65–70 in schedules.py) → `ScheduleTask.allowed_tools` / `.shell_patterns`
- Skill `allowed-tools` → parsed same way (line 107–108 in skills/__init__.py) → propagated to `ScheduleTask` at discovery (line 165–167 in schedules.py)
- Pattern expansion: `$SKILL_DIR` replaced with task's parent directory (line 248–250 in schedules.py)

---

## 4. Web UI Sidebar Tabs

**Pattern from existing tabs:**

### Vault Sidebar (`src/decafclaw/web/static/components/vault-sidebar.js`)
- **API call:** `fetch(/api/vault?folder=...)` (line 62–64)
- **Refresh trigger:** Fires on tab activation (`active` property change) and on `vault-changed` event (line 36–39)
- **Properties:** `_wikiPages`, `_vaultFolders`, `_vaultView` (browse/recent)
- **Registration:** Imported in conversation-sidebar and shown conditionally in render (line 4 in conversation-sidebar.js)

### Files Sidebar (`src/decafclaw/web/static/components/files-sidebar.js`)
- **API calls:** `/api/workspace` (browse), `/api/workspace/recent` (recent files) (line 93–94)
- **Refresh trigger:** Tab activation + `turn-complete` event (line 60, 76–77)
- **Properties:** `_files`, `_folders`, `_view`, `_currentFolder`, `_loading`
- **Event emission:** Dispatches custom events for deletions (line 46–50)
- **Silent refresh:** Can fetch without showing loading spinner (line 82–85 opts parameter)

### Conversation Sidebar (`src/decafclaw/web/static/components/conversation-sidebar.js`)
- **Data source:** Internal `store` object (line 9, 32–33)
- **Refresh:** Via store listener (line 64–80)
- **Sections:** Active (default), `_archived`, `_system` (line 38)
- **System conversations:** Populated from `store.systemConversations` (line 80)
- **Registration:** Parent component receives all convos via app event bus

### Common Pattern
1. Component has `active: Boolean` property tracking tab visibility
2. Triggers `updated()` on `active` change to fetch data (line 75 in files-sidebar.js)
3. Calls `/api/...` endpoint, parses JSON response
4. Listens for domain events (`vault-changed`, `turn-complete`) to refresh
5. Renders with loading state; silent refresh option to avoid UI flicker

---

## 5. Schedule Management Surface

**No existing HTTP API or web UI surface for schedules today.**

### What Exists
- **HTTP routes:** No `/api/schedules` or similar endpoint (line 1815–1850 in http_server.py list all routes; schedules not included)
- **Web UI:** No schedule management tab in sidebar (conversation-sidebar.js shows `conversations`, `vault`, `files`, `context-inspector`)
- **Schedule listing:** Only in backend via `discover_schedules()` (line 373 in schedules.py)
- **Conversation type inference:** Schedule conversations detected via conv_id pattern match (line 19 in conversations.py); type = "schedule", title = "Schedule: {name} [{ts}]"
- **Config file listing:** Includes schedules in editable config list (line 1533–1534 in http_server.py), alongside admin/workspace config files

### Indirection in System Conversations List
- `list_system_conversations()` (line 61–97 in conversations.py) discovers archived schedule conversations by filename pattern (line 19–20)
- Pattern: `^schedule-(.+)-(\d{8}-\d{6})$` → extracts task name and timestamp
- **No enable/disable, no edit, no trigger mechanism** via HTTP

---

## 6. Skill Installation & Discovery

**Discovery:** `src/decafclaw/skills/__init__.py:260–351` (`discover_skills()`)

### Scan Order (Trust Tiers)
1. **Workspace** (lowest trust): `config.workspace_path / "skills"` (line 281)
2. **Admin**: `config.agent_path / "skills"` (line 282)
3. **Bundled**: `_BUNDLED_SKILLS_DIR` = `src/decafclaw/skills/` (line 283)
4. **Extra** (user-configured): `config.extra_skill_paths` entries (line 284)

### Discovery Mechanism
- Each scan entry is polymorphic (line 240–257):
  - If entry path itself has `SKILL.md` at root → treat as single skill, yield it directly
  - Otherwise if directory → yield each immediate subdirectory
- Collision handling: first-found wins (line 341–345); workspace shadows none (workspace is lowest tier)

### Extra Skill Paths Configuration
- Defined in `config.extra_skill_paths` (line 231 in skills/__init__.py)
- Supports `$DECAFCLAW_REPO` and `$CONTRIB` variable expansion (line 207–229):
  - Auto-detect repo root via marker files (line 18–31: `contrib/` + `pyproject.toml`)
  - `$DECAFCLAW_REPO` = repo root; `$CONTRIB` = `$DECAFCLAW_REPO/contrib` (line 225–228)
  - Relative paths anchored to `config.agent_path` (line 235)

### Trust Tier Restrictions
- `auto-approve` and `always-loaded` blocked for workspace tier (line 306–319)
- Config entry `config.skills_always_loaded` can force trusted tiers to load (line 324–332)
- Missing required env vars skip skill entirely (line 335–338)

### Installation Flow
- **No dedicated "install" command today** — skills are discovered from directories
- Bundled: baked into package (`src/decafclaw/skills/`)
- Admin: placed manually in `data/{agent_id}/skills/`
- Extra: paths configured in `config.extra_skill_paths`, can point to:
  - Shared directories (e.g., `$CONTRIB/skills/{name}`)
  - Isolated skill dirs on disk
- **Workspace skills:** Agent-writable at `data/{agent_id}/workspace/skills/` (security boundary; no auto-approve/always-loaded allowed)

### Loader Implementation
- `discover_skills()` (line 260–351): main entry point
- `_iter_skill_dirs()` (line 240–257): polymorphic directory iterator
- `_resolve_extra_skill_paths()` (line 207–237): expand variables and anchoring
- `parse_skill_md()` (line 69–128): YAML frontmatter parser
- `build_skill_tool_owners()` (line 354–388): indexes skill tool names by importing `tools.py` for each skill
- `build_catalog_text()` (line 391–428): generates skill listing for system prompt

---

## Summary

**Schedule discovery** is symmetric: file-based (admin > workspace) and skill-based (admin > extra > bundled), with file-based always winning. Both route through `run_schedule_task()` which applies per-task tool allowlists and pre-activates required skills via a context setup closure. **No HTTP API or UI surface exists** for schedule management; scheduled conversations are inferred from conv_id pattern. **Skill installation** is purely discovery-based: no install flow, just directory scanning with variable expansion for extra paths.
