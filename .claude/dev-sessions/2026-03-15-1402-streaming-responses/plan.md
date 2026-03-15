# Streaming LLM Responses — Implementation Plan

## Overview

6 phases, each ending with lint + test + commit. The core streaming function (Phase 2) is the most complex piece — everything else wires it in.

---

## Phase 1: Config and Plumbing

**Goal:** Add streaming config fields. No behavior changes yet.

**Prompt:**

Add streaming configuration to `src/decafclaw/config.py`.

Requirements:
1. Add fields to `Config`:
   - `llm_streaming: bool = True`
   - `llm_show_tool_calls: bool = True`
   - `llm_stream_throttle_ms: int = 500`
2. Wire in `load_config()`:
   - `LLM_STREAMING` env var (default `true`)
   - `LLM_SHOW_TOOL_CALLS` env var (default `true`)
   - `LLM_STREAM_THROTTLE_MS` env var (default `500`)
3. No tests needed — config is simple field additions.

Lint and test after (ensure existing tests still pass).

---

## Phase 2: `call_llm_streaming` Function

**Goal:** The core streaming LLM client. SSE parsing, tool call assembly, callback invocation. Returns the same dict as `call_llm`.

**Prompt:**

Add `call_llm_streaming` to `src/decafclaw/llm.py`.

Requirements:
1. **Function signature:**
   ```python
   async def call_llm_streaming(config, messages, tools=None,
                                 on_chunk=None,
                                 llm_url=None, llm_model=None, llm_api_key=None):
   ```
   Returns the same dict as `call_llm`: `{"content", "tool_calls", "role", "usage"}`

2. **Request:** Same as `call_llm` but add `"stream": True` and `"stream_options": {"include_usage": True}` to the body.

3. **SSE parsing with httpx-sse:**
   ```python
   from httpx_sse import aconnect_sse
   async with httpx.AsyncClient() as client:
       async with aconnect_sse(client, "POST", url, json=body, headers=headers, timeout=120) as event_source:
           async for event in event_source.aiter_sse():
               if event.data == "[DONE]":
                   break
               chunk = json.loads(event.data)
               # process delta...
   ```

4. **Text content accumulation:** Each chunk's `choices[0].delta.content` (if present) is appended to the accumulated content string. Call `on_chunk("text", token_text)` for each.

5. **Tool call assembly:** Tool calls arrive incrementally:
   - `delta.tool_calls[i]` with `index`, `id`, `function.name` → start of a new tool call. Call `on_chunk("tool_call_start", {"index": i, "name": name})`.
   - `delta.tool_calls[i]` with `function.arguments` fragment → append to the in-progress tool call's arguments. Call `on_chunk("tool_call_delta", {"index": i, "arguments_delta": fragment})`.
   - Track in-progress tool calls in a dict: `{index: {"id": ..., "function": {"name": ..., "arguments": "..."}}}`.
   - When the stream ends, finalize all in-progress tool calls. Call `on_chunk("tool_call_end", {"index": i, "name": ..., "arguments": ...})` for each.

6. **Usage:** Check the final chunk for `usage` field (from `stream_options`). Fall back to `None`.

7. **Done callback:** Call `on_chunk("done", {"usage": usage})` when stream completes.

8. **Error handling:** If the stream errors, return whatever was accumulated. Log the error.

9. **on_chunk is optional:** If `None`, skip all callback invocations (just accumulate silently).

10. Create `tests/test_llm_streaming.py` with tests:
    - Mock SSE responses for text-only streaming → verify accumulated content, callback calls
    - Mock SSE with tool calls → verify tool call assembly, callbacks in order
    - Mock SSE with mixed text + tool calls → verify both
    - Mock SSE with usage in final chunk → verify usage returned
    - Mock SSE with no usage → verify None
    - on_chunk=None doesn't error

    For mocking, create a helper that yields fake SSE events. Don't need a real HTTP server — mock the httpx client or use a fake event source.

Lint and test after.

---

## Phase 3: Wire Streaming into Agent Loop

