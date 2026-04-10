# Spec: Project Skill (Spec / Plan / Execute Loop)

**Issue:** #17  
**Goal:** Give the agent a structured workflow for complex multi-step tasks, with persistent markdown artifacts at each stage and Python tools handling the mechanics.

## Problem

The agent struggles with sustained multi-step work. Without structure, it loses track of what it's doing, skips steps, and doesn't produce artifacts that enable resumability across conversations. The agent needs rails — a state machine that guides it through phases and tools that handle bookkeeping so the LLM focuses on content.

## Overview

A bundled skill called `project` that provides tools for managing structured projects. Each project lives in a workspace directory (`workspace/projects/{date}-{slug}/`) with markdown artifacts tracking the full lifecycle: brainstorm → spec → plan → execute → done.

Two interaction modes:
- **Normal mode:** The agent pauses at review gates for user feedback before advancing.
- **Express mode:** The agent runs through all phases autonomously, still generating all artifacts for context and resumability.

The user can switch to express mode at any point.

## State Machine

```
created → brainstorming → spec_review → planning → plan_review → executing → done
```

**Transitions:**
- `created → brainstorming`: Automatic on project creation.
- `brainstorming → spec_review`: Agent calls tool to submit the spec draft.
- `spec_review → planning`: User approves (normal) or auto-advance (express).
- `spec_review → brainstorming`: User requests changes — refine, don't restart.
- `planning → plan_review`: Agent calls tool to submit the plan draft.
- `plan_review → executing`: User approves (normal) or auto-advance (express).
- `plan_review → planning`: User requests changes — refine, don't restart.
- `executing → done`: All plan steps completed (or explicitly marked done).
- `executing → planning`: Replanning needed based on execution discoveries. Existing plan is refined, not discarded.
- `executing → brainstorming`: Fundamental rethink needed — back to the drawing board. Existing artifacts stay as revision context.

Invalid transitions are rejected by the tools with a clear error message indicating the current state and valid next states.

In express mode, `spec_review` and `plan_review` are auto-advanced — the agent still writes the artifacts but doesn't pause for user input.

## Project Directory

```
workspace/projects/{YYYY-MM-DD-HHMM}-{slug}/
  project.json    # State: status, mode, metadata, timestamps
  spec.md         # Specification (brainstorm output)
  plan.md         # Structured plan with steps
  notes.md        # Running notes, research, scratch
  {other files}   # Encouraged: research, intermediate outputs, etc.
```

### project.json

```json
{
  "slug": "refactor-auth",
  "description": "Refactor authentication to support OAuth2",
  "status": "executing",
  "mode": "normal",
  "created_at": "2026-04-07T13:27:00Z",
  "updated_at": "2026-04-07T14:15:00Z"
}
```

### plan.md Structure

The plan is a markdown document with a structured step list. Steps are tracked as a checklist that the tools parse and update:

```markdown
# Plan: Refactor Auth

## Overview
Brief description of approach.

## Steps

- [ ] 1. Research OAuth2 library options
  - [ ] 1.1. Compare authlib vs oauthlib
  - [ ] 1.2. Check Mattermost OAuth2 support
- [ ] 2. Design token storage schema
- [x] 3. Implement token refresh flow
  > Completed: Implemented using authlib with SQLite token store.
- [ ] 4. Update Mattermost client
- [-] 5. Write migration script
  > Skipped: Not needed — new installs only for now.
```

Step statuses:
- `[ ]` — pending
- `[>]` — in progress
- `[x]` — done (with optional completion note as blockquote)
- `[-]` — skipped (with reason as blockquote)

Sub-steps are indented under their parent. A parent step is complete when all sub-steps are complete.

## Tools

All tools take `project` as first parameter — the slug or directory name of the project.

### project_create(description, slug?, mode?)
Create a new project. Generates the directory, initializes `project.json`, creates empty artifact files. Returns the project path and confirms the state is `brainstorming`.
- `slug` is optional — derived from description if not provided.
- `mode` defaults to `"normal"`. Can be `"normal"` or `"express"`.

