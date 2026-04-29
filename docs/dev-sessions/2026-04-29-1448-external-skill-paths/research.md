# Skill Discovery & Configuration System: Research Notes

## 1. Skill Discovery Flow

### Scan Path Precedence
`discover_skills()` in `src/decafclaw/skills/__init__.py:178-242` builds scan list in this order (line 186-189):
1. **Workspace skills**: `config.workspace_path / "skills"` (highest priority)
2. **Agent-level skills**: `config.agent_path / "skills"`  
3. **Bundled skills**: `_BUNDLED_SKILLS_DIR` (lowest priority)

Each path is scanned via `iterdir()` (line 200); first skill name found wins (line 232-236).

### SkillInfo Data Type
`src/decafclaw/skills/__init__.py:17-38` defines `@dataclass SkillInfo`:
- `name`, `description`, `location`, `body` (markdown content after frontmatter)
- `has_native_tools`, `requires_env`, `user_invocable`, `allowed_tools`, `shell_patterns`
- `context` ("inline" or "fork"), `argument_hint`, `model`, `requires_skills`
- `always_loaded`, `schedule` (cron expr), `enabled`, `auto_approve`

**No "source" or "trust" field in dataclass.** Trust boundary is determined post-hoc at runtime:
- Line 215-223: `auto_approve` stripped from non-bundled skills (admin/workspace) via `is_relative_to(bundled_dir)`
- Bundled status checked via resolved path comparison; not stored in SkillInfo

### Trust Boundary / auto-approve
Line 214-223: Bundled-only enforcement for `auto_approve` flag:
```python
if info.auto_approve:
    is_bundled = skill_dir.resolve().is_relative_to(bundled_dir)
    if not is_bundled:
        log.warning("Ignoring 'auto-approve: true' on non-bundled skill...")
        info.auto_approve = False
```

Same pattern applies to `always_loaded` in `src/decafclaw/prompts/__init__.py:98` — only bundled skills allowed.

### Duplicate Resolution
Line 232-236: When same skill name found in multiple paths, **first one wins** (workspace > agent > bundled):
```python
if info.name in seen_names:
    log.debug(f"Skill '{info.name}' at {skill_dir} shadowed by {seen_names[info.name]}")
    continue
```

### Environment Variable Dependencies
Line 225-229: Skills with unmet `requires_env` are skipped entirely:
```python
missing_env = [v for v in info.requires_env if not os.environ.get(v)]
if missing_env:
    log.debug(f"Skipping skill '{info.name}': missing env vars {missing_env}")
    continue
```

---

## 2. Config Field Plumbing (End-to-End)

### Config Loading Flow
`src/decafclaw/config.py:338-502` (`load_config()`) implements 3-tier resolution:
1. Environment variables (systematic name `PREFIX_FIELDNAME` + metadata alias)
2. `data/{agent_id}/config.json` values
3. Dataclass defaults

### Example: vault_path (list[str] = str case)
- **Declared**: `src/decafclaw/config_types.py:204` — `vault_path: str = "workspace/vault/"`
- **Loaded**: Line 432-433 in `config.py` via `load_sub_config(VaultConfig, ...)`
- **Resolution**: `config.property vault_root` (line 210-213) resolves relative → absolute:
  ```python
  @property
  def vault_root(self) -> Path:
      p = Path(self.vault.vault_path)
      return p if p.is_absolute() else self.agent_path / p
  ```
- **No tilde/HOME expansion** — uses `is_absolute()` check; relative paths anchored to `agent_path`

### Example: email.allowed_recipients (list[str])
- **Declared**: `src/decafclaw/config_types.py:279` — `allowed_recipients: list[str] = field(default_factory=list)`
- **Loaded**: Line 438-439 in `config.py` via nested `load_sub_config(EmailConfig, ...)`
- **Parsed from JSON** directly (line 138-141 in `load_sub_config()`); env var support via `_parse_list()` (line 54-66)
- **Env override**: `EMAIL_ALLOWED_RECIPIENTS` as comma-separated or JSON array

### Generic Sub-config Loader
`src/decafclaw/config.py:87-144` (`load_sub_config()`) for each field:
1. Check env var `{PREFIX}_{FIELD_UPPER}` (skip if prefix empty)
2. Check metadata `env_alias` or dict alias
3. Check JSON file value
4. Fall through to dataclass default

Type coercion via `_coerce()` (line 69-80):
- `list` → `_parse_list()` tries JSON parse, falls back to comma-split
- `bool` → `_parse_bool()` checks "true"/"1"/"yes"
- `int`/`float` → direct cast
- Other → string

---

## 3. Existing "Extra Paths" Patterns

**NONE FOUND EXPLICITLY.** However, related patterns:

