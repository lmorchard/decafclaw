# Project Skill

Structured workflow for complex multi-step tasks. Guides the agent through
a lifecycle of brainstorm → spec → plan → execute → done, with persistent
markdown artifacts at each stage.

## When to use

Any task involving 3+ steps, research, or work spanning multiple turns.
Not for quick one-off questions.

## State machine

```
brainstorming → spec_review → planning → plan_review → executing → done
```

Backward transitions are supported:
- `spec_review → brainstorming` (refine spec)
- `plan_review → planning` (refine plan)
- `executing → planning` (replan based on discoveries)
- `executing → brainstorming` (fundamental rethink)

## Project directory

```
workspace/projects/{YYYY-MM-DD-HHMM}-{slug}/
  project.json    # State metadata
  spec.md         # Specification
  plan.md         # Structured plan with step checklist
  notes.md        # Timestamped notes
  {other files}   # Research, scratch, intermediate outputs
```

## Plan format

Steps use a markdown checklist with status markers:

```markdown
- [ ] 1. Pending step
- [>] 2. In-progress step
- [x] 3. Done step
  > Completed: What was accomplished.
- [-] 4. Skipped step
  > Skipped: Why it was skipped.
```

Sub-steps are indented under parents. Steps can be inserted mid-execution.

## Tools

| Tool | Description |
|------|-------------|
| `project_create` | Create a new project |
| `project_next_task` | Get the next instruction for the current phase |
| `project_task_done` | Mark the current phase's work complete and advance |
| `project_status` | Check current state and progress |
| `project_list` | List all projects |
| `project_switch` | Switch the active project |
| `project_advance` | Move backward to an earlier phase (e.g. replan) |
| `project_update_spec` | Write/update the spec |
| `project_update_plan` | Write/update the plan |
| `project_update_step` | Update a step's status |
| `project_add_steps` | Insert new steps into the plan |
| `project_note` | Append a timestamped note |

## User commands

- `!project` / `/project` — list or show status
- `!project create <description>` — create a new project
- `!project status <slug>` — check a specific project
- `!project list` — list all projects

## Execution loop

The two driver tools are `project_next_task` (asks "what should I do now?") and `project_task_done` (signals "I finished — advance"). The general loop:

1. `project_next_task` → tells you what to do this turn
2. Do the work — for the executing phase, this means picking a step, marking it in_progress with `project_update_step`, completing it, and marking it done
3. `project_task_done` → advance the phase (or, in `executing`, finalize when all steps are checked off)
4. Repeat

For parallel work within a single step, use `delegate_task` for independent sub-tasks.
