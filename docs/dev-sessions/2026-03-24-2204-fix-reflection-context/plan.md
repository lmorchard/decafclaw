# Reflection Context Fix — Plan

**Spec:** [spec.md](spec.md)
**Issue:** [#124](https://github.com/lmorchard/decafclaw/issues/124)

## Overview

Four steps, each building on the previous. Each step ends with lint + test passing.

---

## Step 1: Config + `build_tool_summary` signature update

**Goal:** Add `max_tool_result_len` to config, update `build_tool_summary` to use it, fix existing tests.

**Files:**
- `src/decafclaw/config_types.py`
- `src/decafclaw/reflection.py`
- `src/decafclaw/agent.py`
- `tests/test_reflection.py`

### Prompt

In `src/decafclaw/config_types.py`, add `max_tool_result_len: int = 2000` to the `ReflectionConfig` dataclass, after `visibility`.

In `src/decafclaw/reflection.py`:
- Remove the module-level `MAX_TOOL_RESULT_LEN = 500` constant.
- Change `build_tool_summary` signature from `(history, turn_start_index)` to `(history, turn_start_index, max_result_len: int = 2000)`.
- Replace the `MAX_TOOL_RESULT_LEN` reference inside the function with the `max_result_len` parameter.

In `src/decafclaw/agent.py` at line ~587, update the `build_tool_summary` call to pass the config value:
```python
tool_summary = build_tool_summary(
    history, turn_start_index,
    max_result_len=config.reflection.max_tool_result_len,
)
```

In `tests/test_reflection.py`:
- Update `test_truncates_long_results` to verify it truncates at the new default (2000, not 500). The existing `"x" * 1000` input is now *shorter* than the limit, so it should NOT be truncated. Change the input to `"x" * 3000` and assert the result is shorter than 3000 and contains `"..."`.
- Add a new test `test_custom_max_result_len` that passes `max_result_len=100` and verifies truncation at that length.

Run `make check && make test` to verify.

---

## Step 2: `build_prior_turn_summary` function + tests

**Goal:** Add the new function and its tests. Not wired into the agent yet.

**Files:**
- `src/decafclaw/reflection.py`
- `tests/test_reflection.py`

### Prompt

In `src/decafclaw/reflection.py`, add a new function `build_prior_turn_summary` after the existing `build_tool_summary`:

```python
def build_prior_turn_summary(
    history: list, turn_start_index: int,
    max_turns: int = 3, max_result_len: int = 200,
) -> str:
```

Logic:
1. If `turn_start_index <= 0`, return `""`.
2. Scan `history[:turn_start_index]` to find turn boundaries. A turn starts at each `role == "user"` message. Collect the start indices of all user messages.
3. Take the last `max_turns` turn start indices.
4. For each of those turns (from the turn's start index up to the next turn's start index, or `turn_start_index` for the last one), extract tool_calls and tool results using the same logic as `build_tool_summary` — tool name + key args for calls, truncated content for results — but using `max_result_len` (default 200, shorter than current-turn since this is background context).
5. If no tool lines were collected, return `""`.
6. Return `"Tools used in prior turns:\n" + "\n".join(tool_lines)`.

Reuse the arg-formatting logic from `build_tool_summary` — extract it into a small helper `_format_tool_args(fn: dict) -> str` that both functions call, to avoid duplication.

In `tests/test_reflection.py`, add a new `TestBuildPriorTurnSummary` class with these tests:

1. `test_first_turn_empty` — `turn_start_index=0` returns `""`.
2. `test_no_tools_in_prior_turns` — prior turns have only user/assistant text messages, returns `""`.
3. `test_extracts_prior_tools` — build a history with 2 prior turns (each with a tool call + result) and a current turn. Call with `turn_start_index` pointing to the current turn's user message. Assert both prior tool names and result snippets appear.
4. `test_respects_max_turns` — build a history with 5 prior turns with tools. Call with `max_turns=2`. Assert only the last 2 turns' tools appear, not the earlier ones.
5. `test_truncates_results` — a prior-turn tool result with 500 chars, called with `max_result_len=100`. Assert the result is truncated and contains `"..."`.

Run `make check && make test`.

---

## Step 3: Prompt update + `evaluate_response` signature

**Goal:** Update the judge prompt template and wire the prior-turn summary into `evaluate_response`.

**Files:**
- `src/decafclaw/prompts/REFLECTION.md`
- `src/decafclaw/reflection.py`
- `tests/test_reflection.py`

### Prompt

Rewrite `src/decafclaw/prompts/REFLECTION.md` to:

```markdown
You are evaluating whether an AI assistant's response adequately addresses
the user's request.

Review the interaction below, then:
1. Identify what the user asked for
2. Assess whether the response addresses it
3. Note any specific gaps or problems

Then output your verdict as JSON:
{{"pass": true/false, "critique": "specific feedback if failed"}}

If the response is adequate, even if imperfect, pass it.
Only fail responses that clearly miss the point, ignore the question,
or contain significant errors.

Important guidelines:
- The assistant accumulates knowledge across turns. Referencing information
  from prior tool calls (listed below) is legitimate, NOT hallucination.
- Only fail if the response CONTRADICTS tool results or makes claims with
  no plausible source in either the current or prior turn tools.
- "Info not in this turn's results but consistent with prior turns" is acceptable.
- "Info that contradicts what tools actually returned" is a failure.

Common failure modes to watch for:
- The assistant deflected ("I don't have access to that") when tools were available
- Tool results were fetched but the response doesn't use or synthesize them
- A multi-part question was only partially answered
- The response contradicts what the tools returned

Do NOT fail a response just because it could be better. Fail only when
the response meaningfully misses what the user asked for.

---

{{retrieved_context}}

{{prior_turn_tools}}

User: {{user_message}}

{{tool_results_summary}}

Assistant response: {{agent_response}}
```

In `src/decafclaw/reflection.py`, update `evaluate_response`:
- Add parameter `prior_turn_summary: str = ""` after `tool_summary`.
- Format the prior-turn block: if `prior_turn_summary` is non-empty, use it as-is; if empty, use `""` (the template variable just becomes blank).
- Add `prior_turn_tools=prior_turn_summary` to the `.format()` call.

In `tests/test_reflection.py`:
- Add `test_prior_turn_summary_in_prompt` to `TestEvaluateResponse`: mock `call_llm`, call `evaluate_response` with a non-empty `prior_turn_summary="Tools used in prior turns:\nTool: wiki_read(...)"`, and assert the mock was called with messages containing that text in the prompt.
- Add `test_empty_prior_turn_summary` to verify that when `prior_turn_summary=""`, the prompt doesn't contain "Tools used in prior turns".

Run `make check && make test`.

---

## Step 4: Wire into agent loop + final integration

**Goal:** Connect everything in `agent.py` so the reflection judge receives prior-turn context.

**Files:**
- `src/decafclaw/agent.py`

### Prompt

In `src/decafclaw/agent.py`, at the reflection block (~line 585):

1. Update the import to include `build_prior_turn_summary`:
   ```python
   from .reflection import build_tool_summary, build_prior_turn_summary, evaluate_response
   ```

2. After building `tool_summary`, build the prior-turn summary:
   ```python
   prior_turn_summary = build_prior_turn_summary(
       history, turn_start_index,
       max_turns=3,
       max_result_len=200,
   )
   ```

3. Pass it to `evaluate_response`:
   ```python
   result = await evaluate_response(
       config, user_message, content, tool_summary,
       prior_turn_summary=prior_turn_summary,
       retrieved_context=retrieved_context_text,
   )
   ```

Run `make check && make test`. Then commit with a message referencing issue #124.
