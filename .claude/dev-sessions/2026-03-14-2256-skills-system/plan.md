# Skills System — Implementation Plan

## Overview

8 phases, each ending with lint + test + commit. Phases build on each other sequentially — no orphaned code.

---

## Phase 1: SKILL.md Parser and Skill Catalog Data Model

**Goal:** Parse SKILL.md files into a data structure. No loading, no activation — just parsing.

**Prompt:**

Create `src/decafclaw/skills.py` with a SKILL.md parser and catalog data model.

Requirements:
1. Define a `SkillInfo` dataclass with fields: `name` (str), `description` (str), `location` (Path — path to the skill directory), `has_native_tools` (bool — whether `tools.py` exists in the directory), `requires_env` (list[str] — env vars needed), `user_invocable` (bool, default True), `disable_model_invocation` (bool, default False), `body` (str — markdown body after frontmatter, for lazy use later).
2. Write a `parse_skill_md(path: Path) -> SkillInfo | None` function that:
   - Reads the file at `path`
   - Splits YAML frontmatter (between `---` markers) from the markdown body
   - Parses frontmatter with `yaml.safe_load` (add `pyyaml` dependency)
   - Returns `None` with a log warning if `name` or `description` is missing, or if YAML is unparseable
   - Checks for `tools.py` in the same directory as the SKILL.md
   - Populates all SkillInfo fields from frontmatter, with sensible defaults
3. Create `tests/test_skills.py` with tests:
   - Parses a valid SKILL.md with all fields
   - Parses minimal SKILL.md (just name + description)
   - Returns None for missing description
   - Returns None for unparseable YAML
   - Detects `has_native_tools` when tools.py exists alongside SKILL.md
   - Handles `requires.env` as a list

Use `tmp_path` fixtures to create test SKILL.md files on disk.

Lint and test after.

---

## Phase 2: Skill Discovery — Scan Paths and Build Catalog

**Goal:** Scan the three skill directories and build an ordered catalog, respecting priority and `requires.env` gating.

**Prompt:**

Add skill discovery to `src/decafclaw/skills.py`.

Requirements:
1. Write a `discover_skills(config) -> list[SkillInfo]` function that:
   - Builds the scan paths list from config: `[config.workspace_path / "skills", config.agent_path / "skills", Path(__file__).parent / "skills"]`
   - For each path that exists, scans for subdirectories containing `SKILL.md`
   - Calls `parse_skill_md` on each
   - Checks `requires.env` — for each env var in the list, checks `os.environ.get(var)`. If any are missing, skip the skill (log at debug level, not warning)
   - Handles name collisions: first-found wins (workspace overrides agent-level overrides bundled), log a debug message when a lower-priority skill is shadowed
   - Returns the final list of SkillInfo entries
2. Write a `build_catalog_text(skills: list[SkillInfo]) -> str` function that:
   - Returns empty string if no skills
   - Returns a formatted string like:
     ```
     ## Available Skills

     The following skills are available. Use the activate_skill tool to load a skill before using it.

     - **tabstack**: Web browsing, content extraction, research, and automation.
     ```
3. Add tests to `tests/test_skills.py`:
   - Discovers skills from a single directory
   - Priority ordering: workspace skill shadows bundled skill with same name
   - Skips skills with unmet `requires.env`
   - Includes skills when `requires.env` is satisfied (use `monkeypatch.setenv`)
   - Skips directories without SKILL.md
   - `build_catalog_text` formats correctly, returns empty string for empty list

Lint and test after.

---

## Phase 3: Inject Catalog into System Prompt

**Goal:** Wire discovery into startup so the skill catalog appears in the system prompt after AGENT.md.

**Prompt:**

Integrate skill discovery into the system prompt assembly.

Requirements:
1. In `src/decafclaw/prompts/__init__.py`, modify `load_system_prompt(config)`:
   - After assembling SOUL.md + AGENT.md + USER.md sections, call `discover_skills(config)` and `build_catalog_text(skills)`
   - If catalog text is non-empty, append it as an additional section
   - Store the discovered skills list on `config.discovered_skills` so the activation tool can find them later (or return it alongside the prompt — discuss tradeoff)
2. Actually, since config is a dataclass and we don't want to add mutable state to it, instead:
   - Have `load_system_prompt` return a tuple: `(prompt_text, discovered_skills)`
   - Update `src/decafclaw/__init__.py` (the `main()` function) to unpack both values
   - Store `discovered_skills` on `app_ctx` so it's available to tools
