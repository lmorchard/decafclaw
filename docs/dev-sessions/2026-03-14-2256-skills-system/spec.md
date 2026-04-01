# Skills System — Spec

## Goal

Add a skills system to DecafClaw inspired by the [Agent Skills](https://agentskills.io) open standard, Claude Code's skills, and OpenClaw's skills. Skills are modular, portable capability packages that the agent can discover and activate on demand.

## Design Principles

- **Progressive disclosure**: catalog (name + description) at startup, full content on activation, resources on demand
- **Lazy initialization**: nothing is imported or initialized until the agent activates a skill
- **Per-conversation activation**: activated skills and their tools are scoped to the conversation context (`ctx`), not global
- **Compatibility**: shell-based Agent Skills standard skills work via SKILL.md + `shell` tool. Native Python skills extend the standard with structured tool registration.
- **Trust boundary**: skill permissions file lives outside the agent's workspace (read-only to the agent)

## Skill Format

Each skill is a directory containing at minimum a `SKILL.md` file:

```
skills/
  tabstack/
    SKILL.md            # Required: frontmatter + instructions
    tools.py            # Optional: native Python tool functions
    reference.md        # Optional: detailed docs (loaded on demand)
    scripts/            # Optional: CLI scripts for shell-based execution
```

### SKILL.md

YAML frontmatter + markdown body:

```yaml
---
name: tabstack
description: Web browsing, content extraction, research, and browser automation via Tabstack.
requires:
  env:
    - TABSTACK_API_KEY
---

## Instructions

(Markdown body with agent instructions, loaded on activation)
```

#### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Lowercase alphanumeric + hyphens, max 64 chars |
| `description` | Yes | What the skill does, max 1024 chars. Used for catalog. |
| `requires.env` | No | List of env vars that must be set. Skill skipped if unmet. |
| `user-invocable` | No | Bool, default true. If false, hidden from user slash commands. |
| `disable-model-invocation` | No | Bool, default false. If true, agent can't auto-activate. |

### tools.py (Native Python Skills)

Optional. If present, exports:

- `TOOLS: dict[str, Callable]` — maps tool name → function (same pattern as existing tool modules)
- `TOOL_DEFINITIONS: list[dict]` — OpenAI-style JSON schema definitions
- `init(config) -> None` — optional initialization function, called once on first activation. May be sync or async (loader auto-detects like `execute_tool`).

All tool functions receive `ctx` as first parameter, same as existing tools.

## Skill Discovery

### Scan Paths (in priority order, highest first)

1. **Workspace skills** — `data/{agent_id}/workspace/skills/` (agent-writable, ClawHub installs land here)
2. **Agent-level skills** — `data/{agent_id}/skills/` (admin-managed)
3. **Bundled skills** — `src/decafclaw/skills/` (shipped with the package)

Higher-priority skills override lower-priority ones with the same name.

### Discovery Process (at session startup)

1. Scan all three paths for directories containing `SKILL.md`
2. Parse YAML frontmatter (lenient: warn on issues, skip only if `description` missing or YAML unparseable)
3. Check `requires.env` — if any required env var is missing, skip the skill entirely (no error, just absent from catalog)
4. Build catalog: list of `{name, description, location, has_native_tools}` entries
5. Inject catalog into system prompt after AGENT.md as an `## Available Skills` section

### Catalog Format (in system prompt)

```
## Available Skills

The following skills are available. Use the activate_skill tool to load a skill before using it.

- **tabstack**: Web browsing, content extraction, research, and browser automation via Tabstack.
- **some-other-skill**: Does something else.
```

~50-100 tokens per skill.

## Skill Activation

### The `activate_skill` Tool

```json
{
  "name": "activate_skill",
  "description": "Activate a skill to make its capabilities available. ...",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Name of the skill to activate"
      }
    },
    "required": ["name"]
  }
}
```

### Activation Flow

1. Agent calls `activate_skill(name="tabstack")`
2. If skill is already activated in this context, return "already active" (no-op)
3. Check `data/{agent_id}/skill_permissions.json` for trust status:
   - `"always"` → proceed immediately
   - Not present → publish `tool_confirm_request` event, wait for user response
3. User responds:
   - **Yes** (👍) — activate this time only
   - **No** (👎) — deny, return error message
   - **Yes, always** (new option) — write `{"tabstack": "always"}` to permissions file, then activate
4. For **native Python skills** (has `tools.py`):
   - Import `tools.py` module
   - Call `init(config)` if it exists
   - Register `TOOLS` and `TOOL_DEFINITIONS` onto `ctx.extra_tool_definitions` and `ctx.extra_tools`
   - Return SKILL.md body + list of newly available tools
5. For **shell-based skills** (no `tools.py`):
   - Return SKILL.md body only
   - Agent uses existing `shell` tool guided by the instructions

### Agent Loop Changes

- `run_agent_turn` must merge `ctx.extra_tool_definitions` with base `TOOL_DEFINITIONS` when building the tools list for each LLM call
- `execute_tool` must check `ctx.extra_tools` in addition to the global `TOOLS` registry

## Skill Permissions

### File: `data/{agent_id}/skill_permissions.json`

```json
{
  "tabstack": "always",
  "some-community-skill": "always"
}
```

- Lives **outside** the workspace (agent cannot write to it)
- Only written by the confirmation flow (which runs in the host process, not the agent sandbox)
- If file doesn't exist, treat all skills as requiring confirmation
- Simple JSON object: skill name → `"always"` (only value for now, extensible later)

## Confirmation UX

Extends the existing `tool_confirm_request` / `tool_confirm_response` pattern used by the `shell` tool. Adds a third option:

- **Mattermost**: three reactions — 👍 (yes), 👎 (no), ✅ (yes, always)
- **Interactive terminal**: prompt with `[y]es / [n]o / [a]lways`

## Tabstack Migration

The first (and only for this session) skill to extract:

1. Create `src/decafclaw/skills/tabstack/SKILL.md` with frontmatter and usage instructions
2. Move `src/decafclaw/tools/tabstack_tools.py` → `src/decafclaw/skills/tabstack/tools.py`
3. Remove tabstack imports from `src/decafclaw/tools/__init__.py`
4. Skill requires `TABSTACK_API_KEY` env var — if not set, tabstack doesn't appear in catalog

## Testing

1. **Skill discovery** — finds skills in all three tiers, respects priority ordering, skips skills with unmet `requires`
2. **SKILL.md parsing** — extracts frontmatter correctly, handles missing/malformed frontmatter gracefully
3. **Activation** — `activate_skill` returns SKILL.md body, native skills register tools on ctx, tools appear in subsequent turns
4. **Lazy init** — `tools.py` isn't imported and `init()` isn't called until activation
5. **Tabstack migration** — tabstack tools still work after being moved to a skill
6. **Permissions** — confirmation required when no permission entry, skipped when `"always"`, permissions file written on "yes, always"

## Out of Scope (this session)

- Deactivation — skills stay active for the conversation lifetime
- Subagent execution (`context: fork`)
- ClawHub install command
- Cross-client `.agents/skills/` path scanning
- `allowed-tools` frontmatter field
- Skill content protection from compaction
- Multiple skills with conflicting tool names
- User invocation via slash commands (`user-invocable` / `disable-model-invocation` parsed but not enforced)
