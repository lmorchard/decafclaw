# Streaming LLM Responses — Spec

## Goal

Stream LLM response tokens to the user as they arrive instead of waiting for the complete response. Configurable switch between streaming and all-at-once modes.

## Design Principles

- **Two functions**: `call_llm` (existing, unchanged) and `call_llm_streaming` (new). Agent loop picks based on config.
- **Callback-based streaming**: `call_llm_streaming` accepts an `on_chunk` callback for display, returns the same dict as `call_llm` when complete.
- **Non-destructive**: streaming off = exact current behavior. No regressions.
- **Tool call visibility**: separate config for showing tool call details during streaming.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_STREAMING` | `true` | Enable token streaming. `false` = current all-at-once behavior. |
| `LLM_SHOW_TOOL_CALLS` | `true` | Show tool call details during streaming (name + args). |
| `LLM_STREAM_THROTTLE_MS` | `500` | Minimum interval between Mattermost placeholder edits (ms). |

## Streaming Function

```python
async def call_llm_streaming(config, messages, tools=None,
                              on_chunk=None,
                              llm_url=None, llm_model=None, llm_api_key=None):
    """Call the LLM with streaming, invoking on_chunk for each token.

    Args:
        on_chunk: async callback(chunk_type, data) called for each chunk
            chunk_type: "text" | "tool_call_start" | "tool_call_delta" | "tool_call_end" | "done"
            data: varies by type (text string, tool call info, usage dict)

    Returns: same dict as call_llm (content, tool_calls, usage)
    """
```

### Chunk Types

| Type | Data | When |
|------|------|------|
| `text` | `str` (token text) | Text content token received |
| `tool_call_start` | `{"index": int, "name": str}` | Tool call begins |
| `tool_call_delta` | `{"index": int, "arguments_delta": str}` | Tool call arguments chunk |
| `tool_call_end` | `{"index": int, "name": str, "arguments": str}` | Tool call complete (all args received) |
| `done` | `{"usage": dict or None}` | Stream finished |

### SSE Parsing

Use `httpx-sse` (already installed via MCP SDK) for SSE event parsing via `aconnect_sse`:

```python
async with httpx.AsyncClient() as client:
    async with aconnect_sse(client, "POST", url, json=body, headers=headers) as event_source:
        async for event in event_source.aiter_sse():
            if event.data == "[DONE]":
                break
            data = json.loads(event.data)
            # process delta...
```

Request body includes `"stream": true` and `"stream_options": {"include_usage": true}` for token counts in the final chunk.

### Tool Call Assembly

Tool calls arrive as incremental deltas:
- First chunk for a tool call has `index`, `id`, `function.name`
- Subsequent chunks append to `function.arguments`
- We assemble the complete tool call and emit `tool_call_end` when the next tool call starts or the stream ends

### Token Usage

Request `stream_options: {"include_usage": true}`. If the backend supports it, usage appears in the final chunk. If not, return `None` for usage (compaction gracefully handles None).

## Agent Loop Changes

In `run_agent_turn`, before calling the LLM:

```python
if config.llm_streaming:
    response = await call_llm_streaming(config, messages, tools, on_chunk=chunk_handler)
else:
    response = await call_llm(config, messages, tools)