**Goal:** The agent loop uses `call_llm_streaming` when `config.llm_streaming` is true. The `on_chunk` callback comes from the context.

**Prompt:**

Update the agent loop in `src/decafclaw/agent.py` to support streaming.

Requirements:
1. In `run_agent_turn`, replace the LLM call section:
   ```python
   # Before:
   await ctx.publish("llm_start", iteration=iteration + 1)
   response = await call_llm(config, messages, tools=all_tools)
   await ctx.publish("llm_end", iteration=iteration + 1)

   # After:
   await ctx.publish("llm_start", iteration=iteration + 1)
   if config.llm_streaming:
       from .llm import call_llm_streaming
       on_chunk = getattr(ctx, "on_stream_chunk", None)
       response = await call_llm_streaming(config, messages, tools=all_tools, on_chunk=on_chunk)
   else:
       response = await call_llm(config, messages, tools=all_tools)
   await ctx.publish("llm_end", iteration=iteration + 1)
   ```

2. `ctx.on_stream_chunk` is set by the mode (Mattermost or interactive) before calling `run_agent_turn`. It persists across iterations within the turn (the StreamingDisplay for Mattermost is created once per turn).

3. The rest of the agent loop is unchanged — `response` has the same shape regardless of streaming mode.

4. Update usage tracking to handle `None` gracefully (already does with `if usage:`).

5. Tests: verify that with `config.llm_streaming=False`, the old `call_llm` is used. With `True`, `call_llm_streaming` is used. Mock both functions.

Lint and test after.

---

## Phase 4: Mattermost StreamingDisplay

**Goal:** The throttled display manager for Mattermost streaming. Edits the placeholder with accumulated text + tool suffix.

**Prompt:**

Create `StreamingDisplay` and wire it into Mattermost.

