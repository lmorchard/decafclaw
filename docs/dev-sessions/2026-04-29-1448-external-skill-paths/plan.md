# External Skill Paths Implementation Plan

**Goal:** Add a `extra_skill_paths` config field that appends user-configured directories to the skill discovery scan list, so `npx skills add … -a claude-code -g` output (or any other source) can be picked up by decafclaw without code or upstream changes.

**Approach:** Top-level `Config.extra_skill_paths: list[str]` with an `EXTRA_SKILL_PATHS` env override. Each path is `expanduser()`/`expandvars()`-resolved and anchored to `agent_path` if relative. Resolved paths are appended to `discover_skills`' scan list **after** bundled, so externals can never shadow first-party skills. Trust posture follows the existing non-bundled rules — `auto-approve` stripped, `always-loaded` stripped, scheduling excluded — automatically via the existing `is_relative_to(_BUNDLED_SKILLS_DIR)` check.

**Tech stack:** Python stdlib (`pathlib`, `os.path.expandvars`), existing `_parse_list` helper.

---

## Phase 1: Config field, env override, and discovery splice

End-to-end: a skill directory listed in `extra_skill_paths` is discovered and appears alongside bundled/admin/workspace skills. Bundled skills with the same name win.

**Files:**
- Modify: `src/decafclaw/config.py` — add `extra_skill_paths: list[str]` field to `Config` dataclass; populate it in `load_config()` with env-override + JSON read; pass to constructor
- Modify: `src/decafclaw/skills/__init__.py` — add `_resolve_extra_skill_paths(config)` helper; extend `discover_skills` `scan_paths` list
- Test: `tests/test_skills.py` — new tests in the discover_skills section

**Key changes:**

In `src/decafclaw/config.py`, after `default_model: str = ""` (line 166):

```python
extra_skill_paths: list[str] = field(default_factory=list)
```

In `load_config()`, after the `default_model = file_data.get(...)` block (around line 447):

```python
if "EXTRA_SKILL_PATHS" in os.environ:
    extra_skill_paths = _parse_list(os.environ["EXTRA_SKILL_PATHS"])
else:
    raw = file_data.get("extra_skill_paths", [])
    extra_skill_paths = [str(p) for p in raw] if isinstance(raw, list) else []
```

Add `extra_skill_paths=extra_skill_paths,` to the `Config(...)` constructor call (around line 474-497).

In `src/decafclaw/skills/__init__.py`, add a module-level helper above `discover_skills`:

```python
def _resolve_extra_skill_paths(config) -> list[Path]:
    """Resolve user-configured external skill paths.

    For each entry: expand $VARS and ~, then anchor relative paths to
    config.agent_path (matching vault_root). Order is preserved.
    """
    resolved: list[Path] = []
    for raw in config.extra_skill_paths:
        expanded = os.path.expandvars(str(raw))
        p = Path(expanded).expanduser()
        if not p.is_absolute():
            p = config.agent_path / p
        resolved.append(p)
    return resolved
```

In `discover_skills`, change `scan_paths` (lines 186-190) to:

```python
scan_paths = [
    config.workspace_path / "skills",
    config.agent_path / "skills",
    _BUNDLED_SKILLS_DIR,
    *_resolve_extra_skill_paths(config),
]
```

Update the docstring (lines 179-185) to mention the 4th tier and that externals slot below bundled.

**Tests to add** in `tests/test_skills.py` (in the `discover_skills` section, after `test_discover_honors_auto_approve_on_bundled`):

