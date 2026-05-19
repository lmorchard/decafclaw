# Implementation Plan: Separate scheduled prompts from skills

**Goal:** Move schedule discovery from `SKILL.md` frontmatter onto a sibling `SCHEDULE.md`, add a copy-on-write overlay in the admin schedules dir, and surface schedule management in a new sidebar tab.

**Approach:** Skills (any tier) may ship a `SCHEDULE.md` next to `SKILL.md`. Bundled/admin SCHEDULE.md is honored as-is; contrib SCHEDULE.md is forced `enabled: false`. Standalone files in `data/{agent_id}/schedules/` and `workspace/schedules/` continue to work — the admin standalone dir doubles as the overlay store (full-file copies that shadow same-named skill SCHEDULE.md). New `/api/schedules` endpoints expose list/edit/reset. New `<schedules-sidebar>` Lit component is registered as a fourth tab in `<conversation-sidebar>`.

**Tech stack:** Python 3 (Starlette for HTTP), Lit (web UI), pytest, croniter, PyYAML. No new dependencies.

---

## Phase 1: SCHEDULE.md sidecar — discovery refactor + bundled migration

**What this delivers:** End-to-end working scheduled task system using the new layout. `dream`, `garden`, `newsletter` keep firing on their existing cron schedules but are now driven by SCHEDULE.md sidecars instead of SKILL.md frontmatter. Overlay precedence + contrib default-disable are in place.

**TDD opt-in.** Bundled migration is structural (write the files) so it's done after the failing tests are written. The discovery code itself is TDD-driven by the new tests.

**Files:**

- Create: `src/decafclaw/skills/dream/SCHEDULE.md` — `schedule: "0 3 * * *"`, `model: strong`, `required-skills: [vault]`, body = SKILL.md body copied verbatim (preserves current behavior).
- Create: `src/decafclaw/skills/garden/SCHEDULE.md` — `schedule: "0 3 * * 0"`, `model: strong`, `required-skills: [vault]`, body = SKILL.md body copied verbatim.
- Create: `src/decafclaw/skills/newsletter/SCHEDULE.md` — `schedule: "0 7 * * *"`, `allowed-tools: newsletter_list_scheduled_activity, newsletter_list_vault_changes, newsletter_publish, current_time`, `required-skills: [newsletter]`, body = SKILL.md body copied verbatim.
- Modify: `src/decafclaw/skills/dream/SKILL.md` — remove the `schedule: "0 3 * * *"` line from frontmatter. Keep `effort`, `required-skills`, `user-invocable`, `context`. Body unchanged.
- Modify: `src/decafclaw/skills/garden/SKILL.md` — same: remove `schedule:` line. Body unchanged.
- Modify: `src/decafclaw/skills/newsletter/SKILL.md` — same: remove `schedule:` line. Body unchanged.
- Modify: `src/decafclaw/skills/__init__.py` — remove `schedule: str` and `enabled: bool` fields from `SkillInfo` (lines 55–56). Remove their parsing in `parse_skill_md` (lines 125–126). Update the comment at line 55–57 ("schedule cron expression, enabled flag") to remove those.
- Modify: `src/decafclaw/schedules.py`:
  - Replace the skill-as-schedule block (lines 121–168 inside `discover_schedules`) with a new helper that reads SCHEDULE.md sidecars instead of SKILL.md.
  - New helper signature in the module:
    ```python
    def _discover_skill_schedule_files(config) -> dict[str, ScheduleTask]:
        """Walk skill dirs (admin > extra > bundled), read SCHEDULE.md
        sidecars, apply tier-based default-disable for contrib.
        Workspace skill dirs are skipped (workspace skills can't
        self-schedule)."""
    ```
  - Inside `_discover_skill_schedule_files`, when source tier is `"extra"`, force `task.enabled = False` after parse — overriding whatever the SCHEDULE.md file said.
  - `discover_schedules` now: scan admin standalone dir → scan workspace standalone dir → merge SCHEDULE.md sidecars (skills, only where name not already present from standalone dirs). Net precedence: admin standalone > workspace standalone > skill SCHEDULE.md (any tier).
- Modify: `tests/test_schedules.py`:
  - Delete tests `test_discovers_bundled_skill_schedules`, `test_file_schedule_overrides_skill_schedule`, `test_admin_skill_schedules_discovered`, and any other test that asserts behavior of the OLD skill-as-SKILL.md path. Replace them with SCHEDULE.md-based tests.
  - `test_ignores_workspace_skill_schedules` is preserved in spirit — workspace skill dirs still skipped for SCHEDULE.md too.
  - Add new tests (see "Test code" below).
- Modify: `tests/test_skills.py`:
  - Delete `test_parse_schedule_field` (line 194) and `test_parse_schedule_default` (line 206) — `SkillInfo.schedule` no longer exists.
  - Update `SkillInfo(...)` construction at test line 703–705 (`scheduled-only` fixture) to drop the `schedule="..."` kwarg.
- Modify: `tests/test_newsletter_skill.py` — drop assertions on `skill.schedule` if any (verify with grep before editing).
- Modify: `docs/schedules.md` — rewrite Discovery section. Document SCHEDULE.md sidecar layout, contrib default-disable rule, overlay precedence.
- Modify: `docs/skills.md` — remove `schedule:` and `enabled:` frontmatter entries from the field table / documentation. Add a one-paragraph pointer to docs/schedules.md for skills that ship a SCHEDULE.md.
- Modify: `CLAUDE.md` — update the bullet that says `**\`schedule:\` frontmatter** turns a skill into a scheduled task. Bundled, admin-level, and \`extra_skill_paths\`-loaded skills can self-schedule — workspace skills cannot.` to describe the SCHEDULE.md sidecar mechanism + the contrib default-disable rule.

**Key changes:**

- `SkillInfo` loses two fields:
  - `schedule: str` — removed.
  - `enabled: bool` — removed.
- `parse_skill_md` loses two lines: the `schedule=...` and `enabled=...` keyword args.
- New module-level helper in `schedules.py`:

