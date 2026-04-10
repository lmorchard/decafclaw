# Project Skill

Structured workflow for complex multi-step tasks. Guides the agent through
a lifecycle of brainstorm → spec → plan → execute → done, with persistent
markdown artifacts at each stage.

## When to use

Any task involving 3+ steps, research, or work spanning multiple turns.
Not for quick one-off questions.

## Modes

- **Normal:** Pauses at review gates (spec_review, plan_review) for user feedback.
- **Express:** Auto-advances through all phases, still generating artifacts.

Switch at any time with `project_set_mode`.

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
| `project_status` | Check current state and progress |
| `project_list` | List all projects |
| `project_set_mode` | Switch normal/express mode |
| `project_advance` | Move to next phase (or backward) |
| `project_update_spec` | Write/update the spec |
| `project_update_plan` | Write/update the plan |
| `project_next` | Get the next actionable step |
| `project_update_step` | Update a step's status |
| `project_add_steps` | Insert new steps into the plan |
| `project_note` | Append a timestamped note |

## User commands

- `!project` / `/project` — list or show status
- `!project create <description>` — create a new project
- `!project status <slug>` — check a specific project
- `!project list` — list all projects

## Execution loop

The agent follows a tight loop during execution:

1. `project_next` → get next step
2. `project_update_step(step, "in_progress")` → mark it started
3. Do the work
4. `project_update_step(step, "done", note="...")` → mark it done
5. Repeat

For parallel work, use `delegate_task` for independent steps.