3. Remove the tabstack init from `main()` — just delete the `if config.tabstack_api_key` block. Tabstack will be initialized lazily via skill activation after Phase 5.
4. Update `run_interactive` in `agent.py` to read the tools list dynamically (the `TOOL_DEFINITIONS` print at startup should still work — tabstack tools won't be in it, which is correct).
5. Update any existing tests that call `load_system_prompt` to handle the new return type.

Lint and test after.

---

## Phase 4: Context Extensions for Per-Conversation Skill State

**Goal:** Add `extra_tools` and `extra_tool_definitions` to Context, and wire the agent loop to use them.

**Prompt:**

Extend Context and the agent loop to support dynamically registered tools.

Requirements:
1. In `src/decafclaw/context.py`, no changes to the class itself — we'll use the existing dynamic attribute pattern (setattr). But document the convention.
2. In `src/decafclaw/agent.py`, modify `run_agent_turn`:
   - When building the tools list for `call_llm`, merge base `TOOL_DEFINITIONS` with `getattr(ctx, "extra_tool_definitions", [])`
   - Pass the merged list to `call_llm`
3. In `src/decafclaw/tools/__init__.py`, modify `execute_tool`:
   - After checking the global `TOOLS` dict, also check `getattr(ctx, "extra_tools", {})`
   - Extra tools take precedence over global tools (so a skill can override a built-in if needed, though we won't use this yet)
4. Add tests:
   - `test_execute_tool_with_extra_tools`: set `ctx.extra_tools` to a dict with a mock tool function, verify `execute_tool` calls it
   - `test_extra_tools_not_present`: verify `execute_tool` still works when ctx has no `extra_tools` attribute (backward compat)

Lint and test after.

---

## Phase 5: Skill Permissions and Activation Tool

**Goal:** Implement the `activate_skill` tool with permission checking and lazy loading of native Python skills.

**Prompt:**

Create `src/decafclaw/tools/skill_tools.py` with the activation tool and permissions system.

Requirements:
1. **Permissions helpers:**
   - `_permissions_path(config) -> Path`: returns `config.agent_path / "skill_permissions.json"`
   - `_load_permissions(config) -> dict`: reads and returns the JSON file, returns `{}` if missing/corrupt
   - `_save_permission(config, skill_name, value)`: reads existing, adds/updates entry, writes back. This function is called by the host-side confirmation handler, not by the tool itself.

2. **Activation tool: `tool_activate_skill(ctx, name: str) -> str`:**
   - Get the discovered skills list from `ctx.discovered_skills` (set during startup in Phase 3)
   - Find the skill by name. If not found, return error message.
   - Check if already activated: `getattr(ctx, "activated_skills", set())`. If already in set, return "Skill '{name}' is already active."
   - Check permissions via `_load_permissions(ctx.config)`:
     - If skill has `"always"` permission, proceed directly
     - Otherwise, publish `tool_confirm_request` with `tool="activate_skill"`, `command=f"Activate skill: {name}"`, and `skill_name=name` (extra field for the handler). Wait for confirmation (same asyncio.Event pattern as shell_tools.py, 60s timeout).
     - On denial or timeout, return error.
     - On approval, check if `confirm_result` includes `"always": True` — if so, call `_save_permission`.
   - **Load the skill:**
     - Read SKILL.md body from `skill_info.body`
     - If `skill_info.has_native_tools`:
       - `importlib.import_module` or `importlib.util.spec_from_file_location` to load `tools.py` from the skill directory
       - Call `init(config)` if it exists (auto-detect sync/async like `execute_tool`)
       - Merge the module's `TOOLS` into `ctx.extra_tools` (initializing the dict if needed)
       - Merge the module's `TOOL_DEFINITIONS` into `ctx.extra_tool_definitions` (initializing the list if needed)
       - Build a list of newly available tool names
     - Add skill name to `ctx.activated_skills` set
     - Return the SKILL.md body, plus for native skills append: `"\n\nThe following tools are now available: tool1, tool2, ..."`

3. **Tool definition and registry:**
   - `SKILL_TOOLS` dict and `SKILL_TOOL_DEFINITIONS` list, same pattern as other tool modules
   - Tool description: `"Activate a skill to make its capabilities available in this conversation. Check the Available Skills section in your instructions for what's available. REQUIRES USER CONFIRMATION unless previously approved."`

4. **Register in `tools/__init__.py`:**
   - Import and merge `SKILL_TOOLS` and `SKILL_TOOL_DEFINITIONS` into the combined registry

5. **Extend the confirmation event to support "always":**
   - The `tool_confirm_response` event gains an optional `"always": True` field
   - The tool checks for this field and calls `_save_permission` accordingly

6. **Tests in `tests/test_skills.py` (extending the existing file):**
   - Activation of a skill that has "always" permission: no confirmation needed, returns body
   - Activation of already-activated skill: returns "already active"
   - Activation of unknown skill: returns error
   - Native skill activation: tools.py is imported, init() called, tools registered on ctx
   - Permissions file read/write: save permission, load it back
   - Permissions file missing: returns empty dict

For the confirmation flow tests, mock the event bus rather than testing the full async confirmation — that's integration-level.

Lint and test after.

---

## Phase 6: Extend Confirmation UX for "Always" Option

**Goal:** Update the interactive terminal and Mattermost confirmation handlers to support the third "yes, always" option.

**Prompt:**

Extend the confirmation handlers in `agent.py` (interactive) and `mattermost.py` to support "always" responses.

Requirements:
1. **Interactive mode (`agent.py`, `run_interactive`, the `on_progress` handler):**
   - When `event_type == "tool_confirm_request"`:
     - Change the prompt from `"Approve? (y/n): "` to `"Approve? [y]es / [n]o / [a]lways: "`
     - Parse the answer: `y`/`yes` → approved, `n`/`no` → denied, `a`/`always` → approved + always
     - Publish `tool_confirm_response` with `"approved": True/False` and optionally `"always": True`

2. **Mattermost (`mattermost.py`):**
   - In the `tool_confirm_request` handler, update the message to mention the three options:
     `"React with 👍 to approve, 👎 to deny, or ✅ to always approve."`
   - In `_poll_confirmation`, add a check for the `white_check_mark` / `heavy_check_mark` emoji:
     - If found, publish `tool_confirm_response` with `"approved": True, "always": True`

3. No new tests needed for this phase — these are UI-level changes best verified manually. The confirmation flow itself is already tested via the event bus mocking in Phase 5.

Lint and test after (ensure existing tests still pass).

---

## Phase 7: Migrate Tabstack to a Skill

**Goal:** Move tabstack_tools.py into a proper skill directory with SKILL.md.

**Prompt:**

Extract tabstack from the hardcoded tool registry into a bundled skill.

Requirements:
1. Create directory `src/decafclaw/skills/` with `__init__.py` (empty).
2. Create directory `src/decafclaw/skills/tabstack/`.
3. Create `src/decafclaw/skills/tabstack/SKILL.md`:
   ```yaml
   ---
   name: tabstack
   description: "Web browsing, content extraction, deep research, and browser automation via Tabstack API."
   requires:
     env:
       - TABSTACK_API_KEY
   ---
   ```
   Body should contain usage instructions for the agent (can be adapted from the existing tool descriptions — when to use extract_markdown vs research vs automate, etc.)

4. Move `src/decafclaw/tools/tabstack_tools.py` → `src/decafclaw/skills/tabstack/tools.py`:
   - Keep all the code the same
   - The `init_tabstack` function becomes the skill's `init(config)`:
     - Rename to `init(config)`
     - Pull `api_key` and `api_url` from config inside the function (currently passed as args)
   - Keep `TABSTACK_TOOLS` as `TOOLS` and `TABSTACK_TOOL_DEFINITIONS` as `TOOL_DEFINITIONS` (matching the skill convention from the spec)

5. Remove from `src/decafclaw/tools/__init__.py`:
   - Remove the tabstack import
   - Remove `TABSTACK_TOOLS` and `TABSTACK_TOOL_DEFINITIONS` from the merged registries

6. Update `tests/test_imports.py` if it checks for tabstack tool imports.

7. Verify that `discover_skills` in Phase 2 picks up the bundled skill at `Path(__file__).parent / "skills"` — this should already work since the `skills/` directory is now inside the `decafclaw` package.

8. Add a test: with `TABSTACK_API_KEY` set in env, discover_skills finds the tabstack skill. Without it, tabstack is absent from the catalog.

Lint and test after.

---

## Phase 8: Integration Verification and Cleanup

**Goal:** End-to-end verification, documentation updates, final cleanup.

**Prompt:**

Final integration pass and documentation.

Requirements:
1. **Manual verification checklist** (interactive mode):
   - Start with `TABSTACK_API_KEY` set → tabstack appears in "Available Skills" in system prompt
   - Start without `TABSTACK_API_KEY` → no tabstack in catalog
   - Agent conversation → agent calls `activate_skill("tabstack")` → confirmation prompt appears → approve → tabstack tools available in subsequent turns
   - Re-activation → "already active" response
   - "Always" approval → check `skill_permissions.json` written correctly → restart → no confirmation needed

2. **Update documentation:**
   - `CLAUDE.md`: add skills to key files, update conventions section
   - `README.md`: add skills section (discovery, activation, bundled skills, creating skills)
   - `docs/BACKLOG-DEVINFRA.md`: mark skills system as done (move to BACKLOG.md done section)
   - `docs/CONTEXT-MAP.md`: update system prompt layout to show skills catalog

3. **Update `run_interactive` tool listing** — currently prints `TOOL_DEFINITIONS` at startup. Should note that additional tools may become available via skill activation.

4. Run full test suite, lint. Commit.

---

## Summary of Phases

| Phase | What | Key Files | Tests |
|-------|------|-----------|-------|
| 1 | SKILL.md parser + SkillInfo dataclass | `skills.py` | 6 tests |
| 2 | Discovery (scan + catalog) | `skills.py` | 6 tests |
| 3 | Wire into system prompt | `prompts/__init__.py`, `__init__.py` | update existing |
| 4 | Context extensions + agent loop | `context.py`, `agent.py`, `tools/__init__.py` | 2 tests |
| 5 | Activation tool + permissions | `tools/skill_tools.py` | 6 tests |
| 6 | "Always" confirmation UX | `agent.py`, `mattermost.py` | manual |
| 7 | Tabstack migration | `skills/tabstack/` | 1-2 tests |
| 8 | Integration + docs | docs, README | manual |