```python
def _discover_skill_schedule_files(config) -> dict[str, ScheduleTask]:
    """Discover SCHEDULE.md sidecars in skill directories.

    Scans: admin > extra > bundled (no workspace — workspace skills
    cannot self-schedule, matching the long-standing rule).

    Contrib (extra-path) SCHEDULE.md is forced to enabled=False so
    third-party skills don't silently activate cron jobs on install.

    Returns a {name -> ScheduleTask} dict. Caller decides how to
    merge with file-based schedules (currently: file-based wins).
    """
    from .skills import (
        _BUNDLED_SKILLS_DIR,
        _iter_skill_dirs,
        _resolve_extra_skill_paths,
    )

    bundled_dir = _BUNDLED_SKILLS_DIR.resolve()
    admin_skills_dir = (config.agent_path / "skills").resolve()
    extra_paths = _resolve_extra_skill_paths(config)

    sources: list[tuple[str, Path]] = [
        ("admin", admin_skills_dir),
        *(("extra", p) for p in extra_paths),
        ("bundled", bundled_dir),
    ]

    result: dict[str, ScheduleTask] = {}
    for tier, base_dir in sources:
        for skill_dir in _iter_skill_dirs(base_dir):
            sched_md = skill_dir / "SCHEDULE.md"
            if not sched_md.exists():
                continue
            task = parse_schedule_file(sched_md)
            if task is None:
                continue
            task.source = tier
            # Use the skill's directory name as the task name so the
            # overlay file at data/{agent_id}/schedules/{name}.md can
            # shadow it by simple name match.
            task.name = skill_dir.name
            if tier == "extra":
                task.enabled = False  # contrib opts-in via overlay
            # First-found wins (admin > extra > bundled)
            result.setdefault(task.name, task)
    return result
```

- `discover_schedules` is rewritten to use the helper:

```python
def discover_schedules(config) -> list[ScheduleTask]:
    """Discover schedule files from admin/workspace dirs and skill
    SCHEDULE.md sidecars.

    Precedence (highest wins on name collision):
      1. data/{agent_id}/schedules/{name}.md  (admin standalone; also
         acts as the overlay for skill SCHEDULE.md of the same name)
      2. workspace/schedules/{name}.md        (workspace standalone)
      3. Skill SCHEDULE.md (admin > extra > bundled)
    """
    tasks_by_name: dict[str, ScheduleTask] = {}

    # Skill SCHEDULE.md sidecars (lowest precedence — populated first
    # so that standalone files can shadow them).
    skill_tasks = _discover_skill_schedule_files(config)
    tasks_by_name.update(skill_tasks)

    # File-based standalone schedules: workspace then admin. Admin
    # wins over workspace, both win over skill SCHEDULE.md.
    for source, base_dir in [
        ("workspace", config.workspace_path / "schedules"),
        ("admin", config.agent_path / "schedules"),
    ]:
        if not base_dir.is_dir():
            continue
        for path in sorted(base_dir.glob("*.md")):
            task = parse_schedule_file(path)
            if task is None:
                continue
            task.source = source
            tasks_by_name[task.name] = task  # later sources override

    return list(tasks_by_name.values())
```

**Test code (new tests for `tests/test_schedules.py`):**

```python
class TestSkillScheduleFiles:
    """SCHEDULE.md sidecar discovery."""

    def test_bundled_skill_schedule_discovered(self, config):
        # Pre-condition: dream/garden/newsletter SCHEDULE.md exist in src/.
        tasks = {t.name: t for t in discover_schedules(config)}
        assert "dream" in tasks
        assert tasks["dream"].schedule == "0 3 * * *"
        assert tasks["dream"].source == "bundled"
        assert tasks["dream"].enabled is True

    def test_contrib_skill_schedule_forced_disabled(self, config, tmp_path):
        # Place a SCHEDULE.md under an extra_skill_paths entry.
        contrib_skill = tmp_path / "contrib_skills" / "news-monitor"
        contrib_skill.mkdir(parents=True)
        (contrib_skill / "SKILL.md").write_text(
            "---\nname: news-monitor\ndescription: Watch news.\n---\nDo it.\n"
        )
        (contrib_skill / "SCHEDULE.md").write_text(
            "---\nschedule: '0 * * * *'\nenabled: true\n---\nHourly check.\n"
        )
        config.extra_skill_paths = [str(tmp_path / "contrib_skills" / "news-monitor")]
        tasks = {t.name: t for t in discover_schedules(config)}
        assert "news-monitor" in tasks
        assert tasks["news-monitor"].enabled is False  # forced

    def test_admin_overlay_shadows_skill_schedule(self, config):
        # Pre-condition: bundled dream/SCHEDULE.md exists with "0 3 * * *".
        overlay_dir = config.agent_path / "schedules"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        (overlay_dir / "dream.md").write_text(
            "---\nschedule: '0 4 * * *'\nenabled: false\n---\nUser-edited.\n"
        )
        tasks = {t.name: t for t in discover_schedules(config)}
        assert tasks["dream"].schedule == "0 4 * * *"
        assert tasks["dream"].enabled is False
        assert tasks["dream"].body == "User-edited."
        assert tasks["dream"].source == "admin"

    def test_workspace_standalone_shadows_skill_schedule(self, config):
        ws_dir = config.workspace_path / "schedules"
        ws_dir.mkdir(parents=True, exist_ok=True)
        (ws_dir / "dream.md").write_text(
            "---\nschedule: '0 5 * * *'\n---\nWorkspace version.\n"
        )
        tasks = {t.name: t for t in discover_schedules(config)}
        assert tasks["dream"].source == "workspace"
        assert tasks["dream"].schedule == "0 5 * * *"

    def test_admin_overlay_beats_workspace_standalone(self, config):
        admin_dir = config.agent_path / "schedules"
        admin_dir.mkdir(parents=True, exist_ok=True)
        ws_dir = config.workspace_path / "schedules"
        ws_dir.mkdir(parents=True, exist_ok=True)
        (admin_dir / "dream.md").write_text(
            "---\nschedule: '0 4 * * *'\n---\nAdmin overlay.\n"
        )
        (ws_dir / "dream.md").write_text(
            "---\nschedule: '0 5 * * *'\n---\nWorkspace.\n"
        )
        tasks = {t.name: t for t in discover_schedules(config)}
        assert tasks["dream"].source == "admin"
        assert tasks["dream"].schedule == "0 4 * * *"

    def test_workspace_skill_schedule_md_skipped(self, config):
        # Workspace skills can't self-schedule even via SCHEDULE.md.
        ws_skill = config.workspace_path / "skills" / "sneaky"
        ws_skill.mkdir(parents=True)
        (ws_skill / "SKILL.md").write_text(
            "---\nname: sneaky\ndescription: x\n---\nDo it.\n"
        )
        (ws_skill / "SCHEDULE.md").write_text(
            "---\nschedule: '* * * * *'\n---\nShould not run.\n"
        )
        tasks = {t.name: t for t in discover_schedules(config)}
        assert "sneaky" not in tasks

    def test_skill_with_no_schedule_md_not_scheduled(self, config):
        # vault, tabstack, etc. ship without SCHEDULE.md — discovery
        # should not synthesize one.
        tasks = {t.name: t for t in discover_schedules(config)}
        assert "vault" not in tasks
        assert "tabstack" not in tasks
```

