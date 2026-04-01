# Self-Reflection / Retry — Plan

## Status: Ready

## Overview

Four phases. Phase 1 adds the config and the core reflection module. Phase 2 integrates it into the agent loop. Phase 3 adds visibility (events for Mattermost/web UI). Phase 4 adds tests and docs. Each phase ends with lint + test passing and a commit.

The reflection feature is a single `call_llm()` to a judge model after the agent produces a final response. If the judge says fail, the critique is injected as a user-role message and the agent loop `continue`s. This means the core implementation is small — most of the work is in the agent loop integration and the UI visibility.

---

## Phase 1: Config + reflection module

**Goal**: Add `ReflectionConfig` to the config system and create `reflection.py` with the judge logic — prompt assembly, LLM call, result parsing. No integration with the agent loop yet.

**Files**: `src/decafclaw/config_types.py`, `src/decafclaw/config.py`, `src/decafclaw/reflection.py`

### Prompt

**Step 1a: Add ReflectionConfig**

Read `src/decafclaw/config_types.py` and add a new `ReflectionConfig` dataclass:

```python
@dataclass
class ReflectionConfig:
    enabled: bool = True
    url: str = ""       # empty = resolve from llm
    model: str = ""     # empty = resolve from llm
    api_key: str = field(default="", metadata={"secret": True})
    max_retries: int = 2
    visibility: str = "hidden"  # hidden | visible | debug

    def resolved(self, config) -> ReflectionConfig:
        """Return copy with empty url/model/api_key filled from config.llm."""
        return replace(self,
            url=self.url or config.llm.url,
            model=self.model or config.llm.model,
            api_key=self.api_key or config.llm.api_key,
        )
```

Add `reflection: ReflectionConfig` to the top-level `Config` class in `config.py`. Wire it into `load_config()` with env prefix `"REFLECTION"`. Add it to the config CLI's show/get/set support (it's a dataclass sub-config, so it should work automatically).

Lint and test after.

**Step 1b: Create reflection.py**

Create `src/decafclaw/reflection.py` with:

1. **Default judge prompt** — hardcoded string constant with `{user_message}`, `{tool_results_summary}`, `{agent_response}` placeholders.

2. **`load_reflection_prompt(config)`** — returns the prompt template. Checks for `data/{agent_id}/REFLECTION.md` override file; if not found, returns the default.

3. **`build_tool_summary(history, turn_start_index)`** — extracts tool call/result pairs from history starting at `turn_start_index` (where the user message was appended). For each tool call, formats as:
   ```
   Tool: {name}({key_args})
   Result: {truncated_result}
   ```
   Truncates results to 500 chars. Returns empty string if no tools were used.

4. **`async def evaluate_response(config, user_message, agent_response, tool_summary)`** — the main judge function:
   - Loads the prompt template
   - Fills in the template variables
   - Calls `call_llm()` with the reflection model (via `config.reflection.resolved(config)`)
   - Parses the JSON verdict from the response
   - Returns a `ReflectionResult` dataclass:
     ```python
     @dataclass
     class ReflectionResult:
         passed: bool
         critique: str = ""
         raw_response: str = ""  # full judge output for debug mode
         error: str = ""         # if the judge call failed
     ```
   - On any error (network, parse, etc.), returns `ReflectionResult(passed=True, error="...")` — fail-open.

5. **JSON parsing** — the judge response may have the JSON embedded in reasoning text. Extract the JSON object containing `"pass"` and `"critique"` keys. Try `json.loads()` on the full response first; if that fails, regex search for `{...}` patterns. If all parsing fails, treat as pass.

Lint after. No integration yet — this is a standalone module.

---

## Phase 2: Agent loop integration

**Goal**: Wire reflection into `run_agent_turn()`. After the agent produces a final response, call the judge. If it fails, inject critique and continue the loop.

**Files**: `src/decafclaw/agent.py`, `src/decafclaw/context.py`, `src/decafclaw/tools/delegate.py`

### Prompt

Read the spec at `.claude/dev-sessions/2026-03-19-1549-self-reflection/spec.md` and `src/decafclaw/agent.py`.

**Step 2a: Add `is_child` flag to Context**

In `src/decafclaw/context.py`, add `self.is_child: bool = False` to `__init__`.

In `src/decafclaw/tools/delegate.py`, set `child_ctx.is_child = True` after forking the child context.

