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

While a project is in the `executing` phase, `project_next_task`, `project_update_step`, and `project_add_steps` also mirror the plan's steps into the sticky slot above the chat input as a `progress_tracker` widget (fail-open). The slot clears when `project_task_done` finalizes the project (all steps checked off) or when `project_advance` moves the project out of `executing` (e.g. back to planning). See [widgets.md](widgets.md#progress_tracker-widget).

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

### Phase-gated tool exposure

The table above is the full registry, but the agent never sees all of it at once. `get_tools`
(the skill's dynamic provider) recomputes the dispatchable set **every turn** from the current
project's saved status, filtering through `_PHASE_TOOLS` in `skills/project/tools.py`. With no
current project selected, only `project_create` / `project_list` / `project_switch` are offered.

The exclusions are deliberate, not incidental:

- The **review phases** (`spec_review`, `plan_review`) withhold `project_next_task`, so the agent
  can only approve, revise, or check status — it cannot skip the review by asking for the next task.
- **`executing`** withholds `project_update_plan`: the plan is settled by the time execution starts,
  and reshaping it mid-flight is what `project_advance` back to `planning` is for.
- **`done`** withholds both, keeping a finished project read-only apart from notes and switching.

**Instruction text must name only tools the *reading* phase can dispatch.** Several tools return
text telling the agent what to call next, and that text is read on the *following* turn — when the
available tools are whatever `get_tools` returns for the phase in effect *then*. A hardcoded tool
name therefore goes stale silently and dead-ends the agent, which is what [#727](https://github.com/lmorchard/decafclaw/issues/727)
fixed at four sites. Where the reading phase is known at emit time (`project_switch` knows the
switched-to project's status; `project_advance` knows its target), derive the hint from
`_PHASE_TOOLS` via `_next_action_hint` rather than hardcoding. Where it isn't — a static prompt
template read from more than one phase — name a tool dispatchable in *all* of them.

`TestPhaseInstructionConsistency` in `tests/test_project_tools.py` enforces this. It carries a
hand-maintained table of (instruction site → the phases that read it); **a new instruction site
must be added to that table**, or it goes ungraded.

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