Plus, in `tests/test_skills.py`, remove the deleted tests and confirm nothing else references `info.schedule`/`info.enabled`.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (the new SCHEDULE.md discovery tests pass; the deleted ones don't reappear) — 2675 passed
- [x] `make check` passes
- [x] `pytest tests/test_schedules.py tests/test_skills.py -v` passes
- [x] `grep -rn "schedule:" src/decafclaw/skills/*/SKILL.md` returns no matches (bundled skills are migrated)
- [x] `grep -rn "info\.schedule\|skill\.schedule\|skill\.enabled" src/decafclaw/ tests/` returns no matches outside test stubs the migration kept

**Verification — manual:**
- [ ] Inspect each of the new SCHEDULE.md files; confirm the body is a faithful copy of the SKILL.md body it came from.
- [ ] Boot the agent locally with `make dev`; confirm the schedule timer logs discover dream/garden/newsletter as scheduled tasks on tick. (Optionally set a near-future cron and watch one fire.)
- [ ] Open `data/{agent_id}/schedules/` and confirm no overlay files were created automatically — overlays only exist when the user explicitly writes one.

---

## Phase 2: HTTP API for schedule management

**What this delivers:** Three new REST endpoints under `/api/schedules` that list, edit, and reset schedule entries. The PUT endpoint writes the overlay (or in-place edit for admin standalone). The DELETE-overlay endpoint reverts a skill SCHEDULE.md back to its default.

**TDD-driven:** write the API tests first, watch them fail (routes don't exist), then implement handlers + helpers.

**Files:**

- Modify: `src/decafclaw/schedules.py` — add module-level helpers for overlay write / delete / serialization.
- Modify: `src/decafclaw/http_server.py` — three new route handlers + three Route() entries. Handlers may live inline like the other handlers do, or be split into `src/decafclaw/web/schedules.py` if size demands it. Default to inline first to match existing style.
- Create: `tests/test_web_schedules_api.py` — full test coverage for the three endpoints.

**Key changes (in `schedules.py`):**

```python
def serialize_to_markdown(task: ScheduleTask) -> str:
    """Render a ScheduleTask as a SCHEDULE.md-format markdown string.

    Frontmatter includes only the fields with values (so a clean
    minimal default doesn't write empty `model:` etc.). Always-included
    field order: schedule, enabled, channel, model, allowed-tools,
    required-skills, email-recipients.
    """
    import yaml

    fm: dict = {"schedule": task.schedule}
    if not task.enabled:
        fm["enabled"] = False
    if task.channel:
        fm["channel"] = task.channel
    if task.model:
        fm["model"] = task.model
    if task.allowed_tools or task.shell_patterns:
        # Reassemble allowed-tools string with scoped shell patterns
        entries = list(task.allowed_tools)
        entries.extend(f"shell({p})" for p in task.shell_patterns)
        fm["allowed-tools"] = ", ".join(entries)
    if task.required_skills:
        fm["required-skills"] = list(task.required_skills)
    if task.email_recipients:
        fm["email-recipients"] = list(task.email_recipients)

    fm_text = yaml.safe_dump(fm, sort_keys=False).rstrip()
    return f"---\n{fm_text}\n---\n\n{task.body}\n"


def _overlay_path(config, name: str) -> Path:
    """Path where an overlay would live; validated against safe name."""
    safe = _safe_task_name(name)
    if safe != name:
        raise ValueError(f"unsafe schedule name: {name!r}")
    return config.agent_path / "schedules" / f"{name}.md"


def write_overlay(config, name: str, patch: dict) -> ScheduleTask:
    """Apply `patch` to the current effective state of `name` and write
    the full resolved task to the admin standalone path. Returns the
    newly resolved task.

    Patch keys (all optional): enabled (bool), schedule (str),
    body (str), channel (str), allowed_tools (list[str]),
    required_skills (list[str]), model (str).
    """
    tasks = {t.name: t for t in discover_schedules(config)}
    base = tasks.get(name)
    if base is None:
        raise KeyError(name)

    if base.source == "workspace":
        # workspace standalones are agent-owned — not user-editable here
        raise PermissionError("workspace-tier schedules are not editable via this API")

    # Apply patch onto a copy
    from dataclasses import replace
    updated = replace(
        base,
        enabled=patch.get("enabled", base.enabled),
        schedule=patch.get("schedule", base.schedule),
        body=patch.get("body", base.body),
        channel=patch.get("channel", base.channel),
        allowed_tools=list(patch.get("allowed_tools", base.allowed_tools)),
        required_skills=list(patch.get("required_skills", base.required_skills)),
        model=patch.get("model", base.model),
    )

    path = _overlay_path(config, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_to_markdown(updated))

    # Re-discover to return the canonical post-write state
    return {t.name: t for t in discover_schedules(config)}[name]


def delete_overlay(config, name: str) -> ScheduleTask:
    """Delete the admin standalone file for `name`. Caller is responsible
    for ensuring this is a valid revert (skill SCHEDULE.md exists to
    fall back to). Returns the post-delete resolved task.

    Raises FileNotFoundError if no overlay file exists.
    Raises KeyError if after delete no SCHEDULE.md remains (i.e. the
    admin file was the primary source, not an overlay).
    """
    path = _overlay_path(config, name)
    if not path.exists():
        raise FileNotFoundError(name)

    path.unlink()
    tasks = {t.name: t for t in discover_schedules(config)}
    if name not in tasks:
        # The "overlay" was actually the only source. Restore? No —
        # raise so the caller can surface a 404. The file is already
        # gone; this is a programming error in the caller.
        raise KeyError(name)
    return tasks[name]
```

Caveat: the `dataclasses.replace` line works because `ScheduleTask` is a dataclass. Verify by reading `schedules.py` lines 18–35 in the worktree.

**Key changes (in `http_server.py`):**

Three new handlers patterned after `vault_list` / `workspace_write`:

```python
@_authenticated
async def schedules_list(request: Request, username: str) -> JSONResponse:
    """GET /api/schedules — list resolved schedules with metadata."""
    config = request.app.state.config
    items = []
    for t in discover_schedules(config):
        items.append({
            "name": t.name,
            "source_tier": t.source,  # "bundled" | "admin" | "extra" | "workspace"
            "source_path": str(t.path),
            "has_overlay": _has_overlay_for(config, t),
            "enabled": t.enabled,
            "schedule": t.schedule,
            "channel": t.channel,
            "model": t.model,
            "allowed_tools": list(t.allowed_tools),
            "required_skills": list(t.required_skills),
            "body": t.body,
            "next_run_iso": _next_run_iso(config, t),
            "last_run_iso": _last_run_iso(config, t),
        })
    return JSONResponse({"schedules": items})


@_authenticated
async def schedules_update(request: Request, username: str) -> JSONResponse:
    """PUT /api/schedules/{name} — apply patch and write/overlay."""
    config = request.app.state.config
    name = request.path_params["name"]
    body = await request.json()
    try:
        task = write_overlay(config, name, body)
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"schedule": _to_dict(config, task)})


@_authenticated
async def schedules_reset(request: Request, username: str) -> JSONResponse:
    """DELETE /api/schedules/{name}/overlay — revert to skill default."""
    config = request.app.state.config
    name = request.path_params["name"]
    try:
        task = delete_overlay(config, name)
    except FileNotFoundError:
        return JSONResponse({"error": "no overlay"}, status_code=404)
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"schedule": _to_dict(config, task)})
```

Plus three new Route() entries in the routes list (around line 1850):

```python
Route("/api/schedules", schedules_list, methods=["GET"]),
Route("/api/schedules/{name}", schedules_update, methods=["PUT"]),
Route("/api/schedules/{name}/overlay", schedules_reset, methods=["DELETE"]),
```

Plus helpers `_has_overlay_for`, `_next_run_iso`, `_last_run_iso`, `_to_dict` either inline in `http_server.py` or in `schedules.py` if cleaner. Computation:

- `_has_overlay_for(config, task)`: `True` iff `task.source == "admin"` AND a same-named SCHEDULE.md exists in any skill dir (bundled / admin-skill / extra). Use `_discover_skill_schedule_files(config)` for the lookup.
- `_next_run_iso(config, task)`: use `croniter(task.schedule, last_run_dt_or_now).get_next(datetime).isoformat()` with UTC.
- `_last_run_iso(config, task)`: `read_last_run(config, task.name)` → ISO string, or `None` if zero.

**Test code (`tests/test_web_schedules_api.py`):**

```python
import pytest
from starlette.testclient import TestClient


class TestSchedulesAPI:
    """REST endpoints for schedule listing and editing."""

    def test_list_includes_bundled(self, http_app):
        client = TestClient(http_app)
        # auth setup elided — match existing test_web_conversations pattern
        r = client.get("/api/schedules")
        assert r.status_code == 200
        names = {s["name"] for s in r.json()["schedules"]}
        assert "dream" in names
        assert "garden" in names
        assert "newsletter" in names

    def test_list_shape(self, http_app):
        client = TestClient(http_app)
        r = client.get("/api/schedules")
        dream = next(s for s in r.json()["schedules"] if s["name"] == "dream")
        # Required fields
        for key in ("name", "source_tier", "has_overlay", "enabled",
                    "schedule", "body", "next_run_iso"):
            assert key in dream
        assert dream["source_tier"] == "bundled"
        assert dream["has_overlay"] is False
        assert dream["enabled"] is True

    def test_put_creates_overlay(self, http_app, config):
        client = TestClient(http_app)
        r = client.put("/api/schedules/dream", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["schedule"]["enabled"] is False
        # Overlay file written
        overlay = config.agent_path / "schedules" / "dream.md"
        assert overlay.exists()
        # Subsequent GET reflects overlay
        listed = next(s for s in client.get("/api/schedules").json()["schedules"]
                      if s["name"] == "dream")
        assert listed["source_tier"] == "admin"
        assert listed["has_overlay"] is True
        assert listed["enabled"] is False

    def test_put_preserves_unchanged_fields_in_overlay(self, http_app, config):
        client = TestClient(http_app)
        client.put("/api/schedules/dream", json={"schedule": "0 4 * * *"})
        overlay = config.agent_path / "schedules" / "dream.md"
        content = overlay.read_text()
        # The body and required-skills should round-trip
        assert "required-skills" in content
        assert "vault" in content

    def test_put_404_for_unknown_name(self, http_app):
        client = TestClient(http_app)
        r = client.put("/api/schedules/nonexistent", json={"enabled": False})
        assert r.status_code == 404

    def test_put_403_for_workspace_source(self, http_app, config):
        ws_dir = config.workspace_path / "schedules"
        ws_dir.mkdir(parents=True, exist_ok=True)
        (ws_dir / "agent-task.md").write_text(
            "---\nschedule: '0 * * * *'\n---\nAgent self-scheduled.\n"
        )
        client = TestClient(http_app)
        r = client.put("/api/schedules/agent-task", json={"enabled": False})
        assert r.status_code == 403

    def test_delete_overlay_reverts(self, http_app, config):
        client = TestClient(http_app)
        client.put("/api/schedules/dream", json={"enabled": False, "schedule": "0 4 * * *"})
        r = client.delete("/api/schedules/dream/overlay")
        assert r.status_code == 200
        # Reverted to bundled defaults
        listed = next(s for s in client.get("/api/schedules").json()["schedules"]
                      if s["name"] == "dream")
        assert listed["source_tier"] == "bundled"
        assert listed["has_overlay"] is False
        assert listed["schedule"] == "0 3 * * *"
        assert listed["enabled"] is True

    def test_delete_overlay_404_when_absent(self, http_app):
        client = TestClient(http_app)
        r = client.delete("/api/schedules/dream/overlay")
        assert r.status_code == 404
```

`http_app` and `config` fixtures: reuse the patterns from `tests/test_web_conversations.py` (auth setup, in-memory agent config). Verify by reading that file's conftest usage.

**Docs:**

- Modify: `docs/schedules.md` — add a new "HTTP API" section documenting the three endpoints and their payloads.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (Phase 1 tests + new API tests) — 2689 passed
- [x] `make check` passes
- [x] `pytest tests/test_web_schedules_api.py -v` passes (14 tests)
- [ ] `curl -s http://localhost:<port>/api/schedules` (in `make dev`) returns a JSON list with `dream`, `garden`, `newsletter`

**Verification — manual:**
- [ ] `curl -X PUT -d '{"enabled":false}' http://localhost:<port>/api/schedules/dream` — verify the overlay file appears at `data/{agent_id}/schedules/dream.md` and subsequent GET reflects the override.
- [ ] `curl -X DELETE http://localhost:<port>/api/schedules/dream/overlay` — verify the overlay file disappears and GET reflects the bundled default again.
- [ ] Try PUT on a workspace-source schedule — verify 403.

---

## Phase 3: Sidebar UI tab for schedule management

**What this delivers:** A fourth tab in the conversation sidebar that lists discovered schedules, lets the user toggle enabled/disabled, edit cron + channel + prompt body, save (which calls the PUT API), and reset overlays (which calls the DELETE API).

**TDD opt-out for JS:** there's no Lit component test harness currently in the repo (no JS test setup discoverable in the worktree's repo root via `grep test package.json` — verify in execute before claiming). Component-level tests are out of scope for Phase 3; verification is via Playwright-driven manual smoke + the API tests in Phase 2 already exercise the backend.

**Files:**

- Create: `src/decafclaw/web/static/components/schedules-sidebar.js` — new Lit component, patterned after `vault-sidebar.js`. ~250–350 lines.
- Modify: `src/decafclaw/web/static/components/conversation-sidebar.js`:
  - Add `import './schedules-sidebar.js';` at the top (next to existing sidebar imports, lines 4–5).
  - Add a fourth tab button `Schedules` next to `Chats / Vault / Files` (lines 467–474).
  - Add `<schedules-sidebar .active=${this._sidebarTab === 'schedules'} style="..." ></schedules-sidebar>` next to the other sidebar components (lines 478–487).
- Modify: `src/decafclaw/web/static/style.css` (or wherever sidebar tab styling lives) — add any needed CSS for the new tab. Reuse existing primitives where possible (`.dc-overlay-header`, `.dc-icon-btn` etc., per CLAUDE.md web UI styling notes).
- Modify: `docs/web-ui.md` — document the new sidebar tab.
- Modify: `docs/schedules.md` — add a "Sidebar UI" section with a short tour and a screenshot placeholder (no screenshot needed; describe in prose).
- Modify: `CLAUDE.md` (only if `schedules-sidebar.js` becomes a hot navigation file) — add to the Web UI key files list.

**Key changes (`schedules-sidebar.js` outline):**

```javascript
import { LitElement, html, nothing } from 'lit';

export class SchedulesSidebar extends LitElement {
  static properties = {
    active: { type: Boolean },
    _schedules: { type: Array, state: true },
    _loading: { type: Boolean, state: true },
    _expandedName: { type: String, state: true },
    _editDraft: { type: Object, state: true },
    _saveError: { type: String, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.active = false;
    /** @type {Array<object>} */
    this._schedules = [];
    this._loading = false;
    /** @type {string|null} */
    this._expandedName = null;
    this._editDraft = null;
    this._saveError = '';
  }

  updated(changedProps) {
    if (changedProps.has('active') && this.active && !changedProps.get('active')) {
      this.#fetchSchedules();
    }
  }

  async #fetchSchedules() {
    this._loading = true;
    try {
      const res = await fetch('/api/schedules');
      if (res.ok) {
        const data = await res.json();
        this._schedules = data.schedules || [];
      }
    } finally {
      this._loading = false;
    }
  }

  async #toggleEnabled(name, currentEnabled) {
    await fetch(`/api/schedules/${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !currentEnabled }),
    });
    this.#fetchSchedules();
  }

  async #saveEdit() {
    if (!this._editDraft) return;
    const { name, schedule, channel, body, enabled } = this._editDraft;
    const res = await fetch(`/api/schedules/${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ schedule, channel, body, enabled }),
    });
    if (!res.ok) {
      this._saveError = `Save failed: ${res.status}`;
      return;
    }
    this._editDraft = null;
    this._expandedName = null;
    this._saveError = '';
    this.#fetchSchedules();
  }

  async #resetOverlay(name) {
    await fetch(`/api/schedules/${encodeURIComponent(name)}/overlay`, {
      method: 'DELETE',
    });
    this._editDraft = null;
    this._expandedName = null;
    this.#fetchSchedules();
  }

  // render() composes: header (count + refresh) → list of schedule
  // rows → per-row expandable edit panel (cron field, channel field,
  // body textarea, save/cancel/reset buttons).
}

customElements.define('schedules-sidebar', SchedulesSidebar);
```

The render method emits one row per schedule with:
- Name + source tier badge (`bundled` / `admin` / `extra` / `workspace`)
- An "overridden" pill when `has_overlay: true`
- A toggle (checkbox or styled button) for enabled
- Next-run timestamp
- An expand chevron that opens an inline edit panel
- Edit panel: cron input, channel input, body textarea, save/cancel buttons. "Reset to defaults" only visible when `has_overlay: true`.
- Workspace-source rows render the edit affordances disabled with a tooltip.

**Conversation-sidebar.js diff (key snippets):**

```javascript
// imports area (next to existing)
import './schedules-sidebar.js';

// tab button row (insert after Files button)
<button class="sidebar-tab ${this._sidebarTab === 'schedules' ? 'active' : ''}"
  @click=${() => this.#switchTab('schedules')}>Schedules</button>

// component slot (next to vault/files)
<schedules-sidebar
  .active=${this._sidebarTab === 'schedules'}
  style="${this._sidebarTab !== 'schedules' ? 'display:none' : ''}"
></schedules-sidebar>
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (full suite from earlier phases stays green; Phase 3 adds no Python code) — 2689 passed
- [x] `make check` passes (includes `make check-js` typecheck of the new JS file)
- [x] No new WebSocket message types added (so `make check-message-types` is unaffected — confirmed)

**Verification — manual:**
- [ ] `make dev` → load web UI → click the new "Schedules" tab → confirm dream/garden/newsletter appear with `bundled` badge, `enabled: true`, correct cron strings.
- [ ] Toggle `dream` off → confirm an overlay file appears at `data/{agent_id}/schedules/dream.md` and the row now shows `admin` + "overridden". Reload the page; state persists.
- [ ] Expand the row → change cron to `0 4 * * *` and edit body → Save → confirm overlay file reflects edit.
- [ ] Click "Reset to defaults" → overlay file deleted → row reverts to `bundled` badge with original cron and enabled state.
- [ ] Pre-create a `workspace/schedules/agent-task.md` file → load the tab → confirm the row shows `workspace` and the edit affordances are disabled with a tooltip.
- [ ] Visual sanity: tab styling matches existing tabs, no Pico cascade gotchas (per CLAUDE.md web UI section). Force-fresh Chrome via incognito (not while `make dev` is running Playwright on the same profile).
- [ ] Mobile viewport: tab list still fits or scrolls horizontally without breaking layout.

---

## Phase 4: Side-panel schedule editor + workspace-editable

**What this delivers:** Clicking a schedule row opens a dedicated `<schedule-page>` in the `#wiki-main` side panel (same surface as `<wiki-page>`, `<file-page>`, `<config-panel>` — mutually exclusive). The page shows a header (back, name, source tier, reset button when overlay exists), a small form-field row (cron, channel, enabled), and a `<wiki-editor>` for the prompt body. Workspace-tier schedules become user-editable in place (no more 403).

**Driver:** post-Phase 3 UX feedback — the inline expand-to-edit panel feels cramped for prompt body editing. Workspace tier should be user-editable so users can adjust agent-self-scheduled tasks.

**TDD partial:** backend behavior changes (workspace-editable + content alias + modified field) are TDD-driven via API tests. UI changes are verified via `make check-js` + manual smoke (no JS test harness in tree).

**Files:**

Backend:
- Modify: `src/decafclaw/schedules.py`
  - `write_overlay`: when source is `"workspace"`, write to `config.workspace_path / "schedules" / f"{name}.md"` instead of the admin overlay path. Drop the `PermissionError` raise. Path safety (`_safe_task_name`) applies to both paths.
- Modify: `src/decafclaw/http_server.py`
  - `_schedule_to_dict`: add `modified` field (mtime of `task.path` as a unix timestamp, or `0` if file doesn't exist yet for not-yet-overlaid skill SCHEDULE.md? — use `task.path.stat().st_mtime` since `task.path` is always a real file at this point).
  - `schedules_update` handler: at the top, alias the incoming `content` key to `body` if `content` is present and `body` is not: `if "content" in patch and "body" not in patch: patch["body"] = patch.pop("content")`. This lets wiki-editor's `{content, modified}` PUT shape work directly. Drop the `except PermissionError` arm since `write_overlay` no longer raises it (or leave defensively — pick one and be consistent).
  - PUT response: include `modified` field in the returned dict (the mtime of the just-written file). wiki-editor uses this for conflict tracking on subsequent saves.
  - Add new endpoint `GET /api/schedules/{name}` (single-schedule fetch) returning `{schedule: {...}}` — the same dict shape as the list entries. Useful for the side-panel page to fetch a single entry without filtering the list.
- Modify: `tests/test_web_schedules_api.py`
  - Update `test_put_403_for_workspace_source` → rename to `test_put_workspace_writes_in_place`. Assertions: PUT returns 200, file appears at `workspace/schedules/{name}.md`, file does NOT appear at admin overlay path.
  - Add `test_put_accepts_content_as_alias_for_body` — PUT `{content: "new body"}`, assert effective body is updated, no `body` key required in the request.
  - Add `test_get_includes_modified_field` — GET /api/schedules response has `modified` (a number) for each entry.
  - Add `test_get_single_schedule` — `GET /api/schedules/{name}` returns one schedule with full shape; `GET /api/schedules/nonexistent` returns 404.
  - Add `test_put_response_includes_modified` — PUT response has `modified` field that changes (or is set) after write.

Frontend:
- Create: `src/decafclaw/web/static/components/schedule-page.js` — new Lit component, similar shape to `config-panel.js`. Hosts:
  - Header row: back button, name title, source tier badge, "overridden" pill if applicable, "Reset to default" button (only when `has_overlay: true`).
  - Form row: cron input, channel input, enabled checkbox. Each `@change` (or `@blur`) triggers `PUT /api/schedules/{name}` with the changed field; on success, updates `modified` from response and propagates to wiki-editor's `.modified` property.
  - `<wiki-editor>` for the body content. `.page=${name}`, `.content=${_data.body}`, `.modified=${_data.modified}`, `save-endpoint="/api/schedules/"`. wiki-editor's natural PUT shape `{content, modified}` is accepted by the schedule endpoint via the content→body alias.
  - Dispatches `close` event on back button click.
- Modify: `src/decafclaw/web/static/components/schedules-sidebar.js`
  - Remove inline edit machinery: `_editDraft`, `_expandedName`, `_saveError`, `#beginEdit`, `#cancelEdit`, `#saveEdit`, `#resetOverlay`, `#renderEditPanel`.
  - Add `openName: String` property (the currently-open schedule, drives row highlighting).
  - Click on row name → `this.dispatchEvent(new CustomEvent('schedule-open', { detail: { name }, bubbles: true, composed: true }))`.
  - Keep the enabled toggle, tier badge, "overridden" pill, cron + next-run display. The row reduces to a clean list item.
  - Remove `_onSchedulesChanged` listener (already removed in Phase 3 follow-up — verify).
  - The component re-fetches when `active` flips to true, AND when it receives a `schedule-saved` event dispatched globally (so saves from the page panel refresh the sidebar list).
- Modify: `src/decafclaw/web/static/components/conversation-sidebar.js`
  - Pass through `schedule-open` event (let it bubble + composed; app.js will catch it).
  - Pass `.openName=${this._openSchedule}` to `<schedules-sidebar>` and add internal `_openSchedule` state set by external setter (called from app.js).
- Modify: `src/decafclaw/web/static/app.js`
  - Import `./components/schedule-page.js`.
  - Add `<schedule-page>` element to the `#wiki-main` panel-element lookup at the top: `const schedulePageEl = ...querySelector('#wiki-main schedule-page')`.
  - Add `showSchedulePage(name)` function — mirrors `showWikiPage`/`showFilePage`: reveal schedule-page, hide wiki-page/file-page/config-panel, switch sidebar tab to `schedules`, update URL with `?schedule=${name}`.
  - Add `hideSchedulePage()` function.
  - Wire mutual exclusion: each of `showWikiPage`, `showFilePage`, `showConfigPanel` must also hide schedule-page now.
  - Listen for `schedule-open` event → call `showSchedulePage(detail.name)`.
  - Handle `popstate` and initial URL parse: `?schedule=foo` opens schedule-page for `foo`.
- Modify: `src/decafclaw/web/static/index.html` — add `<schedule-page hidden></schedule-page>` inside `#wiki-main`. Position: after the existing wiki/file/config panel elements.
- Modify: `src/decafclaw/web/static/styles/sidebar.css` (and/or main `style.css`)
  - Remove now-unused edit-panel CSS rules: `.schedule-edit-panel`, `.schedule-edit-actions`, `.schedule-save-error`, `.schedule-field-label`, `.schedule-body-readonly`, `.schedule-readonly-note`, `.schedule-reset-btn`.
  - Add new CSS for `<schedule-page>`: header row, form row, wiki-editor integration. Reuse `.dc-overlay-header`, `.dc-icon-btn` primitives. Tier badges keep their existing class names + use the same colors.

Docs:
- Modify: `docs/web-ui.md` — update "Schedules tab" section: clicking a row opens the editor in the side panel. Toggle enabled directly from the list. Reset is in the side-panel header. Workspace schedules are editable in place.
- Modify: `docs/schedules.md` — UI section: rewrite editor description. HTTP API section: add `modified` field, add `content` alias, document new GET single-schedule endpoint, update workspace semantics.

**Key changes (backend):**

`write_overlay` updated body:

```python
def write_overlay(config, name: str, patch: dict) -> ScheduleTask:
    """Apply patch to current effective state and write resolved task.

    For source ∈ {bundled, admin, extra} skill SCHEDULE.md: writes to
    data/{agent_id}/schedules/{name}.md (creates overlay).
    For source == "admin" standalone: in-place edit of same path.
    For source == "workspace": in-place edit of
    workspace/schedules/{name}.md.
    """
    # Filter None values so null = "leave unchanged"
    patch = {k: v for k, v in patch.items() if v is not None}

    # Validate cron if present
    if "schedule" in patch and not croniter.is_valid(patch["schedule"]):
        raise ValueError(f"invalid cron expression: {patch['schedule']!r}")

    tasks = {t.name: t for t in discover_schedules(config)}
    base = tasks.get(name)
    if base is None:
        raise KeyError(name)

    updated = replace(
        base,
        enabled=patch.get("enabled", base.enabled),
        schedule=patch.get("schedule", base.schedule),
        body=patch.get("body", base.body),
        channel=patch.get("channel", base.channel),
        allowed_tools=list(patch.get("allowed_tools", base.allowed_tools)),
        required_skills=list(patch.get("required_skills", base.required_skills)),
        model=patch.get("model", base.model),
    )

    if base.source == "workspace":
        path = config.workspace_path / "schedules" / f"{_safe_task_name(name)}.md"
        if _safe_task_name(name) != name:
            raise ValueError(f"unsafe schedule name: {name!r}")
    else:
        path = _overlay_path(config, name)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_to_markdown(updated))

    return {t.name: t for t in discover_schedules(config)}[name]
```

`_schedule_to_dict` gains `modified`:

```python
def _schedule_to_dict(config, task):
    return {
        "name": task.name,
        "source_tier": task.source,
        "source_path": str(task.path),
        "has_overlay": _has_overlay_for(config, task),
        "enabled": task.enabled,
        "schedule": task.schedule,
        "channel": task.channel,
        "model": task.model,
        "allowed_tools": list(task.allowed_tools),
        "required_skills": list(task.required_skills),
        "body": task.body,
        "modified": task.path.stat().st_mtime if task.path.exists() else 0,
        "next_run_iso": _next_run_iso(config, task),
        "last_run_iso": _last_run_iso(config, task),
    }
```

`schedules_update` handler — alias content → body, add modified to response:

```python
@_authenticated
async def schedules_update(request, username):
    config = request.app.state.config
    name = request.path_params["name"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)
    # Wiki-editor sends {content, modified}; alias content → body
    if "content" in body and "body" not in body:
        body["body"] = body.pop("content")
    try:
        task = write_overlay(config, name, body)
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"schedule": _schedule_to_dict(config, task)})
```

(PermissionError catch removed — workspace is editable now.)

New endpoint:

```python
@_authenticated
async def schedules_get(request, username):
    config = request.app.state.config
    name = request.path_params["name"]
    tasks = {t.name: t for t in discover_schedules(config)}
    task = tasks.get(name)
    if task is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"schedule": _schedule_to_dict(config, task)})
```

Route entry (before the PUT route to avoid `/{name}` shadowing `/{name}/overlay`):

```python
Route("/api/schedules/{name}", schedules_get, methods=["GET"]),
```

**Key changes (frontend, condensed sketch):**

```javascript
// schedule-page.js
import { LitElement, html, nothing } from 'lit';
import './wiki-editor.js';

export class SchedulePage extends LitElement {
  static properties = {
    name: { type: String, reflect: true },
    _data: { state: true },
    _loading: { state: true },
  };
  createRenderRoot() { return this; }

  constructor() {
    super();
    this.name = '';
    this._data = null;
    this._loading = false;
  }

  updated(changedProps) {
    if (changedProps.has('name') && this.name) {
      this.#fetchSchedule();
    }
  }

  async #fetchSchedule() {
    this._loading = true;
    try {
      const res = await fetch(`/api/schedules/${encodeURIComponent(this.name)}`);
      if (res.ok) {
        const data = await res.json();
        this._data = data.schedule;
      } else {
        this._data = null;
      }
    } finally {
      this._loading = false;
    }
  }

  async #patchField(field, value) {
    if (!this._data) return;
    const res = await fetch(`/api/schedules/${encodeURIComponent(this.name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [field]: value }),
    });
    if (res.ok) {
      const data = await res.json();
      this._data = data.schedule;
      window.dispatchEvent(new CustomEvent('schedule-saved'));
    }
  }

  async #resetOverlay() {
    if (!confirm(`Reset "${this.name}" to its skill default?`)) return;
    const res = await fetch(`/api/schedules/${encodeURIComponent(this.name)}/overlay`, {
      method: 'DELETE',
    });
    if (res.ok) {
      const data = await res.json();
      this._data = data.schedule;
      window.dispatchEvent(new CustomEvent('schedule-saved'));
    }
  }

  #close() {
    this.dispatchEvent(new CustomEvent('close', { bubbles: true, composed: true }));
  }

  render() {
    if (!this._data) return html`<div class="schedule-page-empty">${this._loading ? 'Loading…' : 'Not found.'}</div>`;
    const d = this._data;
    return html`
      <div class="schedule-page">
        <div class="schedule-page-header dc-overlay-header">
          <button class="dc-icon-btn" @click=${this.#close} title="Back" aria-label="Back">&larr;</button>
          <span class="schedule-page-title">${d.name}</span>
          <span class="schedule-tier-badge tier-${d.source_tier}">${d.source_tier}</span>
          ${d.has_overlay ? html`<span class="schedule-overlay-badge">overridden</span>` : nothing}
          ${d.has_overlay ? html`
            <button class="outline schedule-reset-btn" @click=${this.#resetOverlay}>Reset to default</button>
          ` : nothing}
        </div>
        <div class="schedule-page-form">
          <label><span>Cron</span>
            <input type="text" .value=${d.schedule}
              @change=${(e) => this.#patchField('schedule', e.target.value)} />
          </label>
          <label><span>Channel</span>
            <input type="text" .value=${d.channel}
              @change=${(e) => this.#patchField('channel', e.target.value)} />
          </label>
          <label class="inline">
            <input type="checkbox" .checked=${d.enabled}
              @change=${(e) => this.#patchField('enabled', e.target.checked)} />
            <span>Enabled</span>
          </label>
        </div>
        <wiki-editor
          .page=${d.name}
          .content=${d.body}
          .modified=${d.modified}
          save-endpoint="/api/schedules/"
        ></wiki-editor>
      </div>
    `;
  }
}
customElements.define('schedule-page', SchedulePage);
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (existing + new API tests) — 2694 passed
- [x] `make check` passes (Python + JS typecheck + message-types drift)
- [x] `pytest tests/test_web_schedules_api.py -v` passes (19 tests)
- [x] `make check-js` clean (new schedule-page.js typechecks)

