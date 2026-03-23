# Skills Config Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move skill config ownership from core `config_types.py` into each skill's `tools.py`, making `config.skills` a freeform dict resolved at activation time.

**Architecture:** Skills export a `SkillConfig` dataclass from their `tools.py`. The skill loader discovers it at activation time, resolves it via the existing `load_sub_config` machinery (env vars + JSON + defaults), and passes it as a second argument to `init(config, skill_config)`. Core config no longer knows about any specific skill.

**Tech Stack:** Python dataclasses, existing `_load_sub_config` pattern.

---

### Task 1: Make `_load_sub_config` public

**Files:**
- Modify: `src/decafclaw/config.py:80` — rename function
- Modify: `src/decafclaw/config.py` — update all internal callers

- [ ] **Step 1: Rename `_load_sub_config` to `load_sub_config`**

In `src/decafclaw/config.py`, rename the function definition at line 80 and update all call sites within the file (there are ~8 calls in `load_config()`).

- [ ] **Step 2: Run tests**

Run: `make check && make test`
Expected: All pass — no external callers yet.

- [ ] **Step 3: Commit**

```bash
git add src/decafclaw/config.py
git commit -m "refactor: make load_sub_config public for skill loader use"
```

---

### Task 2: Change `Config.skills` from `SkillsConfig` to `dict`

**Files:**
- Modify: `src/decafclaw/config.py:143` — change field type
- Modify: `src/decafclaw/config.py:258-264` — change skills loading
- Modify: `src/decafclaw/config_types.py` — remove `SkillsConfig`, `TabstackConfig`, `ClaudeCodeConfig`

- [ ] **Step 1: Remove skill dataclasses from `config_types.py`**

Delete `TabstackConfig`, `ClaudeCodeConfig`, and `SkillsConfig` classes (lines 125-148). Keep `is_secret` and `get_env_alias` — they're used generically.

- [ ] **Step 2: Update `Config.skills` field type**

In `config.py`, change:
```python
skills: SkillsConfig = field(default_factory=SkillsConfig)
```
to:
```python
skills: dict[str, dict[str, Any]] = field(default_factory=dict)
```

Remove the `SkillsConfig` import. Add `Any` to the typing imports.

- [ ] **Step 3: Update skills loading in `load_config()`**

Replace the skills loading block (lines 258-264):
```python
skills_data = file_data.get("skills", {})
tabstack = _load_sub_config(...)
claude_code = _load_sub_config(...)
skills = SkillsConfig(tabstack=tabstack, claude_code=claude_code)
```

With:
```python
skills = file_data.get("skills", {})
```

Just pass through the raw dict. Resolution happens at skill activation time.

- [ ] **Step 4: Run tests**

Run: `make check && make test`
Expected: All pass. The skill `init()` functions are already broken (accessing non-existent attributes), so this doesn't regress anything.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/config.py src/decafclaw/config_types.py
git commit -m "refactor: change Config.skills from SkillsConfig to freeform dict"
```

---

### Task 3: Update `_call_init` to resolve skill config

**Files:**
- Modify: `src/decafclaw/tools/skill_tools.py:57-65` — config-aware init
- Create: `tests/test_skill_config.py` — new tests

- [ ] **Step 1: Write failing tests**

```python
# tests/test_skill_config.py
"""Tests for skill config resolution at activation time."""

import dataclasses
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from decafclaw.tools.skill_tools import _call_init


@dataclass
class MockSkillConfig:
    api_key: str = field(default="", metadata={"secret": True, "env_alias": "MOCK_API_KEY"})
    timeout: int = 30


@pytest.mark.asyncio
async def test_call_init_with_skill_config(ctx, monkeypatch):
    """init(config, skill_config) is called when module has SkillConfig."""
    received = {}

    def mock_init(config, skill_config):
        received["config"] = config
        received["skill_config"] = skill_config

    module = MagicMock()
    module.init = mock_init
    module.SkillConfig = MockSkillConfig

    # Put config data in ctx.config.skills
    ctx.config = dataclasses.replace(ctx.config, skills={"mock": {"api_key": "test-key"}})

    await _call_init(module, ctx.config, "mock")
    assert received["skill_config"].api_key == "test-key"
    assert received["skill_config"].timeout == 30  # default


@pytest.mark.asyncio
async def test_call_init_with_env_override(ctx, monkeypatch):
    """Env vars override JSON config for skill config."""
    received = {}

    def mock_init(config, skill_config):
        received["skill_config"] = skill_config

    module = MagicMock()
    module.init = mock_init
    module.SkillConfig = MockSkillConfig

    monkeypatch.setenv("MOCK_API_KEY", "env-key")
    ctx.config = dataclasses.replace(ctx.config, skills={"mock": {"api_key": "json-key"}})

    await _call_init(module, ctx.config, "mock")
    assert received["skill_config"].api_key == "env-key"


