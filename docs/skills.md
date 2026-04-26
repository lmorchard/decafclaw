# Skills System

DecafClaw supports the [Agent Skills](https://agentskills.io) open standard for modular, portable capability packages. Skills can provide shell-based tools (compatible with OpenClaw, Claude Code, and other agents) or native Python tools with structured function calling.

## How skills work

1. **Discovery**: on startup, DecafClaw scans skill directories for `SKILL.md` files
2. **Catalog**: skill names and descriptions are injected into the system prompt
3. **Activation**: the agent calls `activate_skill("name")` when it needs a skill's tools
4. **Confirmation**: user must approve activation (yes/no/always)
5. **Loading**: native Python skills register structured tools; shell-based skills provide instructions for the `shell` tool

## Skill format

Each skill is a directory containing at minimum a `SKILL.md` file:

```
my-skill/
  SKILL.md            # Required: YAML frontmatter + instructions
  tools.py            # Optional: native Python tool functions
  reference.md        # Optional: detailed docs (loaded on demand)
  scripts/            # Optional: CLI scripts for shell-based execution
```

### SKILL.md

YAML frontmatter followed by markdown instructions:

```yaml
---
name: my-skill
description: What this skill does (shown in the catalog).
requires:
  env:
    - MY_API_KEY
---

## Instructions

Instructions the agent follows after activation.
These are loaded into context only when the skill is activated.
```

### Frontmatter fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Lowercase alphanumeric + hyphens, max 64 chars |
| `description` | Yes | What the skill does, max 1024 chars. Shown in catalog. |
| `requires.env` | No | Env vars that must be set. Skill hidden if unmet. |
| `user-invocable` | No | Bool, default true. When true, the skill can also be triggered as a command (`!name` / `/name`) in addition to agent activation. Orthogonal to activation — a user-invocable skill still appears in the agent's skill catalog. |
| `context` | No | `inline` (default) or `fork`. Fork runs the skill as an isolated child turn. |
| `allowed-tools` | No | Comma-separated tool names pre-approved for this skill. |
| `model` | No | Named model config for `context: fork` skills. See [Model Selection](model-selection.md). |
| `argument-hint` | No | Hint text for command argument substitution. |
| `always-loaded` | No | Bool, default false. **Bundled-only.** Skill is auto-activated at startup and its tools are always present. |
| `schedule` | No | Cron expression. **Bundled and admin-only.** Runs the skill on a schedule. |
| `auto-approve` | No | Bool, default false. **Bundled-only.** Skill activates without a user confirmation prompt. User's explicit `"deny"` in `skill_permissions.json` still overrides. |

**Trust boundary:** `always-loaded`, `schedule`, and `auto-approve` are only honored for skills bundled under `src/decafclaw/skills/`. Admin-level (`data/{agent_id}/skills/`) and workspace-level skills declaring these fields get them silently stripped with a warning log. This prevents escalation from less-trusted skill locations.

### Native Python tools (tools.py)

Optional. If present, the skill registers structured tools that the LLM calls directly. This is a DecafClaw extension to the Agent Skills standard — the standard itself defines shell-based skills only. Native tools provide structured function calling with typed parameters, which is more reliable and efficient than shell-based execution.

```python
from dataclasses import dataclass, field

@dataclass
class SkillConfig:
    """Optional. Declares skill-specific config fields."""
    api_key: str = field(default="", metadata={"secret": True, "env_alias": "MY_API_KEY"})
    timeout: int = 30

TOOLS = {
    "my_tool": my_tool_function,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "my_tool",
            "description": "Does something useful",
            "parameters": { ... }
        }
    }
]

def init(config, skill_config: SkillConfig):
    """Optional. Called once on first activation. Can be async.

    If SkillConfig is exported, receives both global config and
    the resolved skill config. Without SkillConfig, receives
    just init(config) for backward compatibility.
    """
    pass

async def shutdown():
    """Optional. Called to clean up resources (e.g., close clients)."""
    pass
```

All tool functions receive `ctx` as first parameter.

### Dynamic tool loading (get_tools)

Skills can export a `get_tools(ctx)` function to supply different tools per turn based on runtime state. When present, the agent loop calls it at the start of each iteration, replacing the skill's tools and definitions dynamically. Skills without `get_tools` continue to use the static `TOOLS`/`TOOL_DEFINITIONS` dicts.

```python
def get_tools(ctx) -> tuple[dict, list]:
    """Return (tools_dict, tool_definitions) for this turn.

    Called once per agent loop iteration. Use ctx to read
    current state and return only the tools appropriate
    for the current workflow phase.
    """
    if some_condition(ctx):
        return {"tool_a": tool_a_fn}, [TOOL_A_DEF]
    else:
        return {"tool_b": tool_b_fn}, [TOOL_B_DEF]
```

The function must return the same shape as the static exports: a dict mapping names to callables, and a list of OpenAI-format tool definitions. The static `TOOLS` and `TOOL_DEFINITIONS` should still be present as the full set — they're used for the pre-load cache and as a fallback.

This is useful for workflow skills (like `project`) where different phases expose different tools, preventing the model from calling phase-inappropriate tools.

### Ending the agent turn (end_turn)

Tools can signal that the current turn should end by returning `ToolResult(text="...", end_turn=True)`. When any tool in a parallel execution batch sets `end_turn`, the agent loop:

1. Completes all tools in the current batch normally
2. Appends their results to history
3. Makes one final LLM call with an **empty tool list** (forcing text output)
4. Returns that text as the turn response

Use `end_turn` at workflow phase boundaries where the model should stop. Do NOT use it on every tool call — during execution phases, the model should chain freely.

### Confirmation gates (EndTurnConfirm)

For review gates where the user should approve or deny before continuing, return `EndTurnConfirm` as the `end_turn` value:

```python
from decafclaw.media import EndTurnConfirm, ToolResult

async def tool_submit_for_review(ctx) -> ToolResult:
    # ... advance to review state ...
    def on_approve():
        # Advance to next phase
        info.status = NextPhase
        save(info)

    def on_deny():
        # Revert to editing phase
        info.status = PrevPhase
        save(info)

    return ToolResult(
        text="Submitted for review.",
        end_turn=EndTurnConfirm(
            message="Click Approve to proceed or Needs Feedback to request changes.",
            approve_label="Approve",
            deny_label="Needs Feedback",
            on_approve=on_approve,
            on_deny=on_deny,
        ),
    )
```

The agent loop handles the confirmation via the event bus:
- **Approved:** calls `on_approve`, injects an approval note into history, and **continues the loop** — the model chains into the next phase
- **Denied:** calls `on_deny`, injects a denial note, makes a final no-tools LLM call, and **ends the turn**

`EndTurnConfirm` takes priority over `end_turn=True` if both appear in a parallel tool batch.

### Skill-owned config (SkillConfig)

Skills can declare their own configuration by exporting a `SkillConfig` dataclass from `tools.py`. The loader resolves it at activation time from `config.skills[skill_name]` in config.json, with env var overrides from field metadata.

```python
@dataclass
class SkillConfig:
    api_key: str = field(default="", metadata={"secret": True, "env_alias": "MY_API_KEY"})
    api_url: str = field(default="", metadata={"env_alias": "MY_API_URL"})
```

Config resolution order per field:
1. Env var: `SKILLS_{SKILLNAME}_{FIELD}` (systematic name)
2. Env var alias from field metadata
3. JSON value from `config.skills.{skill_name}.{field}`
4. Dataclass default

All `SkillConfig` fields must have defaults. Validate required values in `init()` and return a clear error if missing.

**Important:** Skills are loaded dynamically via `importlib.spec_from_file_location`, which does not set Python package context. This means `tools.py` **must use absolute imports**, not relative imports:

```python
# CORRECT — absolute imports
from decafclaw.skills.my_skill.helpers import some_function
from decafclaw.tools.confirmation import request_confirmation

# WRONG — relative imports will fail at runtime
from .helpers import some_function
from ...tools.confirmation import request_confirmation
```

### Multi-module skills

Skills can have multiple Python modules. The skill loader only imports `tools.py`, but that file can import from sibling modules using absolute imports. For example, the `claude_code` skill has:

```
claude_code/
  SKILL.md
  tools.py          # Entry point — imports from siblings
  sessions.py       # Session lifecycle management
  permissions.py    # Permission handling
  output.py         # Output logging and summaries
```

### Shell-based skills

Skills without `tools.py` are shell-based. The agent reads the SKILL.md instructions and uses the existing `shell` tool to run commands. This is how community skills from ClawHub and OpenClaw work.

## Skill directories

Skills are discovered from three locations, in priority order (highest first):

| Priority | Location | Description |
|----------|----------|-------------|
| 1 | `data/{agent_id}/workspace/skills/` | Agent-writable. ClawHub installs land here. |
| 2 | `data/{agent_id}/skills/` | Admin-managed. |
| 3 | `src/decafclaw/skills/` | Bundled with the package. |

Higher-priority skills override lower-priority ones with the same name.

## Activation and permissions

Skills require user confirmation before activation. The confirmation prompt offers three options:

- **Yes** (👍) — activate this time only
- **No** (👎) — deny activation
- **Always** (✅) — activate and remember the choice

"Always" permissions are stored in `data/{agent_id}/skill_permissions.json`, outside the agent's workspace sandbox. The agent cannot grant itself permission.

### Per-conversation activation

Activated skills and their tools are scoped to the current conversation. Other conversations are unaffected. When a conversation ends, its activated skills are cleaned up.

## Management tools

- **`activate_skill(name)`** — activate a skill in the current conversation
- **`refresh_skills`** — re-scan skill directories without restarting

## Bundled skills

### tabstack

Web browsing, content extraction, research, and browser automation via the Tabstack API. Requires `TABSTACK_API_KEY`.

Tools: `tabstack_extract_markdown`, `tabstack_extract_json`, `tabstack_generate`, `tabstack_automate`, `tabstack_research`

### claude_code

Delegates coding tasks to [Claude Code](https://claude.com/claude-code) as a subagent. The agent can start sessions, send coding tasks, and get results back — all within Mattermost conversations. Requires `ANTHROPIC_API_KEY`.

Tools: `claude_code_start`, `claude_code_send`, `claude_code_exec`, `claude_code_push_file`, `claude_code_pull_file`, `claude_code_stop`, `claude_code_sessions`

Features:
- Persistent sessions via SDK `resume` (one per working directory, 30min idle expiration)
- Working directory sandboxed to the agent workspace
- Full output logged to JSONL, concise summaries returned to save tokens
- Configurable model, budget (default + max), and session timeout
- Upfront user confirmation per task via Mattermost reactions

**Note:** Per-tool permission control (`can_use_tool` callback) is blocked by an upstream SDK bug. Currently uses `bypassPermissions` with upfront confirmation as a workaround. See [issue #53](https://github.com/lmorchard/decafclaw/issues/53) for details and upstream tracking.

### project

Structured workflow for complex multi-step tasks. Guides the agent through a brainstorm → spec → plan → execute lifecycle with persistent markdown artifacts. See [Project Skill](project-skill.md) for full details.

Tools: `project_create`, `project_status`, `project_list`, `project_switch`, `project_next_task`, `project_task_done`, `project_update_spec`, `project_update_plan`, `project_update_step`, `project_add_steps`, `project_advance`, `project_note`

Features:
- State machine with 6 phases and enforced transitions (including backward transitions)
- Dynamic tool loading — only phase-appropriate tools are visible (`get_tools`)
- Phase-boundary tools use `end_turn` to force user interaction before continuing
- Execution-phase tools chain freely for sustained work
- Persistent plan checklist with sub-steps, parsed and updated by tools
- Mid-execution replanning via `project_add_steps`
- User-invocable as `!project` / `/project`

## Using community skills

Shell-based skills from [ClawHub](https://clawhub.com) or OpenClaw's bundled skills can be placed in `data/{agent_id}/workspace/skills/`. As long as the skill has a `SKILL.md` with valid frontmatter and any required env vars are set, it will be discovered and available for activation.

Example: the `weather` skill from ClawHub uses `curl` via the `shell` tool — no external binary needed.

## Creating a skill

1. Create a directory with a `SKILL.md`
2. Add frontmatter with `name` and `description`
3. Add `requires.env` if the skill needs API keys
4. Write instructions in the markdown body
5. Optionally add `tools.py` for native Python tools
6. Place in any of the three skill directories

The agent will discover it on next startup or when `refresh_skills` is called.