**Verification — manual:**
- [ ] Click a schedule row → `<schedule-page>` opens in `#wiki-main` with mutual exclusion against wiki-page/file-page/config-panel. URL gains `?schedule={name}`.
- [ ] Edit cron/channel/enabled in form fields → saves immediately on change → row in sidebar reflects new state (after the schedule-saved event).
- [ ] Edit body in wiki-editor → auto-saves → overlay file reflects edit. The wiki-editor toolbar functions normally.
- [ ] "Reset to default" button works → deletes overlay → schedule-page reloads with bundled defaults → sidebar refreshes.
- [ ] Workspace schedule: click row → edit cron + body → saves to `workspace/schedules/{name}.md` (not admin overlay).
- [ ] Back button (←) closes schedule-page → URL `?schedule` cleared → side panel hides if nothing else open.
- [ ] Opening a wiki page or file while schedule-page is open hides schedule-page (mutual exclusion works in both directions).
- [ ] Deep link: paste URL with `?schedule=dream` in fresh tab → schedule-page opens with dream loaded.

---

## Phase 5: Run now action

**What this delivers:** A "Run now" button in the schedule-page header that fires the schedule immediately, bypassing the cron timer and the `enabled: false` flag. Backend: new `POST /api/schedules/{name}/run` endpoint that kicks the task off via `asyncio.create_task` and returns the generated `conv_id` so the user can navigate to the running conversation.

