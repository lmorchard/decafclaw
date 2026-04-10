# Spec: Project Skill — Workflow Control

**Branch:** `spec-plan-execute-skill` (PR #232)
**Context:** Continuing from the 2026-04-07 session. The project skill's core infrastructure works but models ignore verbal "STOP" directives in tool results, chaining through the entire lifecycle in a single turn. Direct Vertex provider confirms this is model behavior, not a LiteLLM artifact.

## Problem

The project skill relies on text instructions ("STOP: present the spec to the user") to control the agent's flow through phases. LLMs — especially smaller/faster models like Gemini Flash — treat these as suggestions and keep calling tools. The result: 62 tool calls in a single turn, blasting from brainstorming through execution to completion without ever yielding to the user.

Agent framework literature (LangGraph, Anthropic's "Building Effective Agents", CoALA) converges on a clear principle: **control flow must be mechanical, not verbal.** The LLM should not decide when to yield — the orchestrator should.

## Solution

Three complementary mechanisms that together give skills mechanical control over the agent loop:

### 1. `end_turn` signal on ToolResult

`ToolResult.end_turn` accepts `True`, `False`, or an `EndTurnConfirm` action. The agent loop checks this after each tool batch.

**`end_turn=True` (simple end):**
1. All tools in the current parallel batch execute normally
2. Their results are appended to history
3. The agent loop makes **one final LLM call with an empty tool list**, forcing text output
4. That text response is returned — no further tool calls possible

**`end_turn=EndTurnConfirm(...)` (end with confirmation gate):**
1. All tools in the batch execute normally, results appended to history
2. The agent loop presents confirmation buttons via the event bus (same mechanism as existing `request_confirmation`)
3. **If approved:** a note is injected into history and the **loop continues** — the model chains into the next phase
4. **If denied:** the loop makes a final no-tools LLM call and **ends the turn**

This is the key design: approval lets the model keep going (no stall between phases), while denial stops the model and forces it to ask for feedback. The confirmation is handled by the agent loop, not the tool — tools just declare what they need.

**Implementation notes:**
- Only `ToolResult` objects carry the signal. Bare string returns default to `end_turn=False`.
- In parallel batches, all tools execute; the signal is checked after.
- If multiple tools return `EndTurnConfirm`, only one is used (first encountered).
- Eval auto-confirm subscriber on the event bus resolves confirmations instantly.

**Which project tools use what:**
- `project_task_done` from BRAINSTORMING → `EndTurnConfirm` (spec review gate)
- `project_task_done` from PLANNING → `EndTurnConfirm` (plan review gate)
- `project_task_done` from EXECUTING → `end_turn=True` (project complete, yield)
- `project_task_done` denial → `end_turn=True` (ask for feedback)
- `project_update_spec` / `project_update_plan` → no end_turn (model continues to call `task_done` which shows buttons)
- Execution-phase tools → no end_turn (model chains freely)

### 2. Dynamic tool list per skill

Skills can export a `get_tools(ctx)` function in `tools.py` instead of (or alongside) static `TOOLS` and `TOOL_DEFINITIONS` dicts. When present, the skill loader calls `get_tools(ctx)` during tool assembly each turn, receiving a fresh tool set based on current state.

**Behavior:**
- `get_tools(ctx)` returns `(tools_dict, tool_definitions_list)` — same shape as the static exports
- Called once per iteration during tool assembly (before the LLM call)
- Falls back to static `TOOLS`/`TOOL_DEFINITIONS` if `get_tools` is not exported
- The function may do I/O (e.g., read project.json) — acceptable for one read per turn
- Only affects the skill's own tools — core tools, MCP tools, and other skills' tools are unaffected

**Phase-to-tools mapping for the project skill:**

| Phase | Available tools |
|-------|----------------|
| No active project | `project_create`, `project_list`, `project_switch` |
| BRAINSTORMING | `project_next_task`, `project_update_spec`, `project_task_done`, `project_note`, `project_status` |
| SPEC_REVIEW | `project_task_done`, `project_update_spec`, `project_status` |
| PLANNING | `project_next_task`, `project_update_plan`, `project_task_done`, `project_note`, `project_status` |
| PLAN_REVIEW | `project_task_done`, `project_update_plan`, `project_status` |
| EXECUTING | `project_next_task`, `project_task_done`, `project_update_step`, `project_add_steps`, `project_advance`, `project_note`, `project_status` |
| DONE | `project_status`, `project_list`, `project_switch`, `project_note` |

Note: `project_next_task` is excluded from SPEC_REVIEW and PLAN_REVIEW — those phases are gated by the confirmation buttons, not by the model.

### 3. Eval runner fixes

The eval runner counts tool calls cumulatively across turns. Fix: track per-turn counts using a pre-turn snapshot.

## Scope

**In scope:**
- `EndTurnConfirm` dataclass in `media.py`
- `ToolResult.end_turn` field: `bool | EndTurnConfirm = False`
- Agent loop: handle `EndTurnConfirm` (event bus confirmation, continue/end decision)
- Dynamic skill tool loading: `get_tools(ctx)` support
- Project skill: use `EndTurnConfirm` for review gates, dynamic tools per phase
- Eval runner: per-turn tool call/error counting
- Project skill evals: updated assertions
- Documentation: `docs/skills.md` and `CLAUDE.md`

**Out of scope:**
- General-purpose tool filtering hook (file issue)
- Other `EndTurnAction` types beyond confirm (file issue for future `EndTurnInput`, etc.)
- Changes to non-project skills
- Express mode

## Acceptance criteria

- Project skill evals pass on Gemini Flash
- Confirmation buttons appear at spec/plan review gates
- Approval lets the model continue into the next phase (no stall)
- Denial ends the turn (model asks for feedback)
- Execution phase chains freely
- Eval auto-confirm works seamlessly
- Per-turn eval counting
- `docs/skills.md` documents all three mechanisms