**Step 2b: Integrate reflection into the agent loop**

In `run_agent_turn()`, modify the "No tool calls — final response" path (currently lines ~456-479). The current flow is:

```python
# No tool calls — final response
content = response.get("content") or ""
# ... empty retry logic ...
final_msg = {"role": "assistant", "content": content}
history.append(final_msg)
_archive(ctx, final_msg)
# ... compaction, media extraction, return ...
```

Change to:

```python
# No tool calls — final response
content = response.get("content") or ""
# ... empty retry logic ...

# Reflection check
if _should_reflect(ctx, config, content, reflection_retries):
    from .reflection import evaluate_response, build_tool_summary
    tool_summary = build_tool_summary(history, turn_start_index)
    result = await evaluate_response(config, user_message, content, tool_summary)

    # Publish reflection event (for UI visibility)
    await ctx.publish("reflection_result",
        passed=result.passed,
        critique=result.critique,
        raw_response=result.raw_response,
        retry_number=reflection_retries + 1,
        error=result.error)

    if not result.passed and not result.error:
        # Add the failed response to history
        failed_msg = {"role": "assistant", "content": content}
        history.append(failed_msg)
        messages.append(failed_msg)
        _archive(ctx, failed_msg)

        # Add critique as user message
        critique_msg = {
            "role": "user",
            "content": (
                f"[reflection] Your previous response may not fully address "
                f"the user's request.\nFeedback: {result.critique}\n"
                f"Please try again, addressing the feedback above."
            ),
        }
        history.append(critique_msg)
        messages.append(critique_msg)
        _archive(ctx, critique_msg)

        reflection_retries += 1
        continue  # back to LLM call

# Normal final response path (unchanged from here)
final_msg = {"role": "assistant", "content": content}
history.append(final_msg)
_archive(ctx, final_msg)
# ... etc ...
```

Add these variables at the top of the try block (before the iteration loop):
- `reflection_retries = 0`
- `turn_start_index = len(history)` (before the user message is appended — actually, capture it right after appending the user message: `turn_start_index = len(history) - 1`)

**`_should_reflect()` helper:**

```python
def _should_reflect(ctx, config, content, reflection_retries) -> bool:
    """Check whether reflection should run on this response."""
    if not config.reflection.enabled:
        return False
    if reflection_retries >= config.reflection.max_retries:
        return False
    if ctx.is_child:
        return False
    if not content or not content.strip():
        return False
    if getattr(ctx, 'cancelled', None) and ctx.cancelled.is_set():
        return False
    return True
```

Note: the "max iterations hit" skip condition is handled naturally — if we've exhausted the iteration loop, we never reach the reflection check (we exit the for loop).

Lint and test after. Existing tests should still pass since reflection is on by default but the judge just calls the LLM — in tests, the LLM is mocked.

---

## Phase 3: UI visibility

**Goal**: Handle `reflection_result` events in Mattermost and web UI based on visibility mode.

**Files**: `src/decafclaw/mattermost.py`, `src/decafclaw/web/websocket.py`

### Prompt

Read `src/decafclaw/mattermost.py` — look at how `_subscribe_progress` and `ConversationDisplay` handle events like `tool_start`/`tool_end`.

Read `src/decafclaw/web/websocket.py` — look at `on_turn_event()`.

**Step 3a: Mattermost visibility**

In `_subscribe_progress` / the event handler, add handling for the `reflection_result` event:

```python
elif event_name == "reflection_result":
    visibility = config.reflection.visibility
    if visibility == "hidden":
        pass  # suppress
    elif visibility == "visible":
        passed = data.get("passed", True)
        if not passed:
            critique = data.get("critique", "")
            retry_num = data.get("retry_number", 0)
            display.on_reflection(
                passed=False,
                critique=critique,
                retry_number=retry_num,
            )
    elif visibility == "debug":
        display.on_reflection_debug(
            passed=data.get("passed", True),
            critique=data.get("critique", ""),
            raw_response=data.get("raw_response", ""),
            retry_number=data.get("retry_number", 0),
            error=data.get("error", ""),
        )
```

Add `on_reflection()` and `on_reflection_debug()` methods to `ConversationDisplay`. These should update the current message with a collapsed attachment (Mattermost supports this via `props.attachments`):

For `visible` mode:
```
[Reflection retry 1/2] Response may not fully address the question.
Feedback: {critique}
```

