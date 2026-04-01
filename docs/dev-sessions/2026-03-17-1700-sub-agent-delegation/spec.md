# Sub-Agent Delegation

## Problem

The agent runs as a single sequential loop. For complex tasks with independent subtasks, there's no way to fork work to focused child agents.

## Goal

A `delegate` tool that spawns child agents to handle subtasks, running them concurrently when multiple tasks are specified. Results flow back as the tool result.

## Design

### Tool interface

```
delegate(tasks=[
  {task: "Look up the weather in Portland", tools: ["tabstack_research"]},
  {task: "Search memories for cocktail recipes", tools: ["memory_search"]}
])
```

- `tasks`: list of subtask objects, each with:
  - `task` (required): the task description, becomes the child's user message
  - `tools` (required): allowlist of tool names the child can use
  - `system_prompt` (optional): override the default child system prompt
- Single task: returns the child's text response directly
- Multiple tasks: returns labeled results (`Task 1: ...\n\nTask 2: ...`)

### Child agent behavior

- Gets a fresh empty history (no parent context — parent assembles what's needed in the task description)
- Fixed default system prompt: "Complete the following task. Be concise and focused."
- Optional system_prompt override per task
- Same LLM model as parent
- No nesting — `delegate` tool is excluded from child's available tools
- Parent's cancel_event propagated to children

### Config

- `child_max_tool_iterations`: default 10 (vs parent's 30)
- `child_timeout_sec`: default 120

### Failure handling

- Child LLM error → returned as error text in the tool result
- Child hits max iterations → returns accumulated text + "[reached max iterations]"
- Child timeout → returns "[subtask timed out after 120s]"
- Parent cancelled → children cancelled via shared cancel_event
- Individual child failure in parallel run doesn't cancel siblings — all results collected

### Implementation

- Each task becomes a `run_agent_turn` call with a forked context
- Parallel tasks via `asyncio.gather`
- Forked context carries: filtered tools, child system prompt, cancel event, child config overrides
- Lives in `src/decafclaw/tools/delegate.py`

## Out of scope (v1)

- Nested delegation (child delegating further)
- Model routing (child using different model)
- Streaming child progress to the parent's UI
- Persistent child conversations

## References

- Issue: #18
- Related: #17 (spec/plan/execute), #23 (conversation handoff)
- Key files: `src/decafclaw/agent.py` (run_agent_turn), `src/decafclaw/context.py` (fork)
