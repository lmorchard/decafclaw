# Decafclaw Codebase Research — Kindle Skill #375

## Q1. Bundled skill anatomy: Newsletter and Ingest

**Newsletter skill** (`src/decafclaw/skills/newsletter/`):

- **SKILL.md** (lines 1–65): Frontmatter fields used:
  - `name: newsletter`
  - `description: ...`
  - `schedule: "0 7 * * *"` (cron, daily 7am)
  - `user-invocable: true`
  - `context: inline`
  - `allowed-tools: newsletter_list_scheduled_activity, newsletter_list_vault_changes, newsletter_publish, current_time`
  - `required-skills: [newsletter]`

- **tools.py** (lines 1–250+):
  - `SkillConfig` dataclass (lines 19–42): `window_hours`, `email_enabled`, `email_recipients`, `email_subject_prefix`, `vault_page_enabled`, `vault_folder`. Env aliases: `NEWSLETTER_*` prefix.
  - `init(config, skill_config: SkillConfig)` (lines 44–48): signature matches bundled skill loader contract. Stores globals `_config`, `_skill_config`.
  - Tool functions: `newsletter_list_scheduled_activity(ctx, hours=None, window="")` (async, line 194), `newsletter_list_vault_changes(ctx, hours=None, window="")` (async, presumed similar pattern), `newsletter_publish(...)` (presumed exists).
  - Exports: `TOOL_DEFINITIONS` dict mapping tool names to functions (likely lines ~250+).
  - Helper: `_parse_window(spec)` parses "7d", "48h", "2w" to hours; croniter used implicitly for validation.

**Ingest skill** (`src/decafclaw/skills/ingest/`):

- **SKILL.md only** (no tools.py):
  - Frontmatter:
    - `name: ingest`
    - `description: Fetch a URL, workspace file, or attachment...`
    - `user-invocable: true`
    - `context: inline`
    - `required-skills: [tabstack]`
    - `allowed-tools: tabstack_extract_markdown, web_fetch, workspace_read, list_attachments, get_attachment, vault_search, vault_read, vault_write, vault_list, vault_backlinks, current_time`
  - No native tools — imported via `required-skills: [tabstack]`. Skill body (lines 12–97) is the full prompt instruction text, no Python tool wrapper.

**Key pattern**: Bundled skills in `src/decafclaw/skills/{name}/` contain SKILL.md (always) + optional tools.py. Skill loader parses SKILL.md frontmatter for metadata, imports tools.py if present, and calls `init(config, skill_config)` to initialize. `SkillConfig` dataclass in tools.py defines per-skill config with env_alias metadata for environment variable lookup. No `get_tools` dynamic provider observed in newsletter/ingest.

---

## Q2. Scheduled skill plumbing: cron → 60s poll loop → `run_schedule_task`

**Discovery** (`schedules.py:99–160`):
- `discover_schedules(config)` rescans on each poll tick. Searches two sources in precedence order:
  1. Admin: `config.agent_path / "schedules"` (flat .md files in root)
  2. Workspace: `config.workspace_path / "schedules"` (flat .md files, agent-writable)
  3. Bundled and admin skills: iterates `_BUNDLED_SKILLS_DIR` and `config.agent_path / "skills"`, reads SKILL.md for each skill with `schedule:` frontmatter (lines 121–158).
- Returns list of `ScheduleTask` dataclass (lines 18–36): name, schedule (5-field cron), body, source ("admin"/"workspace"/"bundled"), path, enabled, model, allowed_tools, shell_patterns, required_skills, email_recipients.

**Parsing**:
- Schedule files: `parse_schedule_file(path)` (lines 38–93) splits frontmatter via `_split_frontmatter`, extracts `schedule:` field, validates with `croniter.is_valid()`.
- Skill SKILL.md: `parse_skill_md()` in `skills/__init__.py:41–100` extracts `schedule:` field (line 97).

**Poll loop** (`schedules.py:340–416`):
- `schedule_timer(config, event_bus, manager, interval=60, shutdown_event=None, on_result=None)` (line 340+).
- `run_polling_loop(interval=60, ...)` at line 403 calls `_tick()` every 60s.
- Per tick: `discover_schedules()`, check each task via `is_due(config, task)` (line 373, uses croniter), dispatch via `run_schedule_task()` (line 382).

**Task execution** (`run_schedule_task`, lines 216–295):
- Conv ID: `schedule-{task.name}-{YYYYMMDD-HHMMSS}` (line 224). Conv ID naming allows newsletter to parse scheduled task conversations.
- Setup via `setup_schedule_ctx(ctx)` closure (lines 247–273):
  - Applies per-task model override (line 250)
  - Restricts tools to `task.allowed_tools` if specified (lines 251–253)
  - Sets shell patterns (lines 254–255)
  - Overrides email recipients (line 257)
  - Pre-activates required skills via `activate_skill_internal()` (lines 262–273)
