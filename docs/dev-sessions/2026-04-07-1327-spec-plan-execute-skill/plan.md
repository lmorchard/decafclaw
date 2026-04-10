# Plan: Project Skill

## Overview

Build a bundled skill at `src/decafclaw/skills/project/` with a state machine, plan parser, and 11 tools. The work is split into 4 phases: core data model, tools, SKILL.md, and integration/testing. Each phase builds on the previous.

## Steps

### Phase 1: Core data model and state machine

- [ ] 1. Create `src/decafclaw/skills/project/__init__.py` and `src/decafclaw/skills/project/state.py`
  - [ ] 1.1. Define `ProjectState` enum: `created`, `brainstorming`, `spec_review`, `planning`, `plan_review`, `executing`, `done`
  - [ ] 1.2. Define `TRANSITIONS` dict mapping each state to its valid next states
  - [ ] 1.3. Define `ProjectInfo` dataclass: slug, description, status, mode, created_at, updated_at, directory path
  - [ ] 1.4. Implement `load_project(config, slug_or_dir) → ProjectInfo` — finds project directory, reads project.json
  - [ ] 1.5. Implement `save_project(info)` — writes project.json, updates `updated_at`
  - [ ] 1.6. Implement `list_projects(config) → list[ProjectInfo]` — scans `workspace/projects/`
  - [ ] 1.7. Implement `validate_transition(current, target) → bool` using TRANSITIONS

- [ ] 2. Create `src/decafclaw/skills/project/plan_parser.py` — plan.md parsing and manipulation
  - [ ] 2.1. Define `Step` dataclass: number (str like "1", "1.2"), description, status (pending/in_progress/done/skipped), note (optional), children (list[Step])
  - [ ] 2.2. Implement `parse_plan(content: str) → tuple[str, list[Step]]` — extract overview text and step tree from markdown. Handle `[ ]`, `[>]`, `[x]`, `[-]` checkboxes and indented sub-steps.
  - [ ] 2.3. Implement `render_plan(overview: str, steps: list[Step]) → str` — serialize back to markdown
  - [ ] 2.4. Implement `find_step(steps, number: str) → Step | None` — lookup by number
  - [ ] 2.5. Implement `next_actionable(steps) → Step | None` — first pending step, or first pending sub-step within an in-progress parent
  - [ ] 2.6. Implement `insert_steps(steps, after_number: str, new_descriptions: list[str]) → list[Step]` — insert new steps/sub-steps, auto-number them
  - [ ] 2.7. Implement `update_step_status(steps, number: str, status, note?) → list[Step]`
  - [ ] 2.8. Implement `plan_progress(steps) → tuple[int, int]` — (completed, total) counting leaf steps only

- [ ] 3. Write tests for state machine and plan parser
  - [ ] 3.1. Test valid/invalid transitions
  - [ ] 3.2. Test plan round-trip: parse → render → parse produces same result
  - [ ] 3.3. Test next_actionable with various step states (all pending, some done, sub-steps, in-progress parent)
  - [ ] 3.4. Test insert_steps: after top-level, as sub-steps, renumbering
  - [ ] 3.5. Test plan_progress with mixed statuses

### Phase 2: Tool implementations

- [ ] 4. Create `src/decafclaw/skills/project/tools.py` — lifecycle tools
  - [ ] 4.1. `project_create(ctx, description, slug="", mode="normal")` — generate timestamp+slug directory name, create dir, write project.json, create empty spec.md/plan.md/notes.md, set status to `brainstorming`, return project path and status
  - [ ] 4.2. `project_status(ctx, project)` — load project, read current phase artifact, compute plan progress if in executing state, return formatted summary
  - [ ] 4.3. `project_list(ctx)` — scan workspace/projects, return table of slug/status/description/updated_at
  - [ ] 4.4. `project_set_mode(ctx, project, mode)` — validate mode string, update project.json

- [ ] 5. Implement state transition and artifact tools
  - [ ] 5.1. `project_advance(ctx, project, target_status="")` — validate transition, check artifact non-empty for phase gate, update status. Optional `target_status` for backward transitions (executing→planning, executing→brainstorming). Forward transitions auto-determine next state.
  - [ ] 5.2. `project_update_spec(ctx, project, content)` — validate state is brainstorming or spec_review, write spec.md
  - [ ] 5.3. `project_update_plan(ctx, project, content)` — validate state is planning or plan_review, write plan.md

- [ ] 6. Implement execution-phase tools
  - [ ] 6.1. `project_next(ctx, project)` — validate executing state, parse plan, return next_actionable with context (previous done step, next steps preview)
  - [ ] 6.2. `project_update_step(ctx, project, step, status, note="")` — parse plan, update step, render and write plan.md. If all steps done, return hint that project can be advanced to done.
  - [ ] 6.3. `project_add_steps(ctx, project, after_step, steps)` — parse plan, insert steps, renumber, render and write
  - [ ] 6.4. `project_note(ctx, project, content)` — append timestamped entry to notes.md

- [ ] 7. Write TOOLS dict and TOOL_DEFINITIONS list
  - [ ] 7.1. Map all 11 tool functions into TOOLS dict
  - [ ] 7.2. Write OpenAI-format TOOL_DEFINITIONS with descriptions and parameter schemas
  - [ ] 7.3. Tool descriptions should guide behavior: e.g., project_next description should say "Call this at the start of each execution step to orient yourself"

### Phase 3: SKILL.md and integration

- [ ] 8. Write `src/decafclaw/skills/project/SKILL.md`
  - [ ] 8.1. Frontmatter: name, description, user-invocable, argument-hint
  - [ ] 8.2. Body: workflow overview, phase-by-phase guidance, tips on when to delegate, resumability instructions, mode switching guidance
  - [ ] 8.3. Keep it concise — this goes into context budget. Focus on behavioral directives, not documentation.

- [ ] 9. Wire up command handling for `!project` / `/project`
  - [ ] 9.1. Review how existing user-invocable skills handle argument parsing (check commands.py)
  - [ ] 9.2. SKILL.md `user-invocable: true` and `argument-hint` should be sufficient — the skill body tells the agent how to interpret `$ARGUMENTS`
  - [ ] 9.3. Test: `!project` → list, `!project create <desc>` → create, `!project status <slug>` → status

### Phase 4: Testing and polish

- [ ] 10. Write integration-style tests
  - [ ] 10.1. Test full lifecycle: create → brainstorm → spec → plan → execute steps → done
  - [ ] 10.2. Test express mode auto-advance
  - [ ] 10.3. Test backward transitions (executing → planning → plan_review → executing)
  - [ ] 10.4. Test resumability: load existing project, check status, continue execution

- [ ] 11. Update documentation
  - [ ] 11.1. Add project skill to CLAUDE.md key files list
  - [ ] 11.2. Add convention notes for the project skill
  - [ ] 11.3. Update AGENT.md if the skill should be mentioned in behavioral rules
  - [ ] 11.4. Create `docs/project-skill.md` feature doc and add to `docs/index.md`

- [ ] 12. Lint, typecheck, test, commit
  - [ ] 12.1. `make check` — fix any issues
  - [ ] 12.2. `make test` — fix any failures
  - [ ] 12.3. Commit with `Closes #17`
