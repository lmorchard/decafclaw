# Dream Memory Consolidation — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add periodic dream consolidation and wiki gardening as scheduled skills. Extend the schedule system to discover schedules from skill frontmatter. No custom Python tools — prompt files leveraging existing infrastructure.

**Architecture:** Two bundled skills with `schedule` frontmatter (both user-invokable and scheduled). The schedule timer is extended to discover skills with schedules alongside regular schedule files, with trust boundary enforcement.

---

### Task 1: Add `schedule` field to SkillInfo and extend schedule discovery

**Files:**
- Modify: `src/decafclaw/skills/__init__.py` — parse `schedule` field
- Modify: `src/decafclaw/schedules.py` — discover skills with schedules
- Modify: `tests/test_skills.py` — parsing test
- Modify: `tests/test_schedules.py` — discovery test

- [ ] **Step 1: Add `schedule` field to SkillInfo**

In `skills/__init__.py`, add to the SkillInfo dataclass:
```python
schedule: str = ""  # cron expression, empty = not scheduled
```

In `parse_skill_md()`, add:
```python
schedule=meta.get("schedule", ""),
```

- [ ] **Step 2: Extend `discover_schedules` to include skills**

In `schedules.py`, after scanning the file-based schedule directories, also scan discovered skills:

```python
# Also discover scheduled skills (bundled + admin only, not workspace)
from .skills import _BUNDLED_SKILLS_DIR
bundled_dir = _BUNDLED_SKILLS_DIR.resolve()
for skill in getattr(config, "discovered_skills", []):
    if not skill.schedule:
        continue
    # Trust boundary: only bundled and admin-level skills
    skill_path = Path(skill.location).resolve()
    is_bundled = skill_path.is_relative_to(bundled_dir)
    is_admin = skill_path.is_relative_to(config.agent_path.resolve() / "skills")
    if not (is_bundled or is_admin):
        continue
    if not croniter.is_valid(skill.schedule):
        log.warning(f"Invalid cron in skill '{skill.name}': {skill.schedule}")
        continue
    # Don't override file-based schedules with same name
    if skill.name in tasks_by_name:
        continue
    tasks_by_name[skill.name] = ScheduleTask(
        name=skill.name,
        schedule=skill.schedule,
        body=skill.body,
        source="bundled" if is_bundled else "admin",
        path=skill.location / "SKILL.md",
        effort=skill.effort,
        required_skills=skill.requires_skills,
    )
```

- [ ] **Step 3: Write tests**

```python
def test_parse_schedule_field(tmp_path):
    """Parse schedule cron expression from skill frontmatter."""
    ...

def test_discover_includes_bundled_skill_schedules(config):
    """Bundled skills with schedule field appear in discover_schedules."""
    ...

def test_discover_ignores_workspace_skill_schedules(config):
    """Workspace skills with schedule field are ignored."""
    ...
```

- [ ] **Step 4: Run tests**

Run: `make check && make test`

- [ ] **Step 5: Commit**

```
feat: extend schedule discovery to include skill frontmatter schedules
```

---

### Task 2: Create `!dream` command skill

**Files:**
- Create: `src/decafclaw/skills/dream/SKILL.md`

- [ ] **Step 1: Write the SKILL.md with four-phase consolidation prompt**

Frontmatter: `name`, `description`, `schedule: "0 * * * *"`, `effort: strong`, `required-skills: [wiki]`, `user-invocable: true`, `context: fork`.

Body: detailed phase-by-phase instructions (Orient → Gather → Consolidate → Prune) referencing available tools.

- [ ] **Step 2: Verify skill discovery and schedule detection**

Run: `make check && make test`

- [ ] **Step 3: Commit**

```
feat: add !dream command for memory consolidation (hourly scheduled)
```

---

### Task 3: Create `!garden` command skill

**Files:**
- Create: `src/decafclaw/skills/garden/SKILL.md`

- [ ] **Step 1: Write the SKILL.md with gardening sweep prompt**

Frontmatter: `name`, `description`, `schedule: "0 3 * * 0"`, `effort: strong`, `required-skills: [wiki]`, `user-invocable: true`, `context: fork`.

Body: gardening instructions (merge overlapping, fix broken links, add connections, update tl;drs, split oversized, review orphans).

- [ ] **Step 2: Verify skill discovery and schedule detection**

Run: `make check && make test`

- [ ] **Step 3: Commit**

```
feat: add !garden command for wiki gardening (weekly scheduled)
```

---

### Task 4: Update wiki SKILL.md with tl;dr convention

**Files:**
- Modify: `src/decafclaw/skills/wiki/SKILL.md`

- [ ] **Step 1: Add tl;dr convention to wiki gardening rules**

Add guidance that pages longer than ~20 lines should have a `> tl;dr:` blockquote after the title.

- [ ] **Step 2: Commit**

```
feat: add tl;dr summary convention to wiki SKILL.md
```

---

### Task 5: Docs update

**Files:**
- Create: `docs/dream-consolidation.md`
- Modify: `docs/index.md`
- Modify: `docs/schedules.md` — add skill schedule frontmatter docs
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write docs**

Document dream consolidation and gardening: what they do, the phases, how schedule frontmatter works, user commands, trust boundary.

- [ ] **Step 2: Update existing docs**

- `docs/schedules.md` — add section on skill schedule frontmatter
- `docs/index.md` — add dream consolidation to features
- `CLAUDE.md` — add key files, conventions

- [ ] **Step 3: Commit**

```
docs: add dream consolidation and skill schedule documentation
```
