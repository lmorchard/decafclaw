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
| `user-invocable` | No | Bool, default true. (Parsed but not enforced yet.) |
| `disable-model-invocation` | No | Bool, default false. (Parsed but not enforced yet.) |

### Native Python tools (tools.py)

Optional. If present, the skill registers structured tools that the LLM calls directly:

```python
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

def init(config):
    """Optional. Called once on first activation. Can be async."""
    pass
```

All tool functions receive `ctx` as first parameter.

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
