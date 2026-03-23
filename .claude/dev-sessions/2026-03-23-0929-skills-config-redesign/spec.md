# Spec: Skills Config Redesign

**Branch:** `skills-config-redesign`

## Problem

Skill configuration is hardcoded into the core config system. `SkillsConfig`, `TabstackConfig`, and `ClaudeCodeConfig` are typed dataclasses in `config_types.py`, meaning adding a new skill requires editing core code. Worse, the skill `init()` functions currently reference non-existent attributes (`config.tabstack_api_key` instead of `config.skills.tabstack.api_key`) — the migration from the flexible-config session was never completed, so skills can't actually read their config.

## Solution

Make `config.skills` a freeform `dict[str, dict]` on the Config object. Each skill owns its config schema by exporting a `SkillConfig` dataclass from its `tools.py`. The skill loader resolves the raw dict against the skill's dataclass at activation time using the existing `_load_sub_config` machinery (env var overrides, secret masking, type coercion).

## Design

### Config object change

`Config.skills` becomes `dict[str, dict[str, Any]]` — raw data from config.json's `"skills"` section. No more `SkillsConfig` wrapper or typed sub-dataclasses in core.

```python
@dataclass
class Config:
    # ... existing groups ...
    skills: dict[str, dict[str, Any]] = field(default_factory=dict)
```

### Skill-owned config dataclasses

Each skill with native tools can export a `SkillConfig` dataclass from its `tools.py`:

**`skills/tabstack/tools.py`:**
```python
@dataclass
class SkillConfig:
    api_key: str = field(default="", metadata={"secret": True, "env_alias": "TABSTACK_API_KEY"})
    api_url: str = field(default="", metadata={"env_alias": "TABSTACK_API_URL"})
```

**`skills/claude_code/tools.py`:**
```python
@dataclass
class SkillConfig:
    model: str = field(default="", metadata={"env_alias": "CLAUDE_CODE_MODEL"})
    budget_default: float = field(default=2.0, metadata={"env_alias": "CLAUDE_CODE_BUDGET_DEFAULT"})
    budget_max: float = field(default=10.0, metadata={"env_alias": "CLAUDE_CODE_BUDGET_MAX"})
    session_timeout: str = field(default="30m", metadata={"env_alias": "CLAUDE_CODE_SESSION_TIMEOUT"})
```

The convention is always `SkillConfig` — the loader discovers it via `getattr(module, "SkillConfig", None)`.

All `SkillConfig` fields must have defaults. Missing config (no JSON entry, no env var) results in defaults, not errors. Skills should validate required values in their `init()` and return a clear error if something critical is missing (e.g. an API key).

### Making `_load_sub_config` public

Rename `_load_sub_config` to `load_sub_config` in `config.py` and export it, since `skill_tools.py` needs to call it at activation time. This is the same pattern used for making `_read_last_heartbeat` public in the health command work.

### Skill loader change

In `skill_tools.py`, `_call_init` becomes config-aware:

1. After importing the skill module, check for `SkillConfig` on the module
2. If found, run `load_sub_config(SkillConfig, config.skills.get(skill_name, {}), f"SKILLS_{skill_name.upper()}")` to resolve the typed config with env var overrides
3. Call `init(config, skill_config)` with both the global config and the resolved skill config
4. If no `SkillConfig` exists, call `init(config)` with the original single-argument signature (backward compat for skills that don't need typed config)

**Skill name to config key mapping:** The config key is the skill's directory name (e.g. `tabstack`, `claude_code`), which is also `SkillInfo.name` from SKILL.md frontmatter. These must match exactly. The env var prefix normalizes to uppercase with underscores: `skill_name.upper().replace("-", "_")`.

**Both activation paths need this:** `activate_skill_internal` and `restore_skills` both call `_call_init` — the config-aware logic lives in `_call_init` itself, so both paths get it automatically.

### Config CLI changes

`config.skills` is now a raw dict, not a dataclass hierarchy. The CLI functions that walk Config fields (`_print_group`, `_resolve_field`, `cmd_get`, `cmd_set`) need to handle this:

- `config show skills` — pretty-print the raw dict as JSON, no secret masking (schema unknown)
- `config show skills.tabstack` — show that skill's raw dict
- `config get skills.tabstack.api_key` — return the raw value from the dict
- `config set skills.tabstack.api_key VALUE` — write to the dict in config.json

Remove skill-related entries from `_ENV_TO_PATH` since the mapping is now dynamic (env var → skill config happens at activation time, not import time).

### What gets removed

- `SkillsConfig` dataclass from `config_types.py`
- `TabstackConfig` dataclass from `config_types.py` (moves to `skills/tabstack/tools.py`)
- `ClaudeCodeConfig` dataclass from `config_types.py` (moves to `skills/claude_code/tools.py`)
- Skills loading block in `config.py` (`_load_sub_config` calls for tabstack/claude_code)
- Skill-related entries in `_ENV_TO_PATH` in `config_cli.py`
- Any references to `config.skills.tabstack` or `config.skills.claude_code` in core code

### Config.json format

Unchanged. The `"skills"` section remains a nested dict:

```json
{
  "skills": {
    "tabstack": {
      "api_key": "..."
    },
    "claude_code": {
      "model": "claude-opus-4",
      "budget_default": 5.0
    }
  }
}
```

### init() signature change

Skills update from `init(config)` to `init(config, skill_config)`:

```python
def init(config, skill_config: SkillConfig):
    global _client
    kwargs = {"api_key": skill_config.api_key}
    if skill_config.api_url:
        kwargs["base_url"] = skill_config.api_url
    _client = AsyncTabstack(**kwargs)
```

The loader detects the right signature — if the skill has `SkillConfig`, it passes both args; otherwise just `config`.

## Out of Scope

- Config file format changes (stays JSON)
- Migrating other JSON files (mcp_servers.json, etc.) into unified config
- Dynamic skill config reloading at runtime
- Schema validation in the config CLI for skill configs

## Acceptance Criteria

- `SkillsConfig`, `TabstackConfig`, `ClaudeCodeConfig` removed from `config_types.py`
- Each skill owns its config dataclass in its own `tools.py`
- Skill config resolved at activation time with env var overrides and secret masking
- `init(config, skill_config)` signature works for tabstack and claude_code skills
- Backward compat: skills without `SkillConfig` still work with `init(config)`
- Config CLI handles `config.skills` as raw dict (show/get/set work, no schema-based masking)
- `_ENV_TO_PATH` skill entries removed from config_cli.py
- `restore_skills` path also resolves skill config correctly
- Existing config.json format works without changes
- Tests cover: skill with SkillConfig activation, skill without SkillConfig, restore_skills, config CLI with dict-based skills
- All existing tests pass