For `debug` mode, include the full raw judge response and error info.

**Step 3b: Web UI visibility**

In `on_turn_event()` in websocket.py, add handling for `reflection_result`:

```python
elif event_name == "reflection_result":
    visibility = config.reflection.visibility
    if visibility != "hidden":
        await ws_send({
            "type": "reflection_result",
            "conv_id": conv_id,
            "passed": data.get("passed", True),
            "critique": data.get("critique", ""),
            "retry_number": data.get("retry_number", 0),
            "raw_response": data.get("raw_response", "") if visibility == "debug" else "",
            "error": data.get("error", ""),
        })
```

The web frontend can render this however it likes — we just send the data.

Lint and test after.

---

## Phase 4: Tests and docs

**Goal**: Add tests for the reflection module and agent loop integration. Update docs.

**Files**: `tests/test_reflection.py`, `tests/test_agent_turn.py`, `CLAUDE.md`, `docs/`

### Prompt

**Step 4a: Unit tests for reflection.py**

Create `tests/test_reflection.py`:

- `test_build_tool_summary_no_tools` — empty history returns empty string
- `test_build_tool_summary_with_tools` — formats tool calls correctly
- `test_build_tool_summary_truncates` — long results are truncated
- `test_evaluate_response_pass` — mock LLM returns pass verdict, returns ReflectionResult(passed=True)
- `test_evaluate_response_fail` — mock LLM returns fail verdict with critique
- `test_evaluate_response_unparseable` — mock LLM returns garbage, treated as pass
- `test_evaluate_response_network_error` — mock LLM raises, treated as pass with error
- `test_evaluate_response_json_in_reasoning` — JSON embedded in reasoning text is extracted
- `test_load_reflection_prompt_default` — no override file, returns default
- `test_load_reflection_prompt_override` — file exists, returns its content

**Step 4b: Integration tests in test_agent_turn.py**

Add to existing test file:

- `test_reflection_pass_delivers_response` — mock judge returns pass, response delivered normally
- `test_reflection_fail_retries` — mock judge returns fail then pass, verify critique injected and second response delivered
- `test_reflection_max_retries_delivers_last` — mock judge always fails, verify last response delivered after max retries
- `test_reflection_disabled_skips` — set `config.reflection.enabled = False`, verify no judge call
- `test_reflection_child_skips` — set `ctx.is_child = True`, verify no judge call
- `test_reflection_error_delivers_response` — mock judge errors, response delivered as-is
- `test_reflection_within_iteration_budget` — verify reflection retries consume iterations from `max_tool_iterations`

For all tests, mock `call_llm` to return canned responses. The reflection judge is just another `call_llm` call with different params — intercept based on the model parameter.

**Step 4c: Docs**

- Create `docs/reflection.md` — feature documentation covering config, prompt customization, visibility modes, how it works
- Update `docs/index.md` — add reflection page
- Update `CLAUDE.md` — add `reflection.py` to key files, add convention note about reflection
- Update `docs/config.md` — add reflection group to config reference

Lint and test after. Final commit.

---

## Dependency Graph

```
Phase 1 (config + reflection.py module)
  ↓
Phase 2 (agent loop integration)
  ↓
Phase 3 (UI visibility — Mattermost + web)
  ↓
Phase 4 (tests + docs)
```

Phases are strictly sequential. Phase 1 is standalone code. Phase 2 depends on Phase 1. Phase 3 depends on Phase 2 (needs the event). Phase 4 depends on all.

## Risk Notes

- **Judge false positives** (~10%) — the retry budget limits damage but could still cause 1-2 unnecessary retries per turn. The default prompt needs careful tuning to bias toward passing. "If adequate, even if imperfect, pass it" is the key instruction.
- **Latency** — adds one LLM call per turn (or more on retries). With a fast model like Flash, this should be <1s. But it's blocking — the user waits. Consider logging timing.
- **History pollution** — failed responses + critiques stay in history. Over a long conversation, this could accumulate noise. Compaction should handle this naturally (the compaction summarizer will condense old reflection exchanges).
- **Prompt template override** — if `REFLECTION.md` has bugs (missing placeholders, bad instructions), the judge will behave unpredictably. The fail-open design (treat parse errors as pass) mitigates this.
- **Testing complexity** — the agent loop tests need to mock two different LLM calls (main model + judge). Pattern: intercept `call_llm` and dispatch based on the model parameter or call sequence.