**Driver:** Useful for testing schedule changes (edit prompt → run immediately to see result), and for manually triggering one-off runs of disabled schedules.

**TDD partial:** backend endpoint is TDD-driven via API tests with `run_schedule_task` patched (per CLAUDE.md test-speed rule). Frontend button verified via manual smoke.

**Files:**

Backend:
- Modify: `src/decafclaw/schedules.py`
  - `run_schedule_task`: add optional `conv_id: str | None = None` parameter. If `None`, generate internally (current behavior). If provided, use the supplied conv_id. This lets the handler return the conv_id to the client before the task starts.
- Modify: `src/decafclaw/http_server.py`
  - New `schedules_run` handler at `POST /api/schedules/{name}/run`:
    - Validate name exists in `discover_schedules(config)`; 404 if not.
    - Generate `conv_id = f"schedule-{name}-{timestamp}"` using same format as the timer.
    - Write `last_run` timestamp via `write_last_run(config, name)` so the cron timer doesn't double-fire shortly after.
    - Kick off `asyncio.create_task(run_schedule_task(config, event_bus, manager, task, conv_id=conv_id))`. Don't await — fire and forget.
    - Return 202 Accepted with `{conv_id, task_name, started_at}` body.
    - **Important:** Runs regardless of `task.enabled` (manual click is explicit intent). The timer respects `enabled`; manual does not.
  - New Route entry registered next to other `/api/schedules/{name}/...` routes:
    ```python
    Route("/api/schedules/{name}/run", schedules_run, methods=["POST"]),
    ```
