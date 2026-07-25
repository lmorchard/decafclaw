---
name: skill-creator
description: "How to author a decafclaw skill — where the files go, SKILL.md frontmatter, the tools.py contract, the get_tools(ctx) signature, and validating before load. Activate BEFORE creating or editing a skill under skills/, or when a skill you wrote isn't loading."
user-invocable: true
---

# Authoring a skill

Use this guide whenever you create or edit a skill in your `skills/` directory,
or when a skill you wrote isn't showing up. decafclaw follows the open **Agent
Skills** standard (agentskills.io) for SKILL.md, but its native-tool model is
decaf-specific — the sections below flag where decaf differs from what you may
know from that standard.

## Where the files go — get this right first

Every path you pass to `workspace_write` / `workspace_read` / `workspace_list`
is **already relative to your workspace root**. So the path is:

```text
skills/<name>/SKILL.md          ✅ correct
workspace/skills/<name>/SKILL.md   ❌ lands at workspace/workspace/skills/...
```

Prefixing with `workspace/` writes the skill one level too deep, where nothing
will ever find it. Discovery only scans the **immediate children** of
`skills/`, so `skills/group/<name>/` is invisible too.

If a skill you just wrote doesn't appear after `refresh_skills`, this is the
first thing to check — call `skill_validate` and read its `discoverable` line.

## Workflow

1. **Delegate the code to `claude_code`.** For anything beyond a few lines of
   `tools.py`, activate the `claude_code` skill and give it the goal. Do NOT
   hand-edit Python with `workspace_replace_lines` / `workspace_insert` —
   line-number surgery on source you can't run corrupts the file, and each
   failed edit costs a round-trip. `claude_code` can write it, import it, and
   iterate until it's clean.
2. **Decide whether you need `tools.py` at all — most skills do not.** If the
   job is "run this command", "read this file", or "fetch this URL", an existing
   tool already does it, and you MUST document that tool in SKILL.md rather than
   wrap it. See "Do you even need `tools.py`?" — read it before writing any
   Python.
3. Create `skills/<name>/SKILL.md`, plus `tools.py` only if step 2 said you
   need it.
4. Validate before loading: call `skill_validate('skills/<name>')`. It reports a
   pass/fail checklist — location, frontmatter, `tools.py` filename, clean
   import, the `get_tools(ctx)` signature, and the exports' shape.
5. Fix every ✗ item, then re-run `skill_validate`.
6. Load it into the catalog: call `refresh_skills`. It lists any skills it
   rejected and why.
7. Activate it with `activate_skill` to use it.

To change an **already-active** skill's `tools.py`: edit the file, then call
`activate_skill` again. That re-imports the module and replaces the old tools.
`refresh_skills` alone is not enough — it updates the catalog, not the loaded
functions.

