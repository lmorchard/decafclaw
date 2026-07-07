# Spec: `skill-creator` authoring-guide skill (#597)

## Background

Third issue from the `web-lmorchard-fa5ec853` postmortem. The contract for authoring a
decafclaw **workspace skill** (SKILL.md frontmatter shape, `tools.py` filename, the
`get_tools(ctx) -> (dict, list)` signature, absolute imports, that `default_api.*` is not
a thing) lives in `docs/skills.md` and `CLAUDE.md` — but is **not in the agent's runtime
context** when it's actually authoring a skill. In the incident the agent had no in-context
knowledge of the contract and cargo-culted a Gemini-function-calling `default_api.*` API
that doesn't exist in decafclaw, used `main.py` instead of `tools.py`, and wrote
`get_tools()` without `ctx`.

Sibling work already shipped: `refresh_skills` now reports rejections and `skill_validate`
lints a single skill (#595/#596, PR #608). This issue adds the **proactive, in-context
guidance** those reactive tools complement.

## Goal

A bundled, lazy-loaded, **text-only** skill — `skill-creator` — whose body is the
authoring contract for decafclaw workspace skills. The agent activates it (catalog-triggered
or via `/skill-creator`) when it intends to create or edit a skill, so the contract is in
context at authoring time.

## Non-goals (out of scope)

- **Always-on injection** of the contract into every system prompt — rejected; it contradicts
  the lazy-load philosophy the skill catalog exists to serve. Authoring is occasional.
- **#598** — diagnostic-discipline guardrails (loop-breaker, verify-tool-fired, apology
  spiral). Making skill activation *more reliable* is #598's territory, not this.
- **Enforcing `name == directory`** in `skill_validate` — the Agent Skills standard requires
  it; decafclaw's loader does not check it today. `skill-creator` will *recommend* it; adding
  the check to `skill_validate` is a possible #596 follow-up, not part of this.
- **A scaffolding tool** that writes skill files for the agent — the skill is pure guidance;
  `workspace_write` already exists.

## Design

### Approach: lazy bundled skill (issue option b)

A new bundled skill at `src/decafclaw/skills/skill-creator/SKILL.md`. No `tools.py` — it's
guidance only; the working tools (`workspace_write`, `skill_validate`, `refresh_skills`)
already exist. Lazy-loaded like every other skill: its `name` + `description` sit in the
always-present catalog; the full body loads on activation. Zero always-on context cost.

Directory name `skill-creator` (hyphen) — it **matches** the frontmatter `name`, so the
skill is self-exemplifying for the "name should match the directory" rule it teaches. (A
text-only skill has no `tools.py`, so the directory is never imported as a Python module and
a hyphen is safe. _Refined during planning — the spec originally proposed an underscore
`skill_creator` dir._)

### Frontmatter

```yaml
---
name: skill-creator
description: "How to author a decafclaw workspace skill — SKILL.md frontmatter, the tools.py contract, the get_tools(ctx) signature, and validating before load. Activate BEFORE creating or editing a skill under workspace/skills/, or when a skill you wrote isn't loading."
user-invocable: true
---
```

- The `description` states **what + when** (per the Agent Skills description best-practice)
  and includes trigger keywords ("author", "create", "edit", "isn't loading").
- **No `allowed-tools` field.** _Resolved during planning:_ the spec originally proposed
  `allowed-tools: workspace_read, workspace_write, skill_validate, refresh_skills` to surface
  the author's working set. Code review found a skill's `allowed-tools` only hard-restricts in
  `context: fork` command mode and otherwise just feeds `preapproved` — on ordinary inline
  `activate_skill` it is inert and does **not** promote the deferred `skill_validate` /
  `refresh_skills`. So it was dropped; the body names those tools instead and the agent fetches
  them from the deferred catalog via `tool_search`. (See plan.md.)
- Stays `user-invocable` (the default), so `/skill-creator` pulls the guide manually.

### Body content (the contract)

Concise runtime surface (target < 200 lines). Sections:

1. **When to use / the workflow.** write files → `skill_validate('skills/<name>')` → fix the
   ✗ items → `refresh_skills` → `activate_skill`. Leans on the tools from #595/#596.

2. **Directory layout.** `workspace/skills/<name>/SKILL.md` (required) + optional `tools.py`.

