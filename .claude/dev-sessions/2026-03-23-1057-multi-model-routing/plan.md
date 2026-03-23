# Multi-Model Routing Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add effort-level-based model routing so different tasks use appropriate models — fast for procedural work, strong for complex reasoning, with user control via tool and commands.

**Architecture:** A `models` dict in config.json maps effort levels to partial LLM configs. A `resolve_effort()` helper merges an effort level's config over `config.llm` defaults. The agent loop forks config with the resolved LLM settings at the start of each turn. Effort is tracked on `ctx.effort` and persisted in conversation sidecars.

**Tech Stack:** Python dataclasses, existing config/context/archive patterns.

---

### Task 1: Add `models` config section and `resolve_effort()` helper

**Files:**
- Modify: `src/decafclaw/config.py` — add `models` field, load from JSON, add `resolve_effort()`
- Create: `tests/test_effort.py` — tests for effort resolution

- [ ] **Step 1: Write failing tests for `resolve_effort()`**

```python
# tests/test_effort.py
"""Tests for effort level resolution."""

import dataclasses

import pytest

from decafclaw.config import resolve_effort


def test_resolve_effort_known_level(config):
    """Known effort level merges model entry over config.llm."""
    config = dataclasses.replace(config, models={
        "strong": {"model": "gemini-2.5-pro"},
    })
    resolved = resolve_effort(config, "strong")
    assert resolved.model == "gemini-2.5-pro"
    assert resolved.url == config.llm.url  # inherits


def test_resolve_effort_with_url_override(config):
    """Effort entry can override url and api_key."""
    config = dataclasses.replace(config, models={
        "strong": {"model": "pro", "url": "https://other", "api_key": "sk-other"},
    })
    resolved = resolve_effort(config, "strong")
    assert resolved.model == "pro"
    assert resolved.url == "https://other"
    assert resolved.api_key == "sk-other"


def test_resolve_effort_unknown_level(config):
    """Unknown effort level falls back to config.llm."""
    resolved = resolve_effort(config, "unknown")
    assert resolved.model == config.llm.model


def test_resolve_effort_no_models_section(config):
    """Absent models section falls back to config.llm."""
    resolved = resolve_effort(config, "strong")
    assert resolved.model == config.llm.model


def test_resolve_effort_default(config):
    """'default' effort level uses the default model entry."""
    config = dataclasses.replace(config, models={
        "default": {"model": "flash"},
    })
    resolved = resolve_effort(config, "default")
    assert resolved.model == "flash"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_effort.py -v`
Expected: FAIL — `resolve_effort` doesn't exist, `Config` has no `models` field.

- [ ] **Step 3: Add `models` field to Config and loading in `load_config()`**

In `config.py`:
- Add `models: dict[str, dict[str, str]] = field(default_factory=dict)` to Config
- In `load_config()`, add: `models = file_data.get("models", {})` (with dict validation like skills)
- Pass `models=models` to the Config constructor

- [ ] **Step 4: Implement `resolve_effort()`**

```python
def resolve_effort(config, level: str) -> LlmConfig:
    """Resolve an effort level to a concrete LLM config.

    Merges config.models[level] over config.llm defaults.
    Unknown levels or absent models section falls back to config.llm.
    """
    from dataclasses import replace
    entry = config.models.get(level, {})
    if not entry:
        return config.llm
    return replace(
        config.llm,
        model=entry.get("model") or config.llm.model,
        url=entry.get("url") or config.llm.url,
        api_key=entry.get("api_key") or config.llm.api_key,
    )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_effort.py -v && make test`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/config.py tests/test_effort.py
git commit -m "feat: add models config section and resolve_effort() helper"
```

---

### Task 2: Add `ctx.effort` and wire into agent loop

**Files:**
- Modify: `src/decafclaw/context.py` — add `effort` field
- Modify: `src/decafclaw/agent.py` — resolve effort before LLM calls
- Modify: `tests/test_effort.py` — test agent loop uses effort

- [ ] **Step 1: Add `effort` field to Context**

In `context.py`, add to `__init__`:
```python
self.effort: str = "default"
```

- [ ] **Step 2: Wire effort resolution into `run_agent_turn`**

At the top of `run_agent_turn`, after skill restoration and before the main loop, resolve the effort level and fork config:

```python
# Resolve effort level to concrete LLM config
from .config import resolve_effort
resolved_llm = resolve_effort(config, ctx.effort)
if resolved_llm is not config.llm:
    config = replace(config, llm=resolved_llm)
    ctx.config = config