- Modify: `tests/test_web_schedules_api.py`
  - Add `test_run_endpoint_starts_task` — patches `run_schedule_task` (per CLAUDE.md, avoids firing real agent turns). Asserts: 202 response, response has `conv_id` + `task_name`, `run_schedule_task` was called once with matching conv_id arg, `last_run` file was written.
  - Add `test_run_endpoint_404_unknown` — POST to nonexistent name returns 404.
  - Add `test_run_endpoint_works_when_disabled` — pre-create a workspace schedule with `enabled: false`, POST to run, assert 202 + task fires (via patched `run_schedule_task`). Confirms manual run bypasses the enabled flag.

Frontend:
- Modify: `src/decafclaw/web/static/components/schedule-page.js`
  - Add `_runStatus: { state: true }` property (values: `''`, `'running'`, `'started'`, `'error'`).
  - Add `_runError: { state: true }` for error message.
  - Add `#runNow` handler — POST `/api/schedules/${name}/run`, handle 202 / 4xx / network error, set `_runStatus` accordingly. Clear status after ~3s with setTimeout.
  - Add "Run now" button in the header, next to (or before) the Reset button. Always visible (not gated on `has_overlay`). Show transient status text next to it ("Started ✓" / "Failed" / "Running…").
  - Use a tag-qualified class `button.schedule-run-btn` per Pico convention.