Requirements:
1. Add `StreamingDisplay` class (could live in `mattermost.py` or a new `streaming.py` — keep it in `mattermost.py` since it's Mattermost-specific):
   ```python
   class StreamingDisplay:
       def __init__(self, client, post_id, throttle_ms=500, show_tool_calls=True):
           self.client = client
           self.post_id = post_id
           self.throttle_ms = throttle_ms
           self.show_tool_calls = show_tool_calls
           self.buffer = ""
           self.tool_suffix = ""
           self.last_edit_time = 0

       async def on_chunk(self, chunk_type, data):
           """Callback for call_llm_streaming."""
           if chunk_type == "text":
               self.tool_suffix = ""  # clear tool suffix when text resumes
               self.buffer += data
               await self._maybe_edit()
           elif chunk_type == "tool_call_start" and self.show_tool_calls:
               self.tool_suffix = f"\n🔧 Calling {data['name']}..."
               await self._maybe_edit()
           elif chunk_type == "done":
               await self.flush()

       async def _maybe_edit(self):
           """Edit placeholder if throttle interval has passed."""
           now = time.monotonic()
           if (now - self.last_edit_time) * 1000 < self.throttle_ms:
               return
           await self._do_edit()

       async def _do_edit(self):
           """Actually edit the placeholder."""
           self.last_edit_time = time.monotonic()
           text = self.buffer + self.tool_suffix
           if not text:
               return
           try:
               await self.client.edit_message(self.post_id, text)
           except Exception:
               pass  # silently skip failed edits (429, etc.)

       async def flush(self):
           """Force final edit with complete text (no tool suffix)."""
           text = self.buffer
           if text:
               try:
                   await self.client.edit_message(self.post_id, text)
               except Exception:
                   pass
   ```

2. In `_process_conversation` (inside `MattermostClient.run`), before calling `run_agent_turn`:
   - If `config.llm_streaming` and there's a `placeholder_id`:
     - Create a `StreamingDisplay` instance
     - Set `req_ctx.on_stream_chunk = display.on_chunk`
   - Send typing indicator at the start

3. Update the progress subscriber: when `config.llm_streaming` is true, skip the `llm_start` → "💭 Thinking..." edit (streaming display handles the placeholder during LLM calls).

4. After `run_agent_turn` returns, the `StreamingDisplay.flush()` has already been called (from the `done` chunk). The response posting logic checks if streaming was active and skips the redundant `edit_message` (the text is already on the placeholder).

5. Tests:
   - `StreamingDisplay` throttles edits correctly
   - `StreamingDisplay` appends/clears tool suffix
   - `StreamingDisplay.flush` forces final edit

Lint and test after.

---

## Phase 5: Interactive Terminal Streaming

**Goal:** Token-by-token typewriter effect in terminal mode.

**Prompt:**

Add streaming support to `run_interactive` in `agent.py`.

Requirements:
1. Create a terminal streaming callback:
   ```python
   async def terminal_stream_chunk(chunk_type, data):
       if chunk_type == "text":
           print(data, end="", flush=True)
       elif chunk_type == "tool_call_start":
           print(f"\n  [calling {data['name']}...]", flush=True)
       elif chunk_type == "done":
           pass  # newline handled by the print after run_agent_turn
   ```

2. Set `ctx.on_stream_chunk = terminal_stream_chunk` if `config.llm_streaming`.

3. Adjust the response printing: when streaming is active, the text has already been printed token-by-token. The `print(f"\nagent> {output}\n")` should:
   - If streaming was active: just print a newline (text already visible) and handle media
   - If not streaming: print the full response as before

4. Tests: minimal — terminal streaming is best verified manually.

Lint and test after.

---

## Phase 6: Integration, Documentation, and Cleanup

**Goal:** End-to-end verification, docs, backlog cleanup.

**Prompt:**

Final integration and documentation.

Requirements:
1. **Manual verification** (Mattermost):
   - Send a simple question → text streams into placeholder in real-time
   - Send a question requiring a tool → tool suffix appears, then text resumes after tool execution
   - Set `LLM_STREAMING=false` → behavior reverts to all-at-once
   - Set `LLM_SHOW_TOOL_CALLS=false` → tool calls invisible during streaming
   - Verify throttling: placeholder doesn't flicker on fast responses
   - Verify multi-iteration: tool call + response streams smoothly

2. **Manual verification** (interactive):
   - Text streams token-by-token (typewriter effect)
   - Tool calls show `[calling tool...]`
   - `LLM_STREAMING=false` → wait then print all at once

3. **Documentation**:
   - Create `docs/streaming.md` — config, UX, how it works
   - Update `docs/index.md`
   - Update `docs/installation.md` — add streaming config vars
   - Update `CLAUDE.md` — key files if any new modules
   - Remove streaming from `docs/backlog/core.md`

4. Run full test suite, lint. Commit.

---

## Summary of Phases

| Phase | What | Key Files | Tests |
|-------|------|-----------|-------|
| 1 | Config fields | `config.py` | existing pass |
| 2 | `call_llm_streaming` | `llm.py` | ~6 tests |
| 3 | Wire into agent loop | `agent.py` | ~2 tests |
| 4 | Mattermost StreamingDisplay | `mattermost.py` | ~3 tests |
| 5 | Interactive terminal streaming | `agent.py` | manual |
| 6 | Integration + docs | docs | manual |

## Implementation Notes

- **`call_llm` stays untouched.** Zero risk of regressing non-streaming mode.
- **`call_llm_streaming` returns the same dict.** The agent loop doesn't need branching after the LLM call — tool handling, history, archiving all work the same.
- **StreamingDisplay is Mattermost-specific.** Terminal mode uses a simple callback. No shared abstraction needed.
- **The `on_chunk` callback is fire-and-forget from `call_llm_streaming`'s perspective.** If the callback errors, log and continue — don't kill the stream.
- **httpx-sse `aconnect_sse` needs the full URL** (not a relative path), since we're creating our own httpx client, not using Mattermost's.
- **Progress subscriber handoff:** streaming display owns the placeholder during LLM calls. Between LLM calls (tool execution phase), the progress subscriber takes over. The `llm_start` event is the handoff point — when streaming is on, `llm_start` doesn't edit the placeholder because streaming is about to start.