@pytest.mark.asyncio
async def test_call_init_without_skill_config(ctx):
    """init(config) is called when module has no SkillConfig (backward compat)."""
    received = {}

    def mock_init(config):
        received["config"] = config

    module = MagicMock()
    module.init = mock_init
    del module.SkillConfig  # MagicMock auto-creates attrs; explicitly remove

    await _call_init(module, ctx.config, "mock")
    assert "config" in received


@pytest.mark.asyncio
async def test_call_init_no_init_function(ctx):
    """No-op when module has no init()."""
    module = MagicMock()
    del module.init

    await _call_init(module, ctx.config, "mock")  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_skill_config.py -v`
Expected: FAIL — `_call_init` doesn't accept `skill_name` yet.

- [ ] **Step 3: Update `_call_init` signature and implementation**

Change `_call_init` in `skill_tools.py`:

```python
async def _call_init(module, config, skill_name: str = "") -> None:
    """Call module.init() with config and optional skill config.

    If the module exports a SkillConfig dataclass, resolve it from
    config.skills[skill_name] + env vars, then call init(config, skill_config).
    Otherwise call init(config) for backward compat.
    """
    init_fn = getattr(module, "init", None)
    if init_fn is None:
        return

    skill_config_cls = getattr(module, "SkillConfig", None)
    if skill_config_cls is not None and skill_name:
        from ..config import load_sub_config
        raw = config.skills.get(skill_name, {})
        prefix = f"SKILLS_{skill_name.upper().replace('-', '_')}"
        skill_config = load_sub_config(skill_config_cls, raw, prefix)
        if asyncio.iscoroutinefunction(init_fn):
            await init_fn(config, skill_config)
        else:
            await asyncio.to_thread(init_fn, config, skill_config)
    else:
        if asyncio.iscoroutinefunction(init_fn):
            await init_fn(config)
        else:
            await asyncio.to_thread(init_fn, config)
```

- [ ] **Step 4: Update call sites to pass `skill_name`**

In `activate_skill_internal` (line 153):
```python
await _call_init(module, ctx.config, skill_info.name)
```

In `restore_skills` (line 90):
```python
await _call_init(module, ctx.config, name)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_skill_config.py -v && make test`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/tools/skill_tools.py tests/test_skill_config.py
git commit -m "feat: resolve skill config at activation time via SkillConfig dataclass"
```

---

### Task 4: Move `TabstackConfig` into tabstack skill and update `init()`

**Files:**
- Modify: `src/decafclaw/skills/tabstack/tools.py` — add `SkillConfig`, update `init()`

- [ ] **Step 1: Add `SkillConfig` dataclass to tabstack tools.py**

At the top of the file (after imports), add:
```python
from dataclasses import dataclass, field

@dataclass
class SkillConfig:
    api_key: str = field(default="", metadata={"secret": True, "env_alias": "TABSTACK_API_KEY"})
    api_url: str = field(default="", metadata={"env_alias": "TABSTACK_API_URL"})
```

- [ ] **Step 2: Update `init()` to use new signature**

Change:
```python
def init(config):
    global _client
    api_key = config.tabstack_api_key
    api_url = config.tabstack_api_url or None
```

To:
```python
def init(config, skill_config: SkillConfig):
    global _client
    api_key = skill_config.api_key
    api_url = skill_config.api_url or None
```

- [ ] **Step 3: Run tests**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add src/decafclaw/skills/tabstack/tools.py
git commit -m "feat: move TabstackConfig into tabstack skill as SkillConfig"
```

---

### Task 5: Move `ClaudeCodeConfig` into claude_code skill and update `init()`

**Files:**
- Modify: `src/decafclaw/skills/claude_code/tools.py` — add `SkillConfig`, update `init()` and tool functions

- [ ] **Step 1: Add `SkillConfig` dataclass**

At the top of `tools.py` (after imports):
```python
from dataclasses import dataclass, field

@dataclass
class SkillConfig:
    model: str = field(default="", metadata={"env_alias": "CLAUDE_CODE_MODEL"})
    budget_default: float = field(default=2.0, metadata={"env_alias": "CLAUDE_CODE_BUDGET_DEFAULT"})
    budget_max: float = field(default=10.0, metadata={"env_alias": "CLAUDE_CODE_BUDGET_MAX"})
    session_timeout: str = field(default="30m", metadata={"env_alias": "CLAUDE_CODE_SESSION_TIMEOUT"})
```

- [ ] **Step 2: Update `init()` and global state**

Change `_config` global to `_skill_config` for clarity. Update `init()`:

```python
_skill_config: SkillConfig | None = None