### vault_path (string, relative)
`src/decafclaw/config_types.py:204`: User can override via `config.json` or `VAULT_VAULT_PATH` env var.
Path is string, not `list[Path]`. Resolution happens at property access time via `is_absolute()` check.

### mcp_servers.json (hardcoded location)
`src/decafclaw/mcp_client.py:91-98` (`load_mcp_config()`):
```python
path = config.agent_path / "mcp_servers.json"
```
No configurable search path; single file location only.

### Skill search (hardcoded 3 locations)
`src/decafclaw/skills/__init__.py:186-189`: Three fixed paths only; no user-configurable append list.

### Vault is closest model
`src/decafclaw/config_types.py:203-206` allows a single path string in config:
```python
@dataclass
class VaultConfig:
    vault_path: str = "workspace/vault/"  # user can override
    agent_folder: str = "agent/"
```
Relative paths are anchored to `agent_path` (not `workspace_path`). No `~` expansion.

---

## 4. Skill Activation, Scheduling, and User-Invokable Triggers

### Prompt Assembly
`src/decafclaw/prompts/__init__.py:36-115` (`load_system_prompt()`):
- Line 82: calls `discover_skills(config)`
- Line 83: builds catalog via `build_catalog_text(skills)`
- Line 88-108: appends **bundled always-loaded** skill bodies to system prompt (trust boundary at line 98)

### Schedule Discovery
`src/decafclaw/schedules.py:99-159` (`discover_schedules()`):
- Scans `config.agent_path / "schedules"` (admin) and `config.workspace_path / "schedules"`
- Also re-discovers scheduled skills from bundled + admin dirs only (line 125-150)
- Line 139: extracts `skill.schedule` field; converts to `ScheduleTask` with `source="bundled"` or `"admin"`
- **Workspace skills NOT scanned for schedule** (security boundary)

### User Commands
`src/decafclaw/commands.py:7-9, 80-105`:
- `find_command(name, discovered_skills)` finds by name if `user_invocable=True` (line 287-291 in skills/__init__.py)
- `list_commands(discovered_skills)` returns sorted on-demand skills (line 294-299)
- `format_help(discovered_skills)` includes all user-invocable skills + MCP prompts

### Consumer Call Sites
1. **Prompts** (`src/decafclaw/prompts/__init__.py:82`): Calls `discover_skills()` once per prompt load
2. **Schedules** (`src/decafclaw/schedules.py:138`): Calls `parse_skill_md()` directly per scheduled skill
3. **Commands** (`src/decafclaw/commands.py:287-290`): Uses pre-discovered skill list from context
4. **Eval** (`src/decafclaw/eval/runner.py`): Calls `discover_skills()` to build loadout for toolchoice testing

No caching of discovery results; rediscovered on each prompt load / schedule poll.

---

## 5. Tests and Fixtures

### Test File
`tests/test_skills.py` (547 lines) covers:

**Fixture Pattern**: Uses `config` fixture from `conftest.py:20-29`:
```python
@pytest.fixture
def config(tmp_data):
    return Config(
        agent=AgentConfig(data_home=str(tmp_data), id="test-agent", user_id="testuser"),
        ...
    )
```
Creates temp `data/test-agent/{workspace,skills}` structure automatically via Config properties.

**Helper**: `_write_skill(skill_dir, frontmatter, body, tools_py)` (line 17-22) writes SKILL.md and optional tools.py.

**Discover Tests** (line 254-360):
- `test_discover_from_single_dir()`: writes skills to `config.workspace_path / "skills"`
- `test_discover_priority_ordering()`: creates same-named skill in workspace + agent; asserts workspace wins
- `test_discover_skips_unmet_requires()`: uses `monkeypatch.delenv()` to simulate missing `requires_env`
- `test_discover_strips_auto_approve_from_workspace_skill()`: creates skill with `auto-approve: true` in workspace, asserts flag stripped + warning logged
- `test_discover_honors_auto_approve_on_bundled()`: asserts bundled `background` and `mcp` skills retain `auto_approve=True`

**Setup Pattern**: All tests create skill directories under `config.agent_path` or `config.workspace_path` 
paths computed from the tmp fixture; no monkeypatching of `_BUNDLED_SKILLS_DIR` (tests rely on real bundled skills).

---

## Summary

**No list[Path] config fields exist today.** Closest pattern: `vault_path` (single string). All paths use relative-to-anchor model (`agent_path` / `workspace_path`) with no `~` or `$HOME` expansion. Skills hardcoded to 3 scan locations; no configurable append list. Trust boundaries enforced post-hoc via `is_relative_to(bundled_dir)` on resolved paths, not via stored metadata. Tests use tmp_path fixture + _write_skill() helpers with real bundled skill fallback.