- Modify: CSS (likely `src/decafclaw/web/static/styles/schedule-page.css`)
  - Add `button.schedule-run-btn` rules (variant style, e.g. accent color to differentiate from Reset).
  - Add `.schedule-run-status` text styling (small, subtle).

Docs:
- Modify: `docs/schedules.md`
  - HTTP API section: document `POST /api/schedules/{name}/run` (request: no body required, response: `{conv_id, task_name, started_at}`, 404 for unknown name, 202 status).
  - Sidebar UI section: mention "Run now" button.
- Modify: `docs/web-ui.md`
  - Schedules tab section: mention Run now in the editor controls list.

**Key code (backend):**

```python
@_authenticated
async def schedules_run(request: Request, username: str) -> JSONResponse:
    """POST /api/schedules/{name}/run — fire a schedule immediately."""
    config = request.app.state.config
    event_bus = request.app.state.event_bus
    manager = request.app.state.manager
    name = request.path_params["name"]

    tasks = {t.name: t for t in discover_schedules(config)}
    task = tasks.get(name)
    if task is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    conv_id = f"schedule-{task.name}-{timestamp}"
    write_last_run(config, task.name)
    asyncio.create_task(
        run_schedule_task(config, event_bus, manager, task, conv_id=conv_id)
    )
    return JSONResponse(
        {
            "conv_id": conv_id,
            "task_name": task.name,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
        status_code=202,
    )
```

