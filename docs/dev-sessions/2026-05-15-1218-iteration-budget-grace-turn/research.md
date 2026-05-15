# Research: Agent loop iteration mechanics

## 1. How `max_tool_iterations` is currently counted and enforced

**Counter mechanics — implicit, via `for ... range()`:**

```python
# agent.py:467-471
for iteration in range(self.config.agent.max_tool_iterations):
    outcome = await self._run_iteration(iteration)
    if isinstance(outcome, _Final):
        return outcome.result
return await self._finalize_max_iterations()
```

- Counter starts at 0, runs through N-1 where N = `max_tool_iterations`
- No explicit counter variable — `range()` does the comparison
- Early exit when `_run_iteration` returns `_Final` (agent.py:468-470)
- Falls through to `_finalize_max_iterations()` (agent.py:471) when budget exhausted

**Exhaustion handler (`_finalize_max_iterations`, agent.py:818-833):**

- Concatenates `self.accumulated_text_parts` (agent.py:825) — any text the LLM emitted alongside tool calls during the loop
- Appends notice: `"\n\n[Agent reached max tool iterations (N) without a final response]"`
- Archives the composite message (agent.py:828)
- Returns `ToolResult(text=msg)` (agent.py:833)
- **No final LLM call is made** — the model gets cut off without a chance to wrap up

## 2. Structure of one iteration

`_run_iteration` (agent.py:524-571):

1. Cancel check (agent.py:528-530)
2. `ctx._current_iteration = iteration + 1` (agent.py:533)
3. `refresh_dynamic_tools(self.ctx)` + `build_tool_list(self.ctx)` (agent.py:535-548)
4. LLM call with full tool list: `_call_llm_with_events(..., all_tools, ...)` (agent.py:550-553)
5. Token accounting (agent.py:560-565)
6. Dispatch: `_handle_tool_calls()` or `_handle_no_tool_calls()` (agent.py:567-571)

**Existing no-tools LLM call patterns** (agent.py):

- **end_turn=True path** (agent.py:653-664): `_call_llm_with_events(..., [], ...)` after tool execution
- **EndTurnConfirm presentation call** (agent.py:615-651): `_call_llm_with_events(..., [], ...)` before requesting confirmation
- **WidgetInputPause** (agent.py:598-613): pauses for widget response, no LLM call

**Empty-response retry** (`_handle_no_tool_calls`, agent.py:668-709):

- `if not content and self.empty_retries < 1: return _Continue()` — retries LLM once when response is empty
- This retry **does consume a budget iteration** (the for-loop advances)

**Reflection** (agent.py:714-720, fires only in `_handle_no_tool_calls`):

- Returns `_Continue()` to retry when verdict says fail (agent.py:683-685)
- Also consumes a budget iteration on retry

**Compaction** (agent.py:708): fires in `_handle_no_tool_calls` after a final no-tools response.

## 3. Configuration & propagation

**Definition (config_types.py:155-180, AgentConfig):**

- `max_tool_iterations: int = 200` (parent)
- `child_max_tool_iterations: int = 10` (child)

**Env vars (config.py:390-406):**

- `MAX_TOOL_ITERATIONS` → `agent.max_tool_iterations`
- `CHILD_MAX_TOOL_ITERATIONS` → `agent.child_max_tool_iterations`

**Runtime use (agent.py:467):** Loop reads `self.config.agent.max_tool_iterations` directly each turn — no caching, no copying.

**Child agent forking (delegate.py:168-173):**

```python
child_config = replace(
    config,
    agent=replace(config.agent, max_tool_iterations=(
        max_iterations or config.agent.child_max_tool_iterations)),
    system_prompt=child_system_prompt,
)
```

- Children always get their own forked `AgentConfig` with `child_max_tool_iterations` (default 10) unless overridden
- No shared state with parent — independent budgets already.

## 4. Existing tests (tests/test_agent_turn.py — note: NOT test_agent.py)

**`test_run_agent_turn_max_iterations`** (tests/test_agent_turn.py:568-591):

```python
ctx.config.agent.max_tool_iterations = 2
tool_call_response = _mock_llm_response(
    content=None,
    tool_calls=[{"id": "tc1", "function": {"name": "memory_recent", "arguments": "{}"}}],
)
with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
    mock_llm.return_value = tool_call_response
    result = await run_agent_turn(ctx, "loop forever", history)
assert "max tool iterations" in result.text
```

**`test_run_agent_turn_max_iterations_preserves_text`** (tests/test_agent_turn.py:594-618):

- Same pattern with `content="Let me check that for you."`
- Asserts both the accumulated text AND `"max tool iterations"` notice appear in result

**Mock pattern:** patch `decafclaw.agent.call_llm` with `AsyncMock`, use `return_value` (not `side_effect`) so every call returns the same response — loop will run all iterations and exit naturally.

**Child-agent test (tests/test_delegate.py):** asserts `child_ctx.config.agent.max_tool_iterations == 10`.

## 5. "Force final message" patterns

There is no dedicated force-final-at-limit pattern today. The existing no-tools LLM call patterns (end_turn=True, EndTurnConfirm presentation) are precedent for the shape a grace turn would take: build a final LLM call with `tools=[]`, append the result to history, return `_Final`.

No `tool_choice=none` constraint is used anywhere — the `tools=[]` argument alone is sufficient to force a no-tool response.