- Preamble + body substitution (lines 275–279): `build_task_preamble()` + `substitute_body()` from `polling` and `commands`.
- Dispatches to manager via `enqueue_turn(conv_id, kind=TurnKind.SCHEDULED_TASK, prompt, context_setup=setup_schedule_ctx)` (lines 283–292).

**Bundled skill scheduling integration**: Skills with `schedule:` frontmatter discovered in `discover_schedules()` (line 139 checks `skill.schedule`). Skill body becomes the task prompt (line 150 sets `body=skill.body`). Skill's tools are auto-activated via `required_skills` in the task setup (lines 243, 262–273).

---

## Q3. Vault page write conventions: frontmatter + body serialization

**Frontmatter parsing** (`frontmatter.py:19–55`):
- `parse_frontmatter(text)` (line 19): regex `_FRONTMATTER_RE` (line 16: `\A---\n(.*?)\n---\n`, re.DOTALL) extracts YAML block, returns `(dict, body_text)`. Falls back to `({}, text)` on parse error.
- `serialize_frontmatter(metadata, body)` (line 46): YAML dumps metadata, wraps with `---` delimiters, returns full markdown.

**Vault write** (`skills/vault/tools.py:300–325+`):
- `tool_vault_write(ctx, page: str, content: str)` (async, line 300):
  - Validates page name via `_safe_write_path()` (line 303).
  - Checks user write permissions via `_check_user_write_allowed()` (line 310).
  - Runs confirmation gate if needed (lines 311–318).
  - Writes file directly (presumed to call path.write_text()).
- Frontmatter fields used:
  - Ingest skill expects `tags: [ingested, ...]`, `summary: ...` in SKILL.md step 5 (lines 71–77).
  - `frontmatter.py:get_frontmatter_field()` (line 58) handles type coercion for `importance` (float [0,1]), `keywords`/`tags` (list[str]), `summary` (str).
  - `build_composite_text(metadata, body)` (line 88) prepends summary, keywords, tags for embedding indexing.

**Agent-managed sections vs user-preserved regions**: No existing convention documented. Writes are full-page overwrites; no per-section update mechanism detected. SKILL.md describes writing to `agent/pages/` folder to avoid collision with user pages (line 57).

**Vault journal** (`vault/tools.py:563`):
- `tool_vault_journal_append(ctx, tags: list[str], content: str)` writes daily entries under `vault_agent_journal_dir`.

---

## Q4. SkillConfig resolution and persistence

**Config loading** (`config.py:88–145`):
- `load_sub_config(dc_class, json_data, env_prefix, env_aliases=None)` (line 88):
  1. Env var: `{ENV_PREFIX}_{FIELD_NAME}` (line 115)
  2. Env alias from field metadata `env_alias` or dict (line 120)
  3. JSON data `json_data[field_name]` (line 126)
  4. Dataclass default (line 144)
  - Nested dataclass recursion (lines 130–137): derives nested prefix `{PREFIX}_{FIELD_NAME}`.
  - Returns instantiated `dc_class(**kwargs)` (line 145).

**Skill config location**:
- Resolved at skill activation. Root config has `skills: dict[str, dict[str, Any]]` (line 163 in Config dataclass). Per-skill config loaded into `config.skills[skill_name]` dict via `load_sub_config(SkillConfig, config.skills.get(skill_name, {}), env_prefix="")` (presumed in skill loader, exact location not traced).
- Admin-level config at `data/{agent_id}/config.json` under `skills` key. Env vars with `SKILL_*` prefix or field-specific aliases (e.g., `NEWSLETTER_WINDOW_HOURS`, `TABSTACK_API_KEY`) override JSON.

**Fresh install defaults**: Dataclass field defaults apply when no JSON entry and no env var set. Newsletter defaults: `window_hours=24`, `email_enabled=False`, `vault_page_enabled=True` (lines 21–42).

**`init()` signature**: `init(config, skill_config: SkillConfig)` — receives global Config + resolved SkillConfig. Called once per skill activation, initializes module-level state (`_config`, `_skill_config` in newsletter).

---

## Q5. Notification surfaces for scheduled-skill outcomes

**Notification record** (`notifications.py:29–42`):
- `NotificationRecord` dataclass: `id`, `timestamp` (ISO-8601 UTC), `category` ("heartbeat", "schedule", "background", ...), `title`, `priority` ("low"/"normal"/"high"), `body`, `link`, `conv_id`.
- `to_dict()` / `from_dict()` for JSONL serialization.