Verify `request.app.state` exposes `event_bus` and `manager` — they should, since other handlers route through manager (search http_server.py for `app.state.manager` references to confirm the pattern).

`run_schedule_task` signature change:

```python
async def run_schedule_task(config, event_bus, manager, task: ScheduleTask,
                             conv_id: str | None = None) -> dict:
    """Run a single scheduled task as an agent turn via ConversationManager.

    If conv_id is provided, use it; otherwise generate from task.name + now.
    Returns {"task_name", "channel", "response", "is_ok", "context_id"}.
    """
    from .conversation_manager import TurnKind

    if conv_id is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        conv_id = f"schedule-{task.name}-{timestamp}"
    # ... rest unchanged
```

**Key code (frontend, the new pieces):**

```javascript
async #runNow() {
  this._runStatus = 'running';
  this._runError = '';
  try {
    const res = await fetch(
      `/api/schedules/${encodeURIComponent(this.name)}/run`,
      { method: 'POST' },
    );
    if (!res.ok) {
      this._runStatus = 'error';
      this._runError = `Failed (${res.status})`;
      return;
    }
    this._runStatus = 'started';
    // Clear transient status after 3s.
    setTimeout(() => {
      if (this._runStatus === 'started') this._runStatus = '';
    }, 3000);
  } catch (e) {
    this._runStatus = 'error';
    this._runError = 'Network error';
  }
}

// In render header, next to Reset button:
<button class="outline schedule-run-btn" @click=${this.#runNow}>Run now</button>
${this._runStatus === 'running' ? html`<span class="schedule-run-status">Running…</span>` :
  this._runStatus === 'started' ? html`<span class="schedule-run-status">Started ✓</span>` :
  this._runStatus === 'error' ? html`<span class="schedule-run-status error">${this._runError}</span>` :
  nothing}
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (new endpoint tests included) — 2697 passed
- [x] `make check` passes (Python + JS typecheck + message-types drift)
- [x] `pytest tests/test_web_schedules_api.py -v` passes — 22 tests total (19 existing + 3 new run-endpoint tests)

**Verification — manual:**
- [ ] Open `dream` in schedule-page → click "Run now" → button briefly shows "Started ✓" → server logs show the schedule task firing → a new conversation appears in the system conversation list.
- [ ] Disable `dream` → "Run now" still fires (bypasses the enabled flag).
- [ ] POST `/api/schedules/nonexistent/run` directly → returns 404.
- [ ] Concurrent: click "Run now" twice in quick succession → both fire (no double-fire prevention in v1; documented).
