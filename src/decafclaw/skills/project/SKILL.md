---
name: project
description: "Structured project workflow: brainstorm, spec, plan, execute multi-step tasks"
user-invocable: true
argument-hint: "[create|status|list] [description]"
---

## Project Workflow

Use projects for complex multi-step tasks. Each project follows a
lifecycle: **brainstorm → spec → plan → execute → done**.

### How to use

1. Call `project_create` to start a new project
2. Call `project_next_task` — it tells you what to do
3. Do what it says (the turn will end automatically after key actions)
4. When the user responds, continue from where you left off
5. Repeat until done

**Two key tools drive the workflow:**
- `project_next_task` — tells you what to do NOW (does not advance phases)
- `project_task_done` — signals you're done and advances to the next phase

### Spec vs Plan

- **Spec** = WHAT and WHY. This is the problem definition. Describes the goal, requirements, constraints, and acceptance criteria. Does not list steps or say how to do it.
- **Plan** = HOW. This is the implementation plan to solve the problem described in the spec. A numbered checklist of concrete action steps to implement the spec. Written AFTER the spec is approved.

These are separate phases. Do not include a plan in the spec or a spec in the plan.

### Key rules

- **Do the work, then call project_task_done** — don't skip ahead
- **During brainstorming:** Ask questions to understand the project, then write the spec (WHAT, not HOW) with `project_update_spec`
- **Review phases:** Read the user's response. If they approve, call `project_task_done`. If they have feedback, revise the artifact.
- **During planning:** Write a concrete step-by-step plan (HOW) with `project_update_plan`
- **During execution:** Work through steps freely — mark in_progress, do work, mark done, repeat
- **Write output files to the project directory**

### Command handling

When invoked as `!project` or `/project`:
- No args or `status`: call `project_list` or `project_status`
- `create <description>`: call `project_create`
- `status <slug>`: switch to that project and call `project_status`
- `list`: call `project_list`

User said: $ARGUMENTS
