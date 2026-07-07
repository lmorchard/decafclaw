# `skill-creator` Authoring-Guide Skill (#597) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bundled, lazy-loaded, text-only `skill-creator` skill whose body is the in-context authoring contract for decafclaw workspace skills, so the agent has the contract in context at authoring time instead of cargo-culting non-decafclaw patterns.

**Architecture:** A new bundled skill directory `src/decafclaw/skills/skill-creator/` containing only `SKILL.md` (no `tools.py` — pure guidance). Discovered and lazy-loaded like every other bundled skill; its name+description sit in the always-present catalog and the agent activates it when it intends to author a skill. It leans on the already-shipped `skill_validate` / `refresh_skills` tools (#595/#596) by naming them in the body.

**Tech Stack:** Markdown (SKILL.md + YAML frontmatter); decafclaw skill discovery (`discover_skills`); pytest; the YAML behavior-eval harness (`evals/*.yaml`).

## Global Constraints

- The skill is **text-only**: directory contains `SKILL.md` and nothing else (NO `tools.py`).
- Directory name and frontmatter `name` are BOTH `skill-creator` (hyphen, matching — the skill itself teaches "name should match the directory").
- **Do NOT add an `allowed-tools` frontmatter field.** Verified in code: `allowed-tools` only sets `ctx.tools.allowed` in `context: fork` command mode (`commands.py:419-423`) and otherwise only feeds `preapproved`; on inline activation it neither promotes deferred tools nor is needed. The body names `skill_validate` / `refresh_skills` instead (they live in the deferred catalog; the agent fetches them via `tool_search`).
- The body must teach the **decafclaw `tools.py` contract** (filename `tools.py` not `main.py`; absolute imports; `TOOLS`/`TOOL_DEFINITIONS` and/or `get_tools(ctx) -> (dict, list)`; every tool takes `ctx` first; `default_api.*` does not exist) and explicitly flag where decaf **differs from the generic Agent Skills standard** (`scripts/`-of-shell-code, space-separated `allowed-tools`).
- The body must fold in the Agent Skills standard's portable wisdom: description best-practice (state *what* + *when*, with a good-vs-poor example), `name`/`description` constraints (`name` ≤64 chars, lowercase + hyphens, no leading/trailing/consecutive hyphens, match the directory; `description` ≤1024 chars), and progressive disclosure (keep `SKILL.md` lean; push depth into reference files).
- TDD where there is testable behavior: failing test → run fail → implement → run pass → commit.
- `make check` (lint + typecheck + JS + message-types drift) and `make test` must be green before the final commit of each task.
- Work happens in the worktree `.claude/worktrees/597-skill-creator` (venv synced; `HTTP_PORT=18896`). Use `uv run pytest …`, `make lint`, `make typecheck`.

---

## File Structure

- `src/decafclaw/skills/skill-creator/SKILL.md` — **create.** The skill (frontmatter + authoring-contract body).
- `tests/test_skills.py` — **modify.** Add one discovery test asserting the bundled `skill-creator` is found with the expected properties (text-only, not always-loaded, bundled tier).
- `docs/skills.md` — **modify.** Add a short note that the in-context authoring guide is the `skill-creator` skill.
- `CLAUDE.md` — **modify.** Add `skill-creator` to the bundled-skills list line.
- `evals/skill-authoring.yaml` — **create.** One behavior case asserting the agent activates a skill when asked to author one.

---

## Task 1: The `skill-creator` skill + discovery test + docs

**Files:**
- Create: `src/decafclaw/skills/skill-creator/SKILL.md`
- Modify: `tests/test_skills.py`
- Modify: `docs/skills.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `discover_skills(config) -> list[SkillInfo]` and the `SkillInfo` dataclass (fields used: `name`, `description`, `body`, `has_native_tools`, `always_loaded`, `trust_tier`) from `decafclaw.skills`.
- Produces: a discoverable bundled skill named `skill-creator`.

- [ ] **Step 1: Write the failing discovery test**

Add to `tests/test_skills.py` (the file already imports `discover_skills` and `SkillInfo`; add near the other `test_discover_*` bundled tests, e.g. after `test_discover_honors_auto_approve_on_bundled`):

```python
def test_discover_includes_skill_creator(config):
    """The bundled skill-creator authoring guide is discovered, text-only,
    and lazy (not always-loaded)."""
    skills = discover_skills(config)
    by_name = {s.name: s for s in skills}
    assert "skill-creator" in by_name, (
        f"bundled skill-creator not discovered; got {sorted(by_name)}"
    )
    sc = by_name["skill-creator"]
    assert sc.has_native_tools is False, "skill-creator must be text-only (no tools.py)"
    assert sc.always_loaded is False, "skill-creator must stay lazy (not always-loaded)"
    assert sc.trust_tier == "bundled"
    assert sc.description.strip(), "skill-creator needs a non-empty description"
    assert sc.body.strip(), "skill-creator needs a non-empty body"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/597-skill-creator
uv run pytest tests/test_skills.py::test_discover_includes_skill_creator -v
```
Expected: FAIL — `assert "skill-creator" in by_name` (skill not created yet).

- [ ] **Step 3: Create the skill directory + SKILL.md**

Create `src/decafclaw/skills/skill-creator/SKILL.md` with EXACTLY this content:

```markdown
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

```
workspace/skills/<name>/
  SKILL.md      # required: --- frontmatter --- then a markdown body
  tools.py      # optional: native Python tools (see contract below)
```

## SKILL.md frontmatter

The file MUST start with a `---` YAML frontmatter block containing at least
`name` and `description`. Without valid frontmatter the skill is rejected at
discovery.

- `name` — ≤ 64 chars, lowercase letters/numbers and hyphens only, no leading,
  trailing, or consecutive hyphens, and it **should match the directory name**.
- `description` — ≤ 1024 chars. State **what the skill does AND when to use it**,
  with concrete keywords — this is what the agent matches on to decide whether to
  activate the skill, so a vague description means it never fires.
  - Good: `Extracts text and tables from PDF files, fills PDF forms, merges PDFs. Use when working with PDF documents or when the user mentions PDFs, forms, or extraction.`
  - Poor: `Helps with PDFs.`

Optional fields decaf understands include `user-invocable`, `context`,
`argument-hint`, `required-skills`, and `allowed-tools` (see the tools section).

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
```
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
```

- [ ] **Step 4: Run the discovery test to verify it passes**

Run:
```bash
uv run pytest tests/test_skills.py::test_discover_includes_skill_creator -v
```
Expected: PASS.

- [ ] **Step 5: Update `docs/skills.md`**

Open `docs/skills.md`. Find the "Validating a skill before it loads" section (added by PR #608, ~line 317). Immediately BEFORE that section, add:

```markdown
### Authoring a skill (in-context guide)

The bundled **`skill-creator`** skill is the in-context authoring guide. Activate
it (or run `/skill-creator`) before creating or editing a workspace skill — its
body carries the SKILL.md frontmatter rules, the decaf-specific `tools.py`
contract (filename, absolute imports, `get_tools(ctx)` signature, no
`default_api`), a minimal correct template, and the
`skill_validate` → `refresh_skills` → `activate_skill` workflow. It deliberately
flags where decaf diverges from the generic Agent Skills standard.

```

(Keep the blank line so the existing `### Validating a skill before it loads`
heading stays separated.)

- [ ] **Step 6: Update `CLAUDE.md` bundled-skills list**

In `CLAUDE.md`, find the "### Skills (bundled)" line under "Key files" that reads:

```
`skills/{vault,tabstack,dream,garden,project,claude_code,health,postmortem,ingest,background,mcp,newsletter}/`. `vault`, `background`, `mcp` are always-loaded. Contrib (opt-in) skills live in `contrib/skills/`.
```

Add `skill-creator` to the brace list (after `newsletter`):

```
`skills/{vault,tabstack,dream,garden,project,claude_code,health,postmortem,ingest,background,mcp,newsletter,skill-creator}/`. `vault`, `background`, `mcp` are always-loaded. Contrib (opt-in) skills live in `contrib/skills/`.
```

- [ ] **Step 7: Run lint, typecheck, and the skills test file**

Run:
```bash
make lint && make typecheck && uv run pytest tests/test_skills.py -q
```
Expected: no errors; all skills tests pass (including the new discovery test).

- [ ] **Step 8: Commit**

```bash
git add src/decafclaw/skills/skill-creator/SKILL.md tests/test_skills.py docs/skills.md CLAUDE.md
git commit -m "feat(skills): add skill-creator authoring-guide skill (#597)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Behavior eval — agent activates a skill when asked to author one

**Files:**
- Create: `evals/skill-authoring.yaml`

**Interfaces:**
- Consumes (from Task 1): the discoverable bundled `skill-creator` skill (present in the eval's skill catalog because bundled skills are always discovered).

- [ ] **Step 1: Create the eval case**

Create `evals/skill-authoring.yaml` with:

```yaml
# skill-creator (#597) — LLM-driven angle only.
# The skill's discovery/text-only properties are covered by a unit test in
# tests/test_skills.py. This file isolates the eval-only question: when asked
# to author a new skill, does the agent reach for the skill-creator guide
# (i.e. activate a skill) rather than blindly writing files?
#
# NOTE: do NOT pre-activate skill-creator via setup.skills — the whole point is
# that the catalog description triggers activation. Evals auto_confirm by
# default, so the bundled-skill activation proceeds without a manual click.

- name: "activates the authoring guide when asked to create a skill"
  input: "I want to add a new workspace skill that fetches the weather for a city. Set it up correctly."
  expect:
    max_tool_calls: 4
    max_tool_errors: 1
    expect_tool: activate_skill
```

- [ ] **Step 2: Validate the YAML parses**

Run:
```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/597-skill-creator
uv run python -c "import yaml; yaml.safe_load(open('evals/skill-authoring.yaml')); print('yaml ok')"
```
Expected: `yaml ok`.

- [ ] **Step 3: Run the eval (real LLM)**

> This makes a real model call. If LLM credentials/proxy aren't configured in
> this worktree's `.env`, skip the run and note it for Les; the YAML case ships
> either way. Find the eval runner target first:

Run:
```bash
grep -nE "eval" Makefile | head -20
```
Then run the single eval file via the discovered target (e.g. `make eval FILE=evals/skill-authoring.yaml`, or the runner the Makefile exposes for one file). Expected: the case passes — the agent calls `activate_skill`. If it does NOT activate, that's signal the catalog `description` needs sharpening (it's a control surface) — sharpen the `skill-creator` description in `src/decafclaw/skills/skill-creator/SKILL.md` and re-run. Report the outcome either way.

- [ ] **Step 4: Commit**

```bash
git add evals/skill-authoring.yaml
git commit -m "test(evals): skill-creator activation on authoring prompt (#597)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (before PR)

- [ ] `make check` green.
- [ ] `uv run pytest tests/test_skills.py -q` green; then full `make test` green.
- [ ] Manually skim the rendered `SKILL.md` — frontmatter parses, code fences are intact, the decaf-vs-standard callout reads clearly.
- [ ] Rebase on latest `origin/main` before the squash.

## Self-Review (completed during planning)

- **Spec coverage:** skill creation + frontmatter/body contract → Task 1 Step 3. Description best-practice + naming constraints + progressive disclosure → folded into the SKILL.md body. decaf-vs-standard divergence callout → body "Native tools" section. `docs/skills.md` + `CLAUDE.md` → Task 1 Steps 5-6. Discovery unit test → Task 1 Steps 1-4. Behavior eval → Task 2. `allowed-tools` open question → resolved (dropped) in Global Constraints. All acceptance criteria mapped.
- **Placeholders:** none — the full SKILL.md content is inline; the eval runner target is discovered via a concrete grep rather than assumed.
- **Type/name consistency:** `name: skill-creator` and directory `skill-creator` match throughout; the discovery test keys on `"skill-creator"`; `has_native_tools`/`always_loaded`/`trust_tier` match `SkillInfo` fields verified in the codebase.
- **Deviation from spec, within its stated contingency:** spec proposed an `allowed-tools` field with a planning caveat; planning verified it's inert for inline activation, so it's omitted and the body names the tools — exactly the fallback the spec authorized.