```python
def test_discover_includes_extra_skill_path(tmp_path, config):
    """A skill in extra_skill_paths is discovered."""
    extra = tmp_path / "external"
    _write_skill(extra / "ext-only", "name: ext-only\ndescription: External skill.")
    config.extra_skill_paths = [str(extra)]

    skills = discover_skills(config)
    assert any(s.name == "ext-only" for s in skills)


def test_discover_extra_path_does_not_shadow_bundled(tmp_path, config):
    """An external skill named the same as a bundled skill loses to bundled."""
    extra = tmp_path / "external"
    # `vault` is a real bundled skill — confirm via discover_skills output below.
    _write_skill(extra / "vault", "name: vault\ndescription: Imposter vault.")
    config.extra_skill_paths = [str(extra)]

    skills = discover_skills(config)
    vaults = [s for s in skills if s.name == "vault"]
    assert len(vaults) == 1
    assert vaults[0].description != "Imposter vault."  # bundled wins


def test_discover_extra_path_relative_anchored_to_agent(tmp_path, config):
    """A relative entry resolves under config.agent_path."""
    rel_dir = config.agent_path / "external"
    _write_skill(rel_dir / "rel-skill", "name: rel-skill\ndescription: Relative.")
    config.extra_skill_paths = ["external"]

    skills = discover_skills(config)
    assert any(s.name == "rel-skill" for s in skills)


def test_discover_extra_path_expands_user(tmp_path, config, monkeypatch):
    """A leading ~ expands to $HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    extra = tmp_path / "shared-skills"
    _write_skill(extra / "homed", "name: homed\ndescription: Tilde skill.")
    config.extra_skill_paths = ["~/shared-skills"]

    skills = discover_skills(config)
    assert any(s.name == "homed" for s in skills)


def test_discover_extra_path_expands_envvar(tmp_path, config, monkeypatch):
    """$VAR substrings are expanded via os.path.expandvars."""
    monkeypatch.setenv("MY_SKILLS_ROOT", str(tmp_path / "myroot"))
    extra = tmp_path / "myroot" / "skills"
    _write_skill(extra / "expanded", "name: expanded\ndescription: Var skill.")
    config.extra_skill_paths = ["$MY_SKILLS_ROOT/skills"]

    skills = discover_skills(config)
    assert any(s.name == "expanded" for s in skills)


def test_discover_workspace_and_agent_shadow_extra_path(tmp_path, config):
    """Workspace and admin skills both override same-named external skills."""
    extra = tmp_path / "external"
    _write_skill(extra / "shared", "name: shared\ndescription: External version.")
    _write_skill(
        config.agent_path / "skills" / "shared",
        "name: shared\ndescription: Admin version.",
    )
    config.extra_skill_paths = [str(extra)]

    skills = discover_skills(config)
    matching = [s for s in skills if s.name == "shared"]
    assert len(matching) == 1
    assert matching[0].description == "Admin version."

    # Now also add a workspace version — it should beat the admin version.
    _write_skill(
        config.workspace_path / "skills" / "shared",
        "name: shared\ndescription: Workspace version.",
    )
    skills = discover_skills(config)
    matching = [s for s in skills if s.name == "shared"]
    assert len(matching) == 1
    assert matching[0].description == "Workspace version."


def test_discover_extra_path_skipped_when_missing(tmp_path, config, caplog):
    """A non-existent extra path is silently skipped (no error)."""
    config.extra_skill_paths = [str(tmp_path / "does-not-exist")]
    skills = discover_skills(config)  # must not raise
    # Should not log a warning for the missing path
    assert all("does-not-exist" not in r.message for r in caplog.records
               if r.levelname == "WARNING")
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (2250 passed)
- [x] `make check` passes
- [x] `uv run pytest tests/test_skills.py -v -k "extra"` shows the 7 new tests passing

**Verification — manual:**
- [x] Field loads from defaults: `uv run python -c "from decafclaw.config import load_config; print(load_config().extra_skill_paths)"` prints `[]`
- [x] Env override works: `EXTRA_SKILL_PATHS=/tmp/decaf-ext-test uv run python -c "from decafclaw.config import load_config; print(load_config().extra_skill_paths)"` prints `['/tmp/decaf-ext-test']`
- [ ] (Out of scope, not blocking) `config show` doesn't print top-level scalar/list fields (pre-existing limitation — `default_model`, `providers`, etc. are also absent). Documented as a known gap if it matters; no fix in this PR.

---

## Phase 2: Trust posture + env var format coverage

End-to-end: external skills inherit the workspace trust posture exactly, and the env-var override accepts both JSON and comma-separated forms.

**Files:**
- Test: `tests/test_skills.py` — add tests; no production code changes (this phase verifies Phase 1's design relies on existing trust enforcement and the existing `_parse_list` helper)
- Test: `tests/test_config.py` (or wherever `load_config` env tests live — locate during execute and pick the closest existing pattern)

**Key changes:** None to production code. This phase is pure verification of the trust-and-parsing surface that Phase 1 inherits.

**Tests to add** in `tests/test_skills.py`:

```python
def test_discover_strips_auto_approve_from_extra_path_skill(tmp_path, config, caplog):
    """auto-approve on an external-path skill is stripped, same as workspace."""
    extra = tmp_path / "external"
    _write_skill(
        extra / "ext-auto",
        "name: ext-auto\ndescription: External.\nauto-approve: true",
    )
    config.extra_skill_paths = [str(extra)]
    with caplog.at_level("WARNING"):
        skills = discover_skills(config)
    matching = [s for s in skills if s.name == "ext-auto"]
    assert len(matching) == 1
    assert matching[0].auto_approve is False
    assert any("auto-approve" in r.message for r in caplog.records)


