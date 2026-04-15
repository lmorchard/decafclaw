# Checklist Execution Loop & Tool Cleanup

## Problem

Two underused tools need attention (#233, #234):

1. **Todo tools** (`todo_add`, `todo_complete`, `todo_list`, `todo_clear`) — always-loaded but the agent rarely reaches for them unprompted. The project skill's plan steps serve a similar purpose but with more structure. However, the project skill is heavyweight for simple multi-step tasks.

2. **Think tool** — referenced in a stale docstring but never implemented. Just needs cleanup.

## Solution

Replace the todo tools with a **checklist execution loop** — a general-purpose, always-loaded execution primitive that the agent reaches for unprompted when tasks involve 3+ distinct steps. The loop uses `end_turn=True` for mechanical reliability, ensuring the agent works through steps one at a time without wandering off.

The project skill's execute phase delegates to the checklist tools instead of maintaining its own step-tracking machinery. The project skill's "special sauce" remains its review gates (`EndTurnConfirm`) layered on top.

## Architecture

### Checklist Tools (always-loaded)

Four tools, replacing the current four todo tools:

- **`checklist_create`** — Create a checklist from a list of steps. One active checklist per conversation. Returns the first step to work on.
  - Parameters: `steps` (list of strings describing each step)
  - Returns: confirmation + first step text
  - Behavior: overwrites any existing checklist for this conversation

- **`checklist_step_done`** — Mark the current step as complete and get the next one. Returns `end_turn=True` to force a new LLM iteration with the next step injected as context.
  - Parameters: `note` (optional, brief summary of what was done)
  - Returns: next step text, or "all steps complete" if done
  - Behavior: `ToolResult(end_turn=True)` — mechanically forces a new iteration. When all steps are complete, returns with `end_turn=True` so the agent can summarize.

- **`checklist_abort`** — Abandon the current checklist. Use when the plan needs rethinking or the task is no longer relevant.
  - Parameters: `reason` (why the checklist is being abandoned)
  - Returns: confirmation that the checklist was cleared
  - Behavior: clears the checklist, no `end_turn`

- **`checklist_status`** — Show current checklist progress without advancing.
  - Parameters: none
  - Returns: formatted checklist with completion status

### Tool Descriptions (behavioral guidance)

The `checklist_create` description is the key control surface for getting the agent to reach for it unprompted. It should convey:

- **When to use**: "When you have a task that involves 3 or more distinct steps, create a checklist before starting work. This ensures methodical execution and prevents skipping steps."
- **What it does**: creates a checklist and drives you through it one step at a time
- **When NOT to use**: simple single-step tasks, questions, conversations

### Storage

- Per-conversation markdown files at `{workspace}/todos/{conv_id}.md` (same location as current todos)
- Format: markdown checkboxes with optional completion notes
  ```
  - [x] Step 1 description [done: brief note]
  - [ ] Step 2 description  ← current
  - [ ] Step 3 description
  ```
- Human-readable, crash-recoverable, inspectable
- One active checklist per conversation

### Mechanical Execution

The reliability of the loop comes from `end_turn=True`:

1. Agent calls `checklist_create(steps=[...])` → gets first step
2. Agent works on step 1
3. Agent calls `checklist_step_done(note="...")` → tool returns `end_turn=True`
4. New LLM iteration starts with next step as context
5. Repeat until all steps complete
6. Final `checklist_step_done` returns "all complete" with `end_turn=True` for summary

The agent can call `checklist_abort` at any point to bail out (e.g., if it realizes the plan is wrong). This does NOT use `end_turn` — it just clears the checklist and returns control.

### Project Skill Integration (future)

The project skill's execute phase has its own step tracking via `plan_parser.py` and `state.py`. A future session will refactor it to delegate to the checklist tools, with the project skill adding review gates on top. This session focuses on getting the standalone checklist working first.

### Think Tool Cleanup

Remove the stale "think" reference from the `core.py` docstring. No code to remove (tool was never implemented).

## Scope

### In scope

- Replace todo tools with checklist tools (same always-loaded slot)
- Mechanical execution loop via `end_turn=True`
- Tool descriptions that encourage unprompted usage
- Remove stale think tool docstring reference
- Update tests, CLAUDE.md, docs

### Out of scope

- Project skill integration (future session — get checklist working standalone first)
- Review gates in the checklist (that's the project skill's job)
- Multi-checklist per conversation
- Nested/hierarchical steps (project skill handles numbered sub-steps)
- Changes to the project skill's brainstorm/spec/plan phases

## Acceptance Criteria

1. Agent creates a checklist unprompted when given a multi-step task (validate via manual testing with different phrasings)
2. `checklist_step_done` mechanically advances to the next step via `end_turn=True`
3. `checklist_abort` cleanly exits the loop
4. `checklist_status` shows current progress
5. Checklists persist across conversation restarts (file-based storage)
6. Current todo tools and tests removed/replaced
7. Think tool docstring cleaned up
8. Project skill unchanged (integration is a follow-up session)
