# Fix Empty LLM Response Bug — Plan

Three independent fixes that together eliminate the "stuck on Thinking..." bug. Each step is self-contained: implement, test, lint, commit.

---

## Step 1: Strip `__thought__` data from tool_call_ids

**Goal:** Prevent LiteLLM/Gemini thinking tokens from bloating conversation history.

**Files to change:**
- `src/decafclaw/llm.py` — add a `_sanitize_tool_call_id()` helper, apply it in both `call_llm` and `call_llm_streaming` when building tool_calls
- `tests/test_llm_streaming.py` — add tests for the sanitization

**Details:**
- Add a module-level helper `_sanitize_tool_call_id(tc_id: str) -> str` that strips everything from `__thought__` onward: `tc_id.split("__thought__")[0]`
- In `call_llm()`: after extracting `message.get("tool_calls")`, sanitize each tool call's `id` field before returning
- In `call_llm_streaming()`: sanitize the `id` when a new tool call is first created in `tool_calls_in_progress` (line ~149)
- This ensures all downstream consumers (agent loop, archive, compaction) only ever see clean IDs

**Tests to add:**
- `test_sanitize_tool_call_id_strips_thought` — verifies the helper strips `__thought__` suffix
- `test_sanitize_tool_call_id_preserves_normal` — verifies normal IDs pass through unchanged
- `test_streaming_tool_call_id_sanitized` — end-to-end test that a streaming response with a `__thought__` ID gets cleaned

### Prompt

```
In `src/decafclaw/llm.py`, add a helper function `_sanitize_tool_call_id(tc_id: str) -> str` that strips LiteLLM's embedded thinking data from tool call IDs. The pattern is: the real ID is everything before `__thought__`, and the thinking data (base64) comes after. If `__thought__` is not present, return the ID unchanged.

Apply this sanitization in two places:
1. In `call_llm()`, after extracting tool_calls from the response message, sanitize each tool call's "id" field before returning.
2. In `call_llm_streaming()`, sanitize the "id" when creating a new entry in `tool_calls_in_progress` (around line 149 where `tc_delta.get("id", ...)` is used).

Then add tests in `tests/test_llm_streaming.py`:
- `test_sanitize_tool_call_id_strips_thought`: call the helper with `"call_abc123__thought__CiUBjz1rX..."` and verify it returns `"call_abc123"`.
- `test_sanitize_tool_call_id_preserves_normal`: call with `"call_abc123"` and verify it passes through unchanged.
- `test_streaming_tool_call_id_sanitized`: create SSE events with a tool call that has a `__thought__`-bloated ID, run `call_llm_streaming`, and verify the returned tool_calls have the clean ID.

Run `make check && make test` after.
```

---

## Step 2: Re-raise streaming errors when nothing was accumulated

**Goal:** Don't silently swallow LLM connection/streaming errors as empty responses.

**Files to change:**
- `src/decafclaw/llm.py` — modify the `except` block in `call_llm_streaming` to track the error and re-raise if nothing was accumulated
- `tests/test_llm_streaming.py` — add tests for error handling

**Details:**
- Add a variable `_stream_error = None` before the try block
- In the `except Exception` block (line 175), store the error: `_stream_error = e` and keep the existing log.error
- After the try/except, before the finalize section: if `_stream_error` and no content was accumulated and no tool calls in progress, re-raise the original error
- If partial content WAS accumulated before the error, keep current behavior (return what we have) — this handles mid-stream disconnects gracefully

**Tests to add:**
- `test_streaming_error_raises_when_nothing_accumulated`: mock `aconnect_sse` to raise immediately, verify the exception propagates
- `test_streaming_error_returns_partial_on_partial_content`: mock events that emit some text then error, verify partial content is returned (not raised)

### Prompt

```
In `src/decafclaw/llm.py`, modify the error handling in `call_llm_streaming` so that streaming errors are not silently swallowed when nothing was accumulated.

Current code (around line 175):
```python
except Exception as e:
    log.error(f"LLM streaming error: {e}")
```

Change to:
1. Add `_stream_error = None` before the `try` block (around line 109).
2. In the `except` block, store the error: `_stream_error = e` (keep the existing log.error line).
3. After the try/except but before the "Finalize tool calls" section (around line 178), add a check: if `_stream_error` is set AND `content_parts` is empty AND `tool_calls_in_progress` is empty, re-raise the stored error. This lets the caller (agent loop) know the LLM call actually failed.
4. If there IS partial content or partial tool calls, keep current behavior — return what was accumulated. This handles mid-stream disconnects gracefully.

Then add tests in `tests/test_llm_streaming.py`:
- `test_streaming_error_raises_when_nothing_accumulated`: make `aconnect_sse` raise `httpx.ConnectError("connection refused")` immediately. Verify `call_llm_streaming` raises the error instead of returning empty.
- `test_streaming_error_returns_partial_on_partial_content`: create events that emit a text chunk, then make the event source raise an error mid-stream. Verify the function returns the partial content instead of raising.

Run `make check && make test` after.
```

---

## Step 3: Handle empty responses in `_post_response`

**Goal:** Safety net — if the agent returns an empty response for any reason, update or delete the placeholder so it never stays stuck on "Thinking...".

**Files to change:**
- `src/decafclaw/mattermost.py` — add a fallback at the end of `_post_response`
- `tests/test_agent_turn.py` or a new test — verify the behavior

**Details:**
- At the end of `_post_response`, after all existing conditions, add an `else` clause: if `placeholder_id` is set and we reach this point (no text, no media, nothing streamed), delete the placeholder message
- Deleting is better than showing a fallback message — an empty response from the LLM typically means something went wrong, and the error will have been logged. A deleted placeholder is less confusing than a permanent "[no response]"
- The agent loop's error handler in `_process_conversation` already produces `[error: ...]` messages for exceptions, so those will be posted correctly

**Tests:**
- This is primarily a Mattermost integration concern. Add a unit test that mocks `delete_message` and verifies it's called when response_text is empty and placeholder_id is set.

### Prompt

```
In `src/decafclaw/mattermost.py`, fix `_post_response` (around line 357) so that empty responses don't leave the placeholder stuck on "Thinking...".

Current code ends with:
```python
elif placeholder_id and response_text:
    await self.edit_message(placeholder_id, response_text)
elif response_text:
    await self.send(channel_id, response_text, root_id=root_id)
```

Add a final fallback after the last `elif`: if `placeholder_id` is set and we reached this point (meaning response_text is empty, no media, nothing was streamed), delete the placeholder:
```python
elif placeholder_id:
    # Empty response — remove placeholder rather than leaving "Thinking..." stuck
    await self.delete_message(placeholder_id)
```

This is a safety net. The real fixes (steps 1-2) prevent this situation, but if it ever happens again, the placeholder won't be stuck.

Run `make check && make test` after.
```

---

## Step 4: Verify all changes together, lint, test

**Goal:** Final integration check.

### Prompt

```
Run `make check && make test` to verify all three fixes work together. Review any warnings or failures and fix them. Then verify the changes look correct with `git diff`.
```

---

## Execution Order

Steps 1-3 are independent and can be done in any order. Step 4 is the final verification. Each step should be committed separately so the git history is clear.