**Notification emission** (inferred from `vault_page.py:1–95`):
- Event-driven: publisher calls `ctx.publish("notification_created", record=NotificationRecord(...))` (presumed, based on channel subscription at line 1).
- No direct tool function observed; agent-side emission likely via custom handler or event API.

**Channels** (`notification_channels/`):
1. **Vault page** (`vault_page.py`):
   - Daily rollup under `vault_root / configured_folder / YYYY-MM-DD.md`.
   - Appends one markdown section per notification (subheading + metadata block + body).
   - Per-path lock serializes concurrent appends (line 45).
   - Folder validation (line 64): relative path, no `..`, must stay in vault.

2. **Email** (`email.py`):
   - Sends email to configured recipients.

3. **Mattermost DM** (`mattermost_dm.py`):
   - Sends direct message.

**Scheduled-skill outcome surface**:
- Newsletter calls `newsletter_publish()` → returns ToolResult. No explicit notification emission observed (may be implicit in vault journal append or email send).
- `run_schedule_task()` captures result text (line 293) and checks `is_heartbeat_ok(result_text)` (line 294) for status classification.

---

## Q6. HTTP client / scraping patterns and YouTube/transcript work

**Current HTTP clients** (outside tests):
- **`httpx`** (async HTTP): used in:
  - `mattermost.py:14` — MM API calls
  - `embeddings.py:10` — embedding model requests
  - `tools/http_tools.py:9` — web_fetch tool
  - `tools/heartbeat_tools.py:6` — heartbeat HTTP checks
  - `tools/core.py:6` — HTTP utilities
  - `llm/providers/*` — LLM API calls
- **`tabstack`** (web extraction/automation):
  - Imported in `skills/tabstack/tools.py:7` from `tabstack` package.
  - Initialized as AsyncTabstack client in `init(config, skill_config)` (line 24–31).
  - Tools: `tabstack_extract_markdown()`, `tabstack_extract_json()`, `tabstack_generate()`, `tabstack_automate()`, `tabstack_research()` (lines 43–116).
  - Used by `ingest` skill (SKILL.md:8, `required-skills: [tabstack]`).

**No existing authenticated HTML fetch pattern**: httpx client used raw; no cookie jar or session persistence observed. Tabstack handles auth/cookies internally (black-box).

**YouTube / transcript / issue #374 work**:
- Git log shows recent ingest-related commits:
  - `069fcdf feat(ingest): forward user args through fetch.sh for backfill (#467)`
  - `c0cd581 feat: ingest skill — one-shot URL/file/attachment ingestion into the vault (#290)`
  - No YouTube-specific or transcript commits detected.
- Issue #374 not directly referenced in logs; issue title unknown from available data.
- No `youtube`, `transcript` Python modules found in codebase (grep returned zero matches in src/decafclaw).

---

## Summary Table

| Q | Component | File:Lines | Key Contract |
|---|-----------|-----------|--------------|
| Q1 | Newsletter SKILL.md | `skills/newsletter/SKILL.md:1–65` | Scheduled (cron `0 7 * * *`), user-invocable, 4 allowed tools |
| Q1 | Newsletter tools | `skills/newsletter/tools.py:19–48` | `SkillConfig` + `init(config, skill_config)` |
| Q1 | Ingest SKILL.md | `skills/ingest/SKILL.md:1–97` | User-invocable, no native tools, requires tabstack |
| Q2 | Discovery | `schedules.py:99–160` | `discover_schedules()` rescans admin/workspace/.md + bundled skills SKILL.md |
| Q2 | Poll loop | `schedules.py:340–416` | `schedule_timer()` calls `_tick()` every 60s, checks `is_due()`, dispatches via `run_schedule_task()` |
| Q2 | Task exec | `schedules.py:216–295` | Conv ID `schedule-{name}-{timestamp}`, pre-activates skills, applies per-task model/tools |
| Q3 | Parse FM | `frontmatter.py:19–55` | Regex extract YAML, serialize with `---` delimiters |
| Q3 | Vault write | `skills/vault/tools.py:300` | Full-page overwrite; expects YAML frontmatter with `tags`, `summary` |
| Q4 | Config load | `config.py:88–145` | Env → JSON → dataclass default; nested recursion for sub-configs |
| Q4 | Skill activation | `skills/__init__.py:41–100` | `parse_skill_md()` reads SKILL.md, `init()` called by loader |
| Q5 | Notifications | `notifications.py:29–42` | `NotificationRecord` JSONL, event-driven subscription |
| Q5 | Vault channel | `notification_channels/vault_page.py:1–95` | Daily MD pages, per-path lock, folder sandboxing |
| Q6 | HTTP | `tools/http_tools.py`, `llm/providers/*` | httpx for sync calls; async via asyncio.to_thread |
| Q6 | Web automation | `skills/tabstack/tools.py:7–116` | AsyncTabstack client, extract/automate/research tools |

