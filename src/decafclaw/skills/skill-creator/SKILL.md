---
name: skill-creator
description: "How to author a decafclaw workspace skill — SKILL.md frontmatter, the tools.py contract, the get_tools(ctx) signature, and validating before load. Activate BEFORE creating or editing a skill under workspace/skills/, or when a skill you wrote isn't loading."
user-invocable: true
---

# Authoring a workspace skill

Use this guide whenever you create or edit a skill under `workspace/skills/`, or
when a skill you wrote isn't showing up. decafclaw follows the open **Agent Skills**
standard (agentskills.io) for SKILL.md, but its native-tool model is
decaf-specific — the sections below flag where decaf differs from what you may
know from that standard.

## Workflow

1. Create `workspace/skills/<name>/SKILL.md` (and `tools.py` only if the skill
   needs native tools).
2. Validate before loading: call `skill_validate('skills/<name>')`. It reports a
   pass/fail checklist — frontmatter, `tools.py` filename, clean import, the
   `get_tools(ctx)` signature.
3. Fix every ✗ item, then re-run `skill_validate`.
4. Load it into the catalog: call `refresh_skills`. It lists any skills it
   rejected and why.
5. Activate it with `activate_skill` to use it.

(`skill_validate` and `refresh_skills` are in the deferred tool catalog — fetch
them with `tool_search` if they aren't already available.)

## Directory layout

```text
workspace/skills/<name>/
  SKILL.md      # required: --- frontmatter --- then a markdown body
  tools.py      # optional: native Python tools (see contract below)
```

## SKILL.md frontmatter

The file MUST start with a `---` YAML frontmatter block containing at least
`name` and `description`. Without valid frontmatter the skill is rejected at
discovery.

- `name` — ≤ 64 chars, lowercase letters/numbers and hyphens only, no leading,
  trailing, or consecutive hyphens, and it **should match the directory name**. These are conventions from the Agent Skills standard; `skill_validate` checks that `name` is present but does not currently validate its format, so follow them yourself.
- `description` — ≤ 1024 chars. State **what the skill does AND when to use it**,
  with concrete keywords — this is what the agent matches on to decide whether to
  activate the skill, so a vague description means it never fires.
  - Good: `Extracts text and tables from PDF files, fills PDF forms, merges PDFs. Use when working with PDF documents or when the user mentions PDFs, forms, or extraction.`
  - Poor: `Helps with PDFs.`

Optional fields decaf understands include `user-invocable`, `context`,
`argument-hint`, `required-skills`, and `allowed-tools` (see the tools section). Note: `allowed-tools` only takes effect for user-invocable commands and only hard-restricts tools in `context: fork`; it is inert on ordinary inline activation.

## Native tools — the `tools.py` contract (decaf-specific)

**This is where decaf differs from the generic Agent Skills standard.** The
generic standard bundles executable code in a `scripts/` folder that the agent
runs via the shell. decaf does NOT do that. decaf native tools are structured
Python in a file named exactly **`tools.py`**:

- The filename is **`tools.py`** — not `main.py` or anything else.
- Use **absolute** imports only: `from decafclaw.skills.<name>.<module> import ...`.
  The loader imports `tools.py` without package context, so relative imports
  fail at runtime.
- Export a `TOOLS` dict mapping tool name → function, plus a `TOOL_DEFINITIONS`
  list of OpenAI-style function schemas, and/or a `get_tools(ctx) -> (dict, list)`
  function for tools that vary by state.
- **Every tool function takes `ctx` as its first parameter**, even if unused.
- **`default_api.*` does not exist in decaf.** Do not call it. Tools are plain
  Python functions registered via `TOOLS` / `get_tools`.
- decaf's `allowed-tools` frontmatter is a **comma-separated list of decaf tool
  names** (e.g. `vault_read, vault_write, shell(rg *)`) — NOT the standard's
  space-separated `Bash(git:*)` syntax.

### Minimal correct skill with tools

`SKILL.md`:
```markdown
---
name: my-skill
description: Does a specific useful thing. Use when the user asks to do that thing.
---

# My skill

Explain when and how to use the tool here.
```

`tools.py`:
```python
from decafclaw.media import ToolResult


def my_tool(ctx, text: str) -> ToolResult:
    """Every tool takes ctx first."""
    return ToolResult(text=f"got: {text}")


TOOLS = {"my_tool": my_tool}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "my_tool",
            "description": "Does the thing. Use when ...",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "input"}},
                "required": ["text"],
            },
        },
    },
]


def get_tools(ctx) -> tuple[dict, list]:
    """Optional. Return (TOOLS, TOOL_DEFINITIONS), varying by state if needed."""
    return TOOLS, TOOL_DEFINITIONS
```

## Keep it lean (progressive disclosure)

The catalog shows only `name` + `description`; the full `SKILL.md` body loads
only when the skill activates. Keep the body focused (well under ~500 lines).
For a large skill, put deep reference material in separate files under the skill
directory and tell the agent to read them on demand with `workspace_read`.

## More detail

See `docs/skills.md` for the full reference — `SkillConfig` for skill config,
`init()`/`shutdown()` lifecycle, scheduling sidecars, trust tiers, and
user-invocable commands.