3. **SKILL.md frontmatter.** Must open with a `---` YAML block containing `name` +
   `description`. Constraints (from the Agent Skills standard): `name` ≤ 64 chars, lowercase
   alphanumeric + hyphens, no leading/trailing/consecutive hyphens, **should match the
   directory name**; `description` ≤ 1024 chars and must say **what it does AND when to use
   it** with matching keywords. Include the standard's good-vs-poor description contrast:
   - Good: *"Extracts text and tables from PDF files, fills PDF forms, merges PDFs. Use when
     working with PDF documents or when the user mentions PDFs, forms, or extraction."*
   - Poor: *"Helps with PDFs."*

4. **`tools.py` contract (decafclaw-specific — where decaf differs from the generic
   standard).** A callout that the generic Agent Skills standard uses a `scripts/` folder of
   executable code run via shell; **decafclaw does not** — native tools are structured Python
   in **`tools.py`**:
   - filename is exactly `tools.py` (not `main.py` or anything else);
   - **absolute** imports only (`from decafclaw.skills.X.Y import …`) — the loader uses
     `importlib.spec_from_file_location` without package context, so relative imports fail;
   - export a `TOOLS` dict + `TOOL_DEFINITIONS` list, and/or `get_tools(ctx) -> (dict, list)`;
   - every tool function takes `ctx` as its first parameter;
   - **`default_api.*` does not exist** in decafclaw — do not call it.
   - decafclaw `allowed-tools` is **comma-separated decafclaw tool names** (e.g.
     `vault_read, vault_write, shell(rg *)`), NOT the standard's space-separated
     `Bash(git:*)` syntax.

5. **Minimal correct template** (copy-paste): a `SKILL.md` + a `tools.py` exposing one
   `get_tools(ctx)` returning `(TOOLS, TOOL_DEFINITIONS)`.

6. **Progressive disclosure / keep it lean.** Keep `SKILL.md` focused (< ~500 lines); push
   depth into reference files the agent reads on demand via `workspace_read`.

7. **Pointer to depth.** `docs/skills.md` for the full reference (`SkillConfig`, scheduling,
   trust tiers, etc.) and a one-line pointer to the open Agent Skills standard
   (agentskills.io) for general concepts, noting decaf's divergences above.

### Single-source-of-truth / drift

The skill body is the concise **runtime** surface; `docs/skills.md` keeps the long-form
reference. The body links to the doc rather than duplicating it wholesale, so the two don't
drift. The decaf-specific tools.py contract is stated in both (unavoidable); keep the body's
version tight and authoritative for the at-authoring-time moment.

### Docs & conventions

- `docs/skills.md`: add a short note that the in-context authoring guide is the
  `skill-creator` skill (activate it, or `/skill-creator`).
- `CLAUDE.md`: add `skill-creator` to the bundled-skills list in the Skills key-files line.

## Testing & evals

### Unit
- Assert the bundled `skill-creator` is discovered by `discover_skills` from a default config:
  present, `name == "skill-creator"`, `has_native_tools is False` (text-only), `always_loaded
  is False`, trust tier `bundled`, non-empty body and description.

### Eval (behavior — LLM-driven)
- A case in `evals/<theme>.yaml`: prompt like *"I want to add a new skill that does X — set
  it up."* → `expect_tool activate_skill` with a tight `max_tool_calls`. Guards that the
  catalog description actually triggers activation (the whole premise of approach b). Bound
  with `max_tool_calls` / `max_tool_errors`; prefer positive `expect_tool` over
  `expect_no_tool` (reflection-retry caveat).
- A `tool_choice` case is NOT a natural fit (activation is `activate_skill` with a skill-name
  argument, not tool-vs-tool disambiguation), so it's omitted unless planning finds a clean angle.

## Acceptance criteria

- [ ] Bundled `skill-creator/SKILL.md` exists; text-only (no `tools.py`); discovered with
      `name: skill-creator`, sensible `description`, and no `allowed-tools` field (resolved
      above — the body names `skill_validate` / `refresh_skills` instead).
- [ ] Body states the decafclaw `tools.py` contract (filename, absolute imports, `get_tools(ctx)`
      / `TOOLS`, ctx-first, no `default_api`), the SKILL.md frontmatter rules + naming
      constraints, a minimal correct template, the validate→refresh→activate workflow, and the
      "where decaf differs from the generic Agent Skills standard" callout.
- [ ] Body folds in the standard's description best-practice (what + when, good/poor example)
      and progressive-disclosure guidance.
- [ ] `docs/skills.md` and `CLAUDE.md` updated.
- [ ] Unit test for discovery; behavior eval case added.
- [ ] `make check` + `make test` green.