def test_discover_strips_always_loaded_from_extra_path_skill(tmp_path, config):
    """always-loaded on an external-path skill is not honored in catalog text."""
    extra = tmp_path / "external"
    _write_skill(
        extra / "ext-al",
        "name: ext-al\ndescription: Pretender.\nalways-loaded: true",
    )
    config.extra_skill_paths = [str(extra)]
    skills = discover_skills(config)
    catalog = build_catalog_text(skills)
    # ext-al should appear in the catalog but NOT in the Active Skills section
    # (which is bundled-only via is_relative_to(bundled_dir) check).
    assert "ext-al" in catalog
    if "## Active Skills" in catalog:
        active_block = catalog.split("## Active Skills")[1].split("##")[0]
        assert "ext-al" not in active_block
```

**Tests to add** in `tests/test_config.py`. Add to the existing `TestJsonFileLoading` class (which already uses `tmp_path / "decafclaw"` to match `AgentConfig.id` default and `monkeypatch.setenv("DATA_HOME", ...)`); also extend the `_isolate_env` autouse fixture (lines 19-31) to clear `EXTRA_SKILL_PATHS` so the test environment is hermetic.

In the `_isolate_env` fixture's prefix tuple at line 25-30, add `"EXTRA_SKILL_"` (so any pre-set env from the user's shell doesn't leak into tests). Specifically, the tuple becomes:

```python
if any(key.startswith(p) for p in (
    "LLM_", "MATTERMOST_", "COMPACTION_", "EMBEDDING_",
    "HEARTBEAT_", "HTTP_", "TABSTACK_", "CLAUDE_CODE_",
    "SKILLS_", "MEMORY_SEARCH", "SYSTEM_PROMPT",
    "NOTIFICATIONS_", "EMAIL_", "EXTRA_SKILL_",
)):
```

Then add tests inside `class TestJsonFileLoading`:

```python
def test_loads_extra_skill_paths_from_json(self, tmp_path, monkeypatch):
    """extra_skill_paths read from config.json as a list of strings."""
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({
        "extra_skill_paths": ["/opt/team-skills", "~/.claude/skills"],
    }))
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    c = load_config()
    assert c.extra_skill_paths == ["/opt/team-skills", "~/.claude/skills"]


def test_extra_skill_paths_env_comma_separated(self, tmp_path, monkeypatch):
    """EXTRA_SKILL_PATHS env var (comma-separated) overrides config.json."""
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({
        "extra_skill_paths": ["/from-json"],
    }))
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    monkeypatch.setenv("EXTRA_SKILL_PATHS", "/a,/b,/c")
    c = load_config()
    assert c.extra_skill_paths == ["/a", "/b", "/c"]


def test_extra_skill_paths_env_json_array(self, tmp_path, monkeypatch):
    """EXTRA_SKILL_PATHS env var accepts a JSON array."""
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    monkeypatch.setenv("EXTRA_SKILL_PATHS", '["/x", "/y"]')
    c = load_config()
    assert c.extra_skill_paths == ["/x", "/y"]


