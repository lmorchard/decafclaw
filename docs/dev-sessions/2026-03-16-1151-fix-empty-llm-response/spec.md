# Fix Empty LLM Response Bug — Spec

## Problem

The bot gets stuck showing "Thinking..." and never produces a final response after tool-heavy conversations. This has been observed repeatedly as conversations grow.

## Root Causes

### 1. LiteLLM embeds Gemini thinking tokens in tool_call_ids
When using Gemini models with thinking mode via LiteLLM, thinking/reasoning data is packed into `tool_call_id` fields as base64 after a `__thought__` delimiter. These IDs grow to ~3KB+ each. Over a long conversation with many tool calls, this bloats the context significantly and can cause API errors when the history is sent back.

### 2. `call_llm_streaming` silently swallows errors
In `llm.py:175-176`, if the streaming connection fails (timeout, oversized request, malformed data), the exception is caught and logged but execution continues. The function returns `{"content": None, "tool_calls": None}` — which looks like a valid empty response to the caller.

### 3. `_post_response` doesn't handle empty responses
In `mattermost.py:357-388`, when `response_text` is empty, no condition matches to update or delete the placeholder. It stays showing "Thinking..." forever.

## Evidence

From the conversation archive `pcni34ecpfg6ipa1tfkpnta99r.jsonl`:
- Tool result at `18:38:48` with a `__thought__`-bloated tool_call_id (~3KB)
- Empty assistant response at `18:38:49` — only 1 second later (too fast for a real LLM response)
- This indicates the LLM call failed immediately and the error was swallowed

## Fixes Needed

### Fix A: Strip `__thought__` data from tool_call_ids
- Before storing tool call messages in history, strip the `__thought__` suffix from tool_call_ids
- The actual ID is just the part before `__thought__` (e.g., `call_2872f35826ea4a6b83f7ea17130e`)
- This prevents context bloat and avoids confusing the LLM on subsequent calls

### Fix B: Don't silently swallow streaming errors
- In `call_llm_streaming`, if nothing was accumulated (no content, no tool calls) and an exception occurred, re-raise it
- This lets the agent loop's error handler in `_process_conversation` catch it and produce a visible error message

### Fix C: Handle empty responses in `_post_response`
- When `response_text` is empty and there's a placeholder, edit it with a fallback message or delete it
- This is a safety net so placeholders never get stuck even if other bugs exist
