# Plan — true skill-level progressive disclosure

Implementation plan for `./spec.md`. One PR scope (#547). Commit per phase.

## Phase 0 — Survey callers and edge cases

Quick read-only pass before touching code. No commit.

1. `grep` for callers of `tool_search` and direct deferred-tool fetch — any test or eval that asserts loading an individual skill tool by name will break and needs updating.
2. `grep` for `auto-approve:` in skill frontmatter (bundled + contrib) — confirm no skill explicitly sets `auto-approve: false` expecting confirmation, which the new tier-bypass would silently swallow.
3. `grep` for `always_loaded:` to confirm current set (vault, background, mcp expected) and verify no surprises.
4. Confirm tier-path mapping plan: read `_resolve_extra_skill_paths` and the discovery walker to pin down which path each `SkillInfo.location` actually represents (resolved-symlink path vs. configured path). The tier check has to inspect a stable path that reflects user intent, not whatever symlink resolution happened.

Record findings in `notes.md` as we go.

## Phase 1 — Discovery foundations: trust_tier, skill_tool_owners, config field

Goal: data structures and discovery-time precomputation. Pure addition, no behavior change yet.

1. **`SkillInfo.trust_tier`** in `src/decafclaw/skills/__init__.py`:
   - Add field `trust_tier: Literal["bundled", "admin", "extra", "workspace"] = "bundled"` (with a sensible default for tests that mint SkillInfos directly).
   - In `discover_skills`, set `trust_tier` per the scan tier: workspace scan → `"workspace"`, admin scan → `"admin"`, bundled scan → `"bundled"`, extra-paths scan → `"extra"`.
   - The tier reflects which scan loop produced the entry; resolved-symlink paths don't override the user's intent.
2. **`config.skills_always_loaded: list[str]`** in `src/decafclaw/config_types.py` (or wherever the agent config lives):
   - New field with default `[]`.
   - Wire through the JSON config loader (env override via `SKILLS_ALWAYS_LOADED` comma-separated list — match the existing convention).
3. **`config.skill_tool_owners: dict[str, str]`** built at skill discovery:
   - Iterates `config.discovered_skills`, imports each skill's `tools.py` (only if `has_native_tools`), indexes `TOOL_DEFINITIONS` entries → owning skill name.
   - Mirrors the existing `config.always_loaded_skill_tools` cache pattern.
   - Importing tools.py for every skill at discovery has a startup cost. Acceptable — same work activate_skill does eventually anyway, just front-loaded. Profile if it bites.
4. Tests:
   - `SkillInfo` discovered from each tier has the correct `trust_tier`.
   - `config.skill_tool_owners` populated correctly across tiers.
   - `config.skills_always_loaded` loads from JSON and env.

Commit: `feat(skills): add trust_tier, skill_tool_owners, skills_always_loaded foundations`

## Phase 2 — Relax `always_loaded` eligibility to all trusted tiers + honor config list

Goal: extend the always-loaded path to admin/extra tiers via frontmatter or config. Behavior change visible at startup if any non-bundled skill declares `always_loaded` (today silently ignored).

1. In `build_catalog_text` (`src/decafclaw/skills/__init__.py`):
   - Replace the `bundled-only` filter with `trust_tier != "workspace"` AND (frontmatter-`always_loaded` OR name in `config.skills_always_loaded`).
2. In `agent.py`'s always-loaded auto-activation loop (~lines 396-410):
   - Same eligibility check. Apply to all trusted tiers via either path.
3. Tests:
   - Admin-tier skill with `always_loaded: true` auto-loads.
   - Extra-tier skill named in `config.skills_always_loaded` auto-loads.
   - Workspace-tier skill with `always_loaded: true` rejected (with a warning log).
   - Workspace-tier skill named in `config.skills_always_loaded` rejected.
   - Bundled+`always_loaded` still works as today.

Commit: `feat(skills): always_loaded works for trusted tiers via frontmatter or config`

## Phase 3 — Hide skill tools from the agent's deferred-tool list

Goal: skill tools no longer appear in the system prompt's "Available tools" list. They enter `ctx.tools.extra` only via `activate_skill`.

1. In `src/decafclaw/tools/tool_registry.py`:
   - `build_deferred_list_text` already separates `skill_tools` from `core_tools` / `mcp_tools` for grouping. Change: omit the "### Skills" section from the rendered output entirely. The skill tools array still exists for internal classification but doesn't render to the agent.
   - Decide whether to keep the data structure (for `tool_search`'s catalog match) or rename it. Keep it — `tool_search` needs it.
2. In `src/decafclaw/tool_definitions.py`:
   - Verify that classification still records skill tools in `ctx.tools.deferred_pool` so `tool_search` can search them. They just don't render.
3. Update the skill catalog text in `build_catalog_text` to remove the "you MUST activate" accusatory wording and replace with neutral "call activate_skill(name) to load tools."
4. Tests:
   - System-prompt deferred-tool list contains no skill-tool entries.
   - `ctx.tools.deferred_pool` still contains skill tool defs (for tool_search).
   - Always-loaded skill tools still appear in the active tool list.

Commit: `feat(skills): hide skill tools from system prompt until activation`

## Phase 4 — Trust-tier bypass in `tool_activate_skill`

Goal: bundled / admin / extra skills skip confirmation; workspace still confirms.

1. In `src/decafclaw/tools/skill_tools.py`, `tool_activate_skill` (~lines 147-165):
   - Insert the tier rung in the precedence chain. After `"deny"` check, before the existing `"always"` perms check: if `skill_info.trust_tier != "workspace"`, treat as approved without confirmation. Do NOT write to `skill_permissions.json` (the trust is implicit in placement).
2. Update the docstring describing the precedence chain.
3. Tests:
   - Trusted-tier skill: activates without confirmation, no `skill_permissions.json` write.
   - Workspace skill: today's confirmation flow runs.
   - Workspace skill with prior `"always"` perm: still activates without confirmation.
   - Workspace skill with `"deny"` perm: refused.
   - `"deny"` for a trusted-tier skill: still wins (precedence: deny > tier).

Commit: `feat(skills): bypass activate_skill confirmation for trusted tiers`

## Phase 5 — `tool_search` returns skill names

Goal: keyword search matches catalog + hidden tool inventory; returns skills.

1. In `src/decafclaw/tools/search_tools.py`:
   - Rework `tool_search` to:
     - Match user query against (a) skill catalog (name + description) and (b) hidden skill-tool inventory (name + description per skill tool).
     - For tool-name/description matches, look up the owning skill via `config.skill_tool_owners` and surface the skill, not the tool.
     - Return skill names (with their descriptions) and an instruction to call `activate_skill(name)`.
   - Non-skill deferred tools (if any exist) still surface as today.
2. Update the tool definition for `tool_search` (description + parameters) to reflect new behavior — "find skills (and tools they provide) by keyword; activate the skill to use its tools."
3. Tests:
   - `tool_search("edit prose")` returns the writing-clearly skill, not edit_with_strunk.
   - `tool_search("edit_with_strunk")` (recalled tool name) returns writing-clearly via the hidden-inventory match.
   - Duplicate matches (multiple tools in the same skill match the same keyword) dedupe to one skill entry.
   - `tool_search` on always-loaded skill tools either returns the skill or treats it as already-active (whichever pattern fits — decide during implementation).

Commit: `feat(skills): tool_search returns skills, not individual tools`

## Phase 6 — Unknown-tool error names the owning skill

Goal: agent that guesses a hidden tool name gets a direct pointer to recovery.

1. In `src/decafclaw/tools/__init__.py`, `execute_tool` unknown-tool branch (~lines 252-275):
   - Look up the failed `name` in `config.skill_tool_owners`.
   - If found, look up the owning skill in `ctx.config.discovered_skills`:
     - Skill not in `ctx.skills.activated`, trust tier non-workspace OR perms approved: `[error: '{name}' is provided by the '{skill}' skill, which is not activated. Call activate_skill('{skill}') first.]`
     - Skill is workspace and unapproved: `[error: '{name}' is provided by the workspace skill '{skill}', which has not been approved. Call activate_skill('{skill}') to request user approval.]`
     - Skill denied: `[error: '{name}' is provided by skill '{skill}', which has been denied. Tool unavailable.]`
   - If not in `skill_tool_owners`: existing close-match suggestion path.
2. Tests:
   - Each branch above triggers the right error wording.
   - Existing close-match suggestion path still fires for typos that don't match any skill tool.

Commit: `feat(skills): unknown-tool error names the owning skill`

## Phase 7 — Preempt-skill hint cleanup

Goal: keep the hint, sharpen wording, drop tool enumeration (no longer makes sense — those tools are hidden).

1. In `src/decafclaw/context_composer.py`, `_compose_preempt_skill_matches`:
   - Update the rendered hint text. Keep it short; remove the "their tools are NOT loaded yet" framing (the agent never sees them either way). Use imperative phrasing: "These skills look relevant to the current message. Call activate_skill(name) to load their tools." then a bullet list of skill names.
   - No tool enumeration.
2. Tests (existing tests on this function exist — verify):
   - Hint emitted when keyword overlap ≥ 1 with any non-active skill.
   - Hint includes matched skill names.
   - Hint suppressed when no matches.

Commit: `feat(skills): sharpen preempt-skill hint, drop tool enumeration`

## Phase 8 — Docs

1. Update `docs/skills.md`:
   - Document the four trust tiers and what they imply for activation.
   - Document `config.skills_always_loaded` and how it composes with frontmatter `always_loaded`.
   - Update the section describing skill discoverability — emphasize the catalog as the disclosure surface.
2. Update `docs/tool-search.md`:
   - Rewrite to describe skill-level results.
3. Update `docs/preemptive-tool-search.md`:
   - Reflect the simplified hint and the dropped tool enumeration.
4. Update `docs/tool-priority.md` if it references the deferred-tool list rendering — verify.
5. Update `CLAUDE.md` only if the convention changes — likely a small note in the skills section about progressive disclosure being skill-level, plus the trust-tier vocabulary.

Commit: `docs(skills): document trust tiers, skill-level disclosure, skills_always_loaded config`

## Phase 9 — Smoke test

Manual end-to-end verification (no commit unless something needs fixing):

1. Build a fresh checkout, run the agent, ask Flash to edit a blog post (the original failure case).
2. Verify trace:
   - Agent reads the catalog, no skill tool names visible.
   - Agent calls `activate_skill('tabstack')` — silent, no confirmation, body delivered.
   - Agent calls `tabstack_extract_markdown` — works.
   - Agent calls `activate_skill('writing-clearly')` — silent.
   - Agent calls `edit_with_strunk` — works.
   - Total: 4 tool calls in the happy path.
3. Add both skills to `config.skills_always_loaded`. Re-run the same prompt. Verify trace:
   - Agent calls `tabstack_extract_markdown` directly — works (skill auto-loaded at turn start).
   - Agent calls `edit_with_strunk` — works.
   - Total: 2 tool calls.
4. Test the unknown-tool error: prompt Flash to call a tool name from a not-yet-activated skill, verify the error suggests `activate_skill`.
5. Test `tool_search`: prompt "find me a skill for editing prose", verify it returns writing-clearly.
6. Record findings in `notes.md`.

## Phase 10 — Lint, typecheck, full test sweep, PR

1. `make lint`, `make typecheck`, `make check`, `make test` — full clean run.
2. `uv run pytest tests/test_skills.py contrib/skills/ -v` — focused suite.
3. Push branch, open PR against main with `Closes #547`.
4. PR body covers: motivation (system prompt led agent into invisible-gate calls), architecture (skill-level disclosure, trust tiers, always_loaded extension), failure modes (workspace approval, deny precedence), and the smoke-test result. Reference dev-session artifacts.

## Out of scope (deferred to follow-ups)

- Deprecating the `auto-approve` frontmatter flag.
- Per-skill `trust_tier` override frontmatter.
- "Batch activation" tool that activates multiple skills in one call. Always-loaded covers the common case.
- An auto-load eviction mechanism if `always_loaded` skills cause context bloat. Wait for it to bite.

## Open question to revisit if it surfaces during implementation

- **Subagent / child-context inheritance**: today child agents inherit the parent's activated skills (`delegate_task` copies `ctx.tools.extra` and `ctx.skills.data` but clears `ctx.skills.activated`). With skill tools now hidden until activation, the inheritance semantics need to stay consistent — child still has access to parent's tools, but can't activate new skills itself (existing restriction). Verify Phase 4-5 changes don't break this.