### project_status(project)
Read the current state of a project: status, mode, plan progress (X of Y steps done), and the contents of the current phase's artifact. The agent's go-to orientation tool.

### project_list()
List all projects in the workspace with their status, description, and last-updated time. Useful for resuming work across conversations.

### project_set_mode(project, mode)
Switch between `"normal"` and `"express"` mode at any time.

### project_advance(project)
Advance the state machine to the next phase. Validates that the current phase's artifact exists and is non-empty before allowing transition. In review states, this represents user approval (normal mode) or auto-approval (express mode).

### project_update_spec(project, content)
Write or update the spec.md file. Only valid during `brainstorming` or `spec_review` states.

### project_update_plan(project, content)
Write or update the plan.md file (full rewrite). Only valid during `planning` or `plan_review` states.

### project_next(project)
Return the next actionable step from the plan — the first pending step (or first pending sub-step within an in-progress parent). Includes the step number, description, and surrounding context (what was just completed, what comes after). Only valid during `executing` state. Returns a clear "all steps done" message when nothing remains.

### project_update_step(project, step, status, note?)
Update a specific step's status during execution. `step` is the step number (e.g., `"1"`, `"1.2"`). `status` is one of: `"pending"`, `"in_progress"`, `"done"`, `"skipped"`. Optional `note` adds a completion/skip note.

### project_add_steps(project, after_step, steps)
Insert new steps or sub-steps into the plan during execution. `after_step` is the step number to insert after (e.g., `"2"` to add after step 2, `"2.1"` to add sub-steps). `steps` is a list of step description strings. Enables mid-execution replanning without rewriting the whole plan.

### project_note(project, content)
Append a timestamped entry to notes.md. For research findings, decisions, observations, or anything worth recording.

## Skill Configuration

### SKILL.md Frontmatter
```yaml
---
name: project
description: "Structured project workflow: brainstorm, spec, plan, execute"
user-invocable: true
always-loaded: false
argument-hint: "[create|status|list] [description]"
required-skills:
  - vault
---
```

The skill body (SKILL.md markdown) provides the agent with instructions on how to use the workflow — when to brainstorm vs. plan, how to structure specs, when to delegate, etc.

### User-Invocable Commands
- `!project` / `/project` — with no args, lists active projects or shows status of current
- `!project create <description>` — shorthand for creating a new project
- `!project status <slug>` — check status of a specific project

## Behavioral Guidance (in SKILL.md body)

The SKILL.md body instructs the agent on how to use the tools effectively:

- **Brainstorming phase:** Ask the user clarifying questions. Explore requirements, edge cases, constraints. Think about what's needed before writing the spec. In express mode, use your best judgment and note assumptions.
- **Spec writing:** The spec should capture *what* and *why*, not *how*. Include acceptance criteria when possible.
- **Planning phase:** Break the work into concrete, actionable steps. Each step should be small enough to complete in one focused effort. Identify steps that could be parallelized. Consider dependencies between steps.
- **Execution phase:** Work through steps in order. Mark each step in-progress before starting, done when finished. If a step is more complex than expected, decompose it into sub-steps rather than doing untracked work. Use `project_note` to record findings and decisions. Delegate independent steps to sub-agents when appropriate.
- **Resumability:** At the start of any conversation, check for in-progress projects with `project_list`. Use `project_status` to orient yourself before continuing work.

## Non-Goals (for this iteration)

- No integration with the GitHub project board (future enhancement).
- No automatic vault page creation from project artifacts.
- No cross-project dependency tracking.
- No templates for different project types.
- No time tracking or estimates.

## Resolved Questions

1. **Review gate enforcement:** Initially planned for the agent to handle review naturally, but testing showed models skip review without mechanical enforcement. Final implementation uses `request_confirmation` in `project_advance` for forward transitions out of review states in normal mode.
2. **Auto-discovery at activation:** No automatic injection. The SKILL.md body tells the agent about `project_list` and lets it decide when to check for in-progress projects.