log.info(f"Agent turn: effort={ctx.effort}, model={config.llm.model}")
```

This happens once at turn start. The agent loop and streaming code use `config` throughout, so everything downstream picks up the resolved model.

- [ ] **Step 3: Write test for agent loop effort resolution**

```python
@pytest.mark.asyncio
async def test_agent_turn_uses_effort_model(ctx, monkeypatch):
    """Agent turn resolves effort level to the configured model."""
    ctx.config = dataclasses.replace(ctx.config, models={
        "strong": {"model": "gemini-2.5-pro"},
    })
    ctx.effort = "strong"

    # Mock call_llm to capture which model was used
    captured = {}
    async def mock_call_llm(config, messages, **kwargs):
        captured["model"] = config.llm.model
        return {"role": "assistant", "content": "ok", "tool_calls": None, "usage": {}}

    monkeypatch.setattr("decafclaw.agent.call_llm", mock_call_llm)
    monkeypatch.setattr("decafclaw.agent.call_llm_streaming", mock_call_llm)

    from decafclaw.agent import run_agent_turn
    await run_agent_turn(ctx, "hello", [])
    assert captured["model"] == "gemini-2.5-pro"
```

- [ ] **Step 4: Run tests**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/context.py src/decafclaw/agent.py tests/test_effort.py
git commit -m "feat: wire effort level resolution into agent loop"
```

---

### Task 3: Add `set_effort` tool

**Files:**
- Create: `src/decafclaw/tools/effort_tools.py` — tool implementation
- Modify: `src/decafclaw/tools/__init__.py` — register tool
- Modify: `tests/test_effort.py` — tests

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_set_effort_tool(ctx):
    """set_effort changes ctx.effort."""
    from decafclaw.tools.effort_tools import tool_set_effort
    result = await tool_set_effort(ctx, level="strong")
    assert ctx.effort == "strong"
    assert "strong" in result
```

- [ ] **Step 2: Implement `tool_set_effort`**

Create `src/decafclaw/tools/effort_tools.py`:

```python
"""Effort level tools — switch model complexity for the conversation."""

import logging

log = logging.getLogger(__name__)

VALID_EFFORTS = {"fast", "default", "strong"}


async def tool_set_effort(ctx, level: str) -> str:
    """Change the effort level for this conversation."""
    log.info(f"[tool:set_effort] level={level}")

    if level not in VALID_EFFORTS:
        from ..media import ToolResult
        return ToolResult(text=f"[error: unknown effort level '{level}'. Valid: {', '.join(sorted(VALID_EFFORTS))}]")

    ctx.effort = level

    # Resolve to show the user which model they're getting
    from ..config import resolve_effort
    resolved = resolve_effort(ctx.config, level)

    return f"Effort level set to **{level}** (model: {resolved.model}). This applies for the rest of this conversation."
```

Define `EFFORT_TOOLS` and `EFFORT_TOOL_DEFINITIONS` at the bottom, with `set_effort` as an always-loaded tool.

- [ ] **Step 3: Register in `tools/__init__.py`**

Import and merge `EFFORT_TOOLS` / `EFFORT_TOOL_DEFINITIONS`.

- [ ] **Step 4: Add to `DEFAULT_ALWAYS_LOADED` in `tool_registry.py`**

Add `"set_effort"` to the set — it needs to be available without activation.

- [ ] **Step 5: Run tests**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/tools/effort_tools.py src/decafclaw/tools/__init__.py src/decafclaw/tools/tool_registry.py tests/test_effort.py
git commit -m "feat: add set_effort tool for conversation model switching"
```

---

### Task 4: Persist effort level