def test_extra_skill_paths_default_empty(self, tmp_path, monkeypatch):
    """Default is an empty list when neither env nor JSON set."""
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    c = load_config()
    assert c.extra_skill_paths == []
```

**Verification — automated:**
- [x] `make test` passes (2256 passed)
- [x] `uv run pytest tests/test_skills.py tests/test_config.py -v -k "extra_skill_paths or always_loaded_from_extra or strips_auto_approve_from_extra"` shows the 6 new tests passing

**Verification — manual:** none — Phase 1 already covers integration smoke; this phase is regression-net only.

---

## Phase 3: Documentation

End-to-end: a user reading `docs/skills.md` learns the field exists, sees the `npx skills` workflow, and understands the trust limitations. `CLAUDE.md` skills section gets a one-line pointer.

**Files:**
- Modify: `docs/skills.md` — update the "Skill directories" table to include the optional 4th tier; add a new subsection under "Using community skills" explaining the `npx skills` workflow
- Modify: `CLAUDE.md` — extend one bullet in the Skills section

**Key changes:**

In `docs/skills.md`, change the table at lines 228-232 to add a 4th row:

```markdown
| Priority | Location | Description |
|----------|----------|-------------|
| 1 | `data/{agent_id}/workspace/skills/` | Agent-writable. ClawHub installs land here. |
| 2 | `data/{agent_id}/skills/` | Admin-managed. |
| 3 | `src/decafclaw/skills/` | Bundled with the package. |
| 4 | Paths listed in `extra_skill_paths` config | Externally-managed (e.g., `npx skills add`). Lowest priority — cannot shadow bundled skills. |
```

Update the "Higher-priority skills override lower-priority ones with the same name." sentence to add: "External skills (tier 4) cannot shadow bundled skills, but workspace and admin skills can shadow them."

After the existing "Using community skills" section (around line 293), insert a new subsection:

```markdown
### Installing skills via `npx skills`

The [`vercel-labs/skills`](https://www.npmjs.com/package/skills) CLI installs skills from GitHub/GitLab/git URLs into per-agent paths. To wire it into decafclaw:

1. Install with any compatible agent target — `claude-code` is convenient since the path matches a common Claude Code setup:

   ```bash
   npx skills add vercel-labs/agent-skills -a claude-code -g
   # skills land in ~/.claude/skills/<name>/
   ```

2. Add the install location to the agent's `data/{agent_id}/config.json`:

   ```json
   { "extra_skill_paths": ["~/.claude/skills"] }
   ```

   Or set `EXTRA_SKILL_PATHS=~/.claude/skills` in the environment. Multiple paths are supported (JSON array or comma-separated).

3. Restart decafclaw or run `refresh_skills`.

Path entries support `~` and `$VAR` expansion. Relative paths resolve against `data/{agent_id}/`.

**Trust posture for external skills.** External skills are treated identically to workspace skills:

- `auto-approve: true` is ignored (warning logged) — every activation requires confirmation
- `always-loaded: true` is ignored — externals stay lazy-loaded
- `schedule:` frontmatter is ignored — only bundled and admin-level skills can self-schedule
- `user-invocable: true` and Python `tools.py` work normally

Skills authored against the standard Agent Skills format (`SKILL.md` only) work as-is. Skills authored for decafclaw with a `tools.py` extension are decafclaw-specific and won't run in other agents.
```

In `CLAUDE.md`, extend the Skills bullet at line 50:

```markdown
- **Bundled in `src/decafclaw/skills/`**. Each: SKILL.md (required) + `tools.py` (optional). Scan order: workspace > agent-level > bundled > `extra_skill_paths` (configured external dirs, e.g. for `npx skills add`).
```

**Verification — automated:**
- [x] `make lint` passes (sanity)
- [x] `make test` passes (sanity, 2256 passed)

**Verification — manual:**
- [ ] `docs/skills.md` renders correctly (no broken markdown table)
- [ ] The new "Installing skills via `npx skills`" subsection reads cleanly end-to-end
- [ ] `CLAUDE.md` skills bullet still scans as one logical sentence
