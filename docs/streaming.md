# Streaming LLM Responses

DecafClaw can stream LLM response tokens to the user as they arrive, instead of waiting for the complete response. This makes the agent feel much more responsive, especially for longer answers.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_STREAMING` | `true` | Enable streaming. `false` reverts to all-at-once. |
| `LLM_SHOW_TOOL_CALLS` | `true` | Show tool call names during streaming. |
| `LLM_STREAM_THROTTLE_MS` | `200` | Min interval between Mattermost edits (ms). |

## How it works

### Streaming mode (`LLM_STREAMING=true`)

1. The LLM call uses Server-Sent Events (SSE) to receive tokens incrementally
2. Each token is passed to a display callback as it arrives
3. In **Mattermost**: the placeholder message is edited with accumulated text on a throttle timer
4. In **terminal**: tokens print immediately (typewriter effect)
5. When complete, the final assembled response is used for history/archiving as usual

### Non-streaming mode (`LLM_STREAMING=false`)

The agent waits for the complete LLM response before displaying anything. This is the original behavior — useful for debugging or if streaming causes issues with your LLM endpoint.

## Mattermost UX

### Text streaming

The placeholder message updates every 500ms (configurable) with the text received so far. A typing indicator is shown while streaming is active.

### Tool call visibility

When the LLM decides to call a tool during streaming, a suffix is appended to the current text:

```
Here's what I found about cats: they sleep 16 hours a day and
🔧 Calling memory_search...
```

When text resumes after tool execution, the tool suffix is replaced with the new text. The text only grows — no flickering.

Set `LLM_SHOW_TOOL_CALLS=false` to hide tool call names from the streamed output. The existing tool progress events (🔧 Running tool...) still appear during tool execution regardless of this setting.

### Multi-iteration

When the agent calls tools and then gets more text from the LLM, the streaming display persists across iterations. Text accumulates across the entire turn.

## Terminal UX

Tokens print immediately with `flush=True` for a typewriter effect:

```
you> What do cats eat?
Cats are obligate carnivores, meaning they require...
  [calling memory_search...]
Based on your notes, your cat Sassy prefers wet food.
```

## Technical details

- **SSE parsing**: uses `httpx-sse` (already a dependency via MCP SDK)
- **Token usage**: requests `stream_options: {"include_usage": true}` for usage stats in the final chunk. Falls back to `None` if the backend doesn't support it.
- **Error handling**: if the stream errors mid-response, the accumulated text is returned as a partial response.
- **Callback errors**: if the display callback throws an exception, it's logged and the stream continues.
- **Throttle**: Mattermost edits are throttled to avoid rate limiting (429). Failed edits are silently skipped. The final flush always attempts to write the complete text.
