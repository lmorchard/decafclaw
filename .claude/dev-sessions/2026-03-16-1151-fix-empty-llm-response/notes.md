# Fix Empty LLM Response Bug — Notes

## Implementation Summary

All three fixes implemented, tested, and committed separately.

### Step 1: Strip `__thought__` from tool_call_ids (commit 60d3eec)
- Added `_sanitize_tool_call_id()` helper to `llm.py`
- Applied in both `call_llm()` and `call_llm_streaming()`
- 4 new tests in `test_llm_streaming.py`

### Step 2: Re-raise streaming errors (commit 76416b0)
- Added `_stream_error` tracking in `call_llm_streaming()`
- Re-raises if nothing was accumulated; returns partial content on mid-stream errors
- 2 new tests: one for full error propagation, one for partial content preservation

### Step 3: Delete placeholder on empty response (commit 127bfda)
- Added `elif placeholder_id:` fallback in `_post_response()`
- Deletes the placeholder instead of leaving "Thinking..." stuck
- 1-line change, pure safety net

### Verification
- `make check` passes (0 errors, 3 pre-existing pyright warnings in mcp_client.py)
- `make test` passes (344 tests, +6 new)
- All changes on `main`, not pushed yet

## Key Insight
The root cause was LiteLLM embedding Gemini thinking tokens in `tool_call_id` fields (~3KB each), which bloated conversation history and eventually caused API errors. The error was silently swallowed, producing an empty response that left the placeholder stuck. Three independent fixes at different layers ensure this failure mode is addressed.
