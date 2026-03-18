# Plan: Sub-Agent Delegation

## Context

`run_agent_turn(ctx, user_message, history)` is the core agent loop. It uses `ctx.config` for LLM settings, `_build_tool_list(ctx)` for available tools, and `ctx.cancelled` for cancellation. `Context.fork()` creates a child context sharing the event bus. `ctx.allowed_tools` already gates tool execution in `execute_tool`.

The delegate tool needs to: fork a context, restrict tools, set a child system prompt, call `run_agent_turn`, and collect results.

## Step 1: Config — add child agent settings

**Builds on**: existing Config dataclass in `config.py`

**What**: Add two config fields:
- `child_max_tool_iterations: int = 10`
- `child_timeout_sec: int = 120`

Also add them to `load_config()` with env var support (`CHILD_MAX_TOOL_ITERATIONS`, `CHILD_TIMEOUT_SEC`).

**After this step**: Config is ready, nothing wired up yet.

---

## Step 2: Implement `_run_child_turn` helper

**Builds on**: Step 1 config, existing `run_agent_turn` in agent.py

**What**: In a new file `src/decafclaw/tools/delegate.py`, implement a helper that runs a single child agent turn:

```python
async def _run_child_turn(parent_ctx, task, tools, system_prompt=None):
```

This function:
1. Forks the parent context with a fresh context_id
2. Creates a child config via `dataclasses.replace()` with:
   - `max_tool_iterations = config.child_max_tool_iterations`
   - `system_prompt` set to the child prompt (default or override)
3. Sets `child_ctx.allowed_tools` to the requested tool names (as a set)
4. Filters `TOOL_DEFINITIONS` + `extra_tool_definitions` + MCP definitions down to only the allowed tools, sets as `child_ctx.extra_tool_definitions` (and clears the base by using allowed_tools gate)
5. Propagates `parent_ctx.cancelled` to the child
6. Calls `run_agent_turn(child_ctx, task, [])` with empty history
7. Returns the ToolResult text

Wrap in `asyncio.wait_for` with `child_timeout_sec`. Catch exceptions and return error text.

**After this step**: Single child delegation works but isn't exposed as a tool yet.

---

## Step 3: Implement the `delegate` tool

**Builds on**: Step 2 helper

**What**: In `delegate.py`, implement the tool function:

```python
async def tool_delegate(ctx, tasks: list) -> str:
```

This function:
1. Validates the tasks list (each must have `task` and `tools`)
2. For a single task: calls `_run_child_turn` directly, returns its result
3. For multiple tasks: uses `asyncio.gather(*[_run_child_turn(...) for t in tasks], return_exceptions=True)` to run concurrently
4. Formats results: single → direct text, multiple → `"Task 1: ...\n\nTask 2: ..."`
5. Handles exceptions from gather (timeout, LLM error) per-task

Add tool definition with the JSON schema for the `tasks` parameter. Register in `DELEGATE_TOOLS` and `DELEGATE_TOOL_DEFINITIONS`.

**After this step**: Tool exists but isn't registered in the global tool list.

---

## Step 4: Register the delegate tool

**Builds on**: Step 3

**What**:
1. Import and merge `DELEGATE_TOOLS` / `DELEGATE_TOOL_DEFINITIONS` into `tools/__init__.py`
2. Exclude `delegate` from child's available tools to prevent nesting (in `_run_child_turn`, filter it out of allowed_tools)

**After this step**: The delegate tool is available to the agent. End-to-end flow works.

---

## Step 5: Tool definition filtering for child context

**Builds on**: Step 4

**What**: The child's LLM call needs tool definitions filtered to only the allowed tools. Currently `_build_tool_list` always includes everything.

The simplest approach: `allowed_tools` already gates execution in `execute_tool`, so a child calling a non-allowed tool gets an error. But the LLM shouldn't even see tools it can't use — that wastes context and confuses the model.

Modify `_build_tool_list(ctx)` in agent.py: if `ctx.allowed_tools` is set, filter the assembled tool list to only include definitions whose function name is in the allowed set.

**After this step**: Child agents only see the tools they're allowed to use.

---

## Step 6: Lint, test, commit

**What**:
- `make check` — lint + typecheck
- Write tests:
  - Single delegation with mock LLM
  - Parallel delegation with mock LLM
  - Child timeout handling
  - Child tool restriction (only allowed tools visible)
  - Cancel propagation
- `make test`
- Commit with `Closes #18`

---

## Step 7: Update docs

**What**:
- Add delegate tool to README tool table
- Add `CHILD_MAX_TOOL_ITERATIONS` and `CHILD_TIMEOUT_SEC` to README config table
- Update CLAUDE.md key files list with `delegate.py`
- Create `docs/delegation.md` with usage examples