(`skill_validate` and `refresh_skills` are in the deferred tool catalog — fetch
them with `tool_search` if they aren't already available.)

## When something isn't working

Work down this list instead of retrying the same call:

| Symptom | Cause |
|---|---|
| Not in `refresh_skills` output | Wrong location. See "Where the files go". `refresh_skills` flags the two common wrong paths under `Possibly misplaced` — read that first. |
| `skill_validate` fails on `discoverable` | Same — the message names the correct path. |
| A `workspace_write` result says "Did you mean …?" | You prefixed the path with `workspace/`. The file was written where you asked, one level too deep — rewrite it at the suggested path. |
| `refresh_skills` says `No change since the last refresh.` | Your file didn't land where discovery scans, or you didn't write it. |
| Skill loads but activating `<dirname>` fails | Your frontmatter `name` differs from the directory; activate the `name`. `skill_validate` reports this as an advisory. |
| Listed but `activate_skill` errors | `tools.py` doesn't import or its exports are the wrong shape. The error names which. |
| Your edit had no effect | You didn't re-run `activate_skill`, so the old module is still loaded. |
| `TypeError` "raised inside the tool's own code" | The bug is in your `tools.py`, not in how you called the tool. |
| `activate_skill` refuses: "cannot call another tool" | Your `tools.py` tries to reach a decaf tool. There is no way to do this — see "What `tools.py` can and cannot reach". Use a library directly, or delete `tools.py` and document the tool in SKILL.md. |

If two tools disagree — `skill_validate` says PASS but `refresh_skills` doesn't
list the skill — stop and report it. That combination is a decafclaw bug, not
something to work around by retrying.

## Directory layout

```text
skills/<name>/
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

## Do you even need `tools.py`?

**Usually not — and a wrapper around an existing tool is not merely redundant,
it cannot work.** A skill tool has no way to call another decaf tool (see the
next section), so "a tool that runs the dev server" is not a thing you can
build. The working version is prose.

A skill's SKILL.md body is loaded into context on activation. **Prose naming the
tool to call, with the arguments to call it with, IS a complete and working
skill.** That is the normal case, not a lesser one.

So: before writing any Python, check whether a tool already does the work
(`tool_search` searches the deferred catalog). **You MUST NOT write a skill tool
that:**

| If the job is… | Use this instead | Never write |
|---|---|---|
| run a command in the background (dev server, watcher) | `shell_background_start`, then `shell_background_status` / `_stop` / `_list` — always loaded | a `start_*_server` tool |
| run a command and wait | `shell` | a `run_*` tool |
| read or write a workspace file | `workspace_read` / `workspace_write` | a `read_*` / `save_*` tool |
| fetch a URL | `http_get`, or the `tabstack` skill | a `fetch_*` tool |

Write `tools.py` only for Python that no existing tool can express — parsing,
computation, calling a third-party library, talking to an API with a client.

### Worked example: "a skill for my blog that starts the dev server"

✅ Correct. `SKILL.md` only, no `tools.py`:

```markdown
---
name: blogdev
description: Working on the blog at blog.lmorchard.com. Use when previewing or building blog changes.
---

# Blog dev

The blog lives at `blog.lmorchard.com/` in the workspace.

- **Start the dev server:** call `shell_background_start` with
  command `npm start` and cwd `blog.lmorchard.com`.
- **Check on it:** `shell_background_status` with the returned job id.
- **Stop it:** `shell_background_stop`.
- **Build:** `shell` with `./index.js build` in the same directory.
```

❌ Wrong: a `tools.py` exporting `start_dev_server`. It cannot work — the
function has no way to reach `shell_background_start`, and there is no
`default_api` to reach it through.

## What `tools.py` can and cannot reach

A skill tool is an ordinary Python function. It runs **in-process**, and it has
no channel back into the tool layer:

- **There is no `default_api`.** It does not exist in any form. Do not call it.
- **`ctx` is not a tool namespace.** `ctx.shell_background_start(...)` and
  friends do not exist. `ctx` is the runtime context — `ctx.config`,
  `ctx.publish()` for `tool_status` events, `ctx.conv_id`, `ctx.workspace_path`.
- **You cannot call another decaf tool from inside a tool.** If your tool needs
  a shell command, use `subprocess` / `asyncio.create_subprocess_exec`
  directly. If it needs to read a file, use `pathlib`. If the work is really
  "call tool X", don't write a tool at all — document tool X in SKILL.md and
  let the agent call it.
- **This is enforced, not just advised.** `skill_validate` reports it, and
  `activate_skill` **refuses to load a workspace skill** that does it — the tool
  provably cannot run, so there is nothing to gain from loading it. Reaching the
  tool through a subscript (`ctx['shell_background_start'](...)`) or a renamed
  parameter (`context.shell_background_start(...)`) is detected too.
- **You cannot return a command for the agent to run.** `ToolResult` has no
  `tool_code` field. Its fields are `text`, `media`, `display_text`,
  `display_short_text`, `data`, `end_turn`, `widget` — nothing else. Passing
  any other keyword raises `TypeError` at runtime, and it will be reported as
  an error *inside your tool*, because that's where it is.

A tool returns a **result**, never an instruction.

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
- Tools are plain Python functions registered via `TOOLS` / `get_tools` — see
  "What `tools.py` can and cannot reach" above for what they may call.
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