**Files:**
- Modify: `src/decafclaw/persistence.py` — add effort read/write
- Modify: `src/decafclaw/tools/effort_tools.py` — persist on change
- Modify: `src/decafclaw/agent.py` — restore on turn start
- Modify: `tests/test_effort.py` — persistence tests

- [ ] **Step 1: Add effort persistence functions**

In `persistence.py`:

```python
def _effort_path(config, conv_id: str) -> Path:
    return config.workspace_path / "conversations" / f"{conv_id}.effort"


def write_effort(config, conv_id: str, level: str) -> None:
    path = _effort_path(config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(level)


def read_effort(config, conv_id: str) -> str:
    path = _effort_path(config, conv_id)
    if not path.exists():
        return "default"
    try:
        level = path.read_text().strip()
        return level if level else "default"
    except OSError:
        return "default"
```

- [ ] **Step 2: Persist effort in `tool_set_effort`**

After setting `ctx.effort`, persist it:

```python
from ..persistence import write_effort
conv_id = ctx.conv_id or ctx.channel_id
if conv_id:
    write_effort(ctx.config, conv_id, level)
```

- [ ] **Step 3: Restore effort in `run_agent_turn`**

In `agent.py`, alongside the skill state restoration (after line 380):

```python
from .persistence import read_effort
persisted_effort = read_effort(config, conv_id)
if persisted_effort != "default":
    ctx.effort = persisted_effort
```

- [ ] **Step 4: Write test for persistence round-trip**

```python
@pytest.mark.asyncio
async def test_effort_persists_across_turns(ctx):
    """Effort level persists and is restored on the next turn."""
    from decafclaw.persistence import read_effort, write_effort
    conv_id = ctx.conv_id
    write_effort(ctx.config, conv_id, "strong")
    assert read_effort(ctx.config, conv_id) == "strong"
```

- [ ] **Step 5: Run tests**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/persistence.py src/decafclaw/tools/effort_tools.py src/decafclaw/agent.py tests/test_effort.py
git commit -m "feat: persist effort level in conversation sidecar"
```

---

### Task 5: Add `effort` to skill frontmatter and command execution

**Files:**
- Modify: `src/decafclaw/skills/__init__.py` — parse `effort` field, add to SkillInfo
- Modify: `src/decafclaw/commands.py` — apply effort on forked skill execution
- Modify: `tests/test_effort.py` — tests

- [ ] **Step 1: Add `effort` field to SkillInfo**

In `skills/__init__.py`, add to the SkillInfo dataclass:
```python
effort: str = ""  # empty = inherit conversation effort
```

In `parse_skill_md()`, add:
```python
effort=meta.get("effort", ""),
```

- [ ] **Step 2: Apply effort in forked command execution**

In `commands.py`, when executing a fork-mode command, if `skill.effort` is set, apply it to the child context:

Look at `_run_child_turn` in `commands.py` (or wherever fork-mode commands call the child agent). Set `child_ctx.effort = skill.effort` before running the child turn.

- [ ] **Step 3: Write tests**

```python
def test_skill_effort_parsed():
    """Skill frontmatter effort field is parsed into SkillInfo."""
    from decafclaw.skills import parse_skill_md
    # Create a temp SKILL.md with effort field and test parsing
    ...
```

- [ ] **Step 4: Run tests**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/skills/__init__.py src/decafclaw/commands.py tests/test_effort.py
git commit -m "feat: add effort field to skill frontmatter for forked execution"
```

---

### Task 6: Add `effort` parameter to `delegate_task`

**Files:**
- Modify: `src/decafclaw/tools/delegate.py` — accept and apply effort parameter
- Modify: `tests/test_effort.py` — tests

- [ ] **Step 1: Add `effort` parameter to `tool_delegate_task` and `_run_child_turn`**

In `delegate.py`:
- `tool_delegate_task(ctx, task, effort="")` — pass effort through
- `_run_child_turn(parent_ctx, task, effort="")` — if effort is set, apply to child_ctx; otherwise inherit parent's effort

```python
child_ctx.effort = effort if effort else getattr(parent_ctx, "effort", "default")
```

- [ ] **Step 2: Update tool definition**