```

The `chunk_handler` is provided by the caller (Mattermost or interactive mode) via the context.

### Chunk Handler

The agent loop creates the appropriate `on_chunk` callback locally based on the mode — no need for a `ctx` attribute. For Mattermost, the callback wraps a `StreamingDisplay` instance. For interactive, it wraps print statements. The callback is passed directly to `call_llm_streaming`.

### Multi-Iteration Streaming

The agent loop may call the LLM multiple times per turn (tool call → execute → LLM again). The `StreamingDisplay` persists across iterations within a turn:

1. First LLM call streams text → display shows accumulated text
2. LLM returns tool calls → tool suffix shown, then tool execution phase (progress subscriber takes over placeholder briefly)
3. Second LLM call streams more text → display resumes, text continues accumulating from where it left off
4. Final response → display flushes

The display object is created once per turn, not per LLM call.

## Mattermost Streaming UX

### Text Streaming

- Tokens accumulate in a buffer
- Every `LLM_STREAM_THROTTLE_MS` (default 500ms), edit the placeholder with the accumulated text
- Send typing indicator at the start of streaming
- Final edit when stream completes (ensure complete text is shown)

### Tool Call Display

When `LLM_SHOW_TOOL_CALLS=true`:
- On `tool_call_start`: append `\n🔧 Calling {tool_name}...` as a suffix to the current accumulated text
- On `tool_call_end`: the suffix stays until text streaming resumes
- When new text tokens arrive after a tool execution: strip the tool suffix, continue accumulating text
- This prevents flickering — text only grows, tool status is a temporary footer

When `LLM_SHOW_TOOL_CALLS=false`:
- Tool calls are invisible in the placeholder
- Existing progress subscriber (`tool_start`/`tool_end` events) still shows "🔧 Running tool..." via the existing mechanism

### Throttled Edits

```python
class StreamingDisplay:
    """Manages throttled placeholder edits for streaming."""

    def __init__(self, client, post_id, throttle_ms=500):
        self.buffer = ""
        self.tool_suffix = ""
        self.last_edit = 0

    async def on_text(self, text):
        self.tool_suffix = ""  # clear tool suffix when text resumes
        self.buffer += text
        await self._maybe_edit()

    async def on_tool_start(self, name):
        self.tool_suffix = f"\n🔧 Calling {name}..."
        await self._maybe_edit()

    async def flush(self):
        """Force final edit."""
```

## Interactive Terminal Streaming

- `text` chunks: `print(text, end="", flush=True)` — immediate typewriter effect
- `tool_call_start`: print `\n  [calling {name}...]` on a new line
- `tool_call_end`: no output (tool execution will print its own status via progress events)
- `done`: print newline

No throttling needed in terminal mode.

## Interaction with Existing Progress Subscriber

The Mattermost progress subscriber currently edits the placeholder for `tool_start`, `tool_end`, `llm_start` events. With streaming:

- **`llm_start`**: skip the "💭 Thinking..." edit when `config.llm_streaming` is true. The streaming display takes over the placeholder during LLM calls. Check `config.llm_streaming` directly in the progress subscriber.
- **`tool_start`/`tool_end`**: these fire AFTER the LLM response (during tool execution). During tool execution, the streaming display is paused — the progress subscriber can safely edit the placeholder for tool status. When the next LLM call starts streaming, the display resumes control.
- **Handoff pattern**: the streaming display "owns" the placeholder during LLM calls. The progress subscriber "owns" it during tool execution. They don't overlap because tool execution happens between LLM calls, never concurrently.

## Error Handling

- If the SSE stream errors mid-response, return whatever was accumulated (partial text, partial tool calls) with an error indicator
- If `httpx-sse` is not available (shouldn't happen, but), fall back to `call_llm`
- Timeout: use the same 120s timeout as `call_llm`
- **Mattermost 429 rate limiting**: if an edit returns 429 or fails, skip it silently and try again on the next throttle cycle. The final flush always attempts to write the complete text.

## Testing

1. **`call_llm_streaming`** — mock SSE responses, verify callback invocations, verify assembled result matches expected
2. **Tool call assembly** — incremental deltas assembled correctly, multiple tool calls
3. **StreamingDisplay** — throttled edits, tool suffix append/clear, flush
4. **Config switch** — streaming=true uses streaming function, streaming=false uses original
5. **Terminal streaming** — typewriter output with tool call interleaving

## Out of Scope

- Streaming for compaction LLM calls (they use `call_llm` directly)
- Streaming for eval runner
- Partial response display for tool results (tool results appear all at once)
- Streaming in heartbeat turns