def init(config, skill_config: SkillConfig):
    global _config, _skill_config, _session_manager
    _config = config
    _skill_config = skill_config

    from decafclaw.heartbeat import parse_interval
    timeout_sec = parse_interval(skill_config.session_timeout) or 1800

    _session_manager = SessionManager(
        timeout_sec=timeout_sec,
        budget_default=skill_config.budget_default,
        budget_max=skill_config.budget_max,
    )
```

- [ ] **Step 3: Update tool functions that read `_config.claude_code_*`**

Line 83 — change:
```python
_config.claude_code_model
```
to:
```python
_skill_config.model if _skill_config else None
```

Line 135 — change:
```python
_config.claude_code_model if _config else None
```
to:
```python
_skill_config.model if _skill_config else None
```

- [ ] **Step 4: Run tests**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/skills/claude_code/tools.py
git commit -m "feat: move ClaudeCodeConfig into claude_code skill as SkillConfig"
```

---

### Task 6: Update config CLI for dict-based skills

**Files:**
- Modify: `src/decafclaw/config_cli.py` — handle skills as raw dict, remove skill entries from `_ENV_TO_PATH`
- Modify: `tests/test_config_cli.py` — update/add tests

- [ ] **Step 1: Update `cmd_show` to handle dict-based skills**

In `cmd_show`, after the existing dataclass group loop, add handling for the `skills` field as a raw dict (similar to the existing `env` handling):

```python
# Show skills section (raw dict, no schema)
valid_groups.add("skills")
if not args.group or args.group == "skills":
    for skill_name in sorted(config.skills):
        skill_data = config.skills[skill_name]
        if isinstance(skill_data, dict):
            for key in sorted(skill_data):
                print(f"skills.{skill_name}.{key} = ****")
        else:
            print(f"skills.{skill_name} = {skill_data}")
```

Note: All skill config values shown as `****` since we can't know which are secrets without the schema. Use `--reveal` to show them.

Update the reveal case to show actual values when `--reveal` is set.

- [ ] **Step 2: Update `cmd_get` to handle `skills.*` paths**

Add handling for `skills.*` paths (similar to `env.*`):

```python
if args.path.startswith("skills."):
    parts = args.path.split(".", 2)  # skills.tabstack.api_key
    if len(parts) == 2:
        # skills.tabstack — show whole dict
        skill_data = config.skills.get(parts[1], {})
        print(json.dumps(skill_data, indent=2))
    elif len(parts) == 3:
        skill_data = config.skills.get(parts[1], {})
        if parts[2] in skill_data:
            print(skill_data[parts[2]])
        else:
            print(f"Unknown config path: {args.path}", file=sys.stderr)
            sys.exit(1)
    return
```

- [ ] **Step 3: Update `cmd_set` to handle `skills.*` paths**

`cmd_set` calls `_resolve_field` for validation, which fails for dict-based paths. Add `skills.*` as freeform (like `env.*`):

```python
is_env = args.path.startswith("env.")
is_skills = args.path.startswith("skills.")
if not is_env and not is_skills:
    resolved = _resolve_field(config, args.path)
    ...
    value = _coerce_cli_value(field_info, args.value)
else:
    value = args.value
```

Skills values are stored as raw strings in JSON. No type coercion since the schema isn't known.

- [ ] **Step 4: Remove skill entries from `_ENV_TO_PATH`**

Remove lines 221-226 (the `TABSTACK_*` and `CLAUDE_CODE_*` entries).

- [ ] **Step 5: The `skills` field is now a dict, not a dataclass — exclude from the dataclass group loop**

In `cmd_show`, add `"skills"` to the skip list alongside `"system_prompt"` and `"discovered_skills"`:

```python
if group_field.name in ("system_prompt", "discovered_skills", "skills", "env"):
    continue
```

(The `env` and `skills` sections are handled separately below the loop.)

- [ ] **Step 6: Run tests**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add src/decafclaw/config_cli.py tests/test_config_cli.py
git commit -m "feat: update config CLI to handle dict-based skills section"
```

---

### Task 7: Final cleanup and docs

**Files:**
- Modify: `CLAUDE.md` — update conventions about skill config
- Modify: `src/decafclaw/config.py` — remove unused imports

- [ ] **Step 1: Remove unused imports from config.py**

Remove imports of `TabstackConfig`, `ClaudeCodeConfig`, `SkillsConfig` from `config.py`.

- [ ] **Step 2: Update CLAUDE.md**

Add to conventions:
- "Skill config via `SkillConfig` dataclass in `tools.py`." — skills own their config schema by exporting a `SkillConfig` dataclass. The loader resolves it at activation time via `load_sub_config`.
- Update key files to note that `config_types.py` no longer contains skill configs.

- [ ] **Step 3: Run full check**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md src/decafclaw/config.py
git commit -m "docs: update conventions for skill-owned config pattern"
```