Add `effort` parameter to `DELEGATE_TOOL_DEFINITIONS`:
```python
"effort": {
    "type": "string",
    "description": "Effort level for the subtask (fast/default/strong). Omit to inherit parent's level.",
},
```

- [ ] **Step 3: Write test**

```python
@pytest.mark.asyncio
async def test_delegate_effort_parameter(ctx, monkeypatch):
    """delegate_task passes effort to child context."""
    ...
```

- [ ] **Step 4: Run tests**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/tools/delegate.py tests/test_effort.py
git commit -m "feat: add effort parameter to delegate_task"
```

---

### Task 7: Create `!think-harder`, `!think-faster`, `!think-normal` commands

**Files:**
- Create: `src/decafclaw/skills/think-harder/SKILL.md`
- Create: `src/decafclaw/skills/think-faster/SKILL.md`
- Create: `src/decafclaw/skills/think-normal/SKILL.md`

- [ ] **Step 1: Create the three skill directories and SKILL.md files**

`skills/think-harder/SKILL.md`:
```yaml
---
name: think-harder
description: Switch to a stronger, more capable model for this conversation
user-invocable: true
context: inline
allowed-tools: set_effort
---

Call `set_effort(level="strong")` and tell the user the result.
```

`skills/think-faster/SKILL.md`:
```yaml
---
name: think-faster
description: Switch to a faster, cheaper model for this conversation
user-invocable: true
context: inline
allowed-tools: set_effort
---

Call `set_effort(level="fast")` and tell the user the result.
```

`skills/think-normal/SKILL.md`:
```yaml
---
name: think-normal
description: Reset to the default model for this conversation
user-invocable: true
context: inline
allowed-tools: set_effort
---

Call `set_effort(level="default")` and tell the user the result.
```

- [ ] **Step 2: Run tests to verify skill discovery**

Run: `make check && make test`
Expected: All pass. Skills should be discovered by the skill scanner.

- [ ] **Step 3: Commit**

```bash
git add src/decafclaw/skills/think-harder/ src/decafclaw/skills/think-faster/ src/decafclaw/skills/think-normal/
git commit -m "feat: add !think-harder, !think-faster, !think-normal commands"
```

---

### Task 8: Reflection escalation nudge

**Files:**
- Modify: `src/decafclaw/agent.py` — append escalation suggestion after exhausted retries
- Modify: `tests/test_effort.py` — tests

- [ ] **Step 1: Add escalation nudge after reflection retries exhausted**

In `agent.py`, find where the agent delivers the response after reflection retries are exhausted (the `_should_reflect` returns False when `reflection_retries >= max_retries`). After the final response is assembled but before it's returned, check:

```python
if (reflection_retries >= config.reflection.max_retries
        and last_reflection and not last_reflection.passed
        and ctx.effort != "strong"):
    content += (
        "\n\n---\n*I'm not confident in this answer. "
        "Try `!think-harder` to retry with a more capable model.*"
    )
```

- [ ] **Step 2: Write test**

```python
@pytest.mark.asyncio
async def test_reflection_escalation_nudge(ctx, monkeypatch):
    """Reflection nudge appears when retries exhausted and not at strong."""
    ...

@pytest.mark.asyncio
async def test_reflection_no_nudge_at_strong(ctx, monkeypatch):
    """No nudge when already at strong effort."""
    ...
```

- [ ] **Step 3: Run tests**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add src/decafclaw/agent.py tests/test_effort.py
git commit -m "feat: suggest model escalation when reflection retries exhausted"
```

---

### Task 9: Docs update

**Files:**
- Modify: `CLAUDE.md` — add effort level conventions, key files
- Modify: `README.md` — add effort config docs if applicable

- [ ] **Step 1: Update CLAUDE.md**

Add to key files:
- `src/decafclaw/tools/effort_tools.py` — Effort level tool: set_effort
- `src/decafclaw/skills/think-harder/` etc. — Effort level user commands

Add to conventions:
- "Effort levels for model routing." Explain fast/default/strong, where they're specified, and the resolution order.

- [ ] **Step 2: Run lint**

Run: `make check`
Expected: Pass.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add effort level routing conventions and key files"
```
