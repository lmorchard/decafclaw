# Concurrent Tool Display — Plan

## Step 1: message-store.js — key updates on tool_call_id

- `pushMessage` for tool_start: include `tool_call_id` on the message object
- `updateLastToolCall(content)` → `updateToolCall(toolCallId, content)`: find by tool_call_id, not position
- `replaceLastToolCall(msg)` → `replaceToolCall(toolCallId, msg)`: find by tool_call_id, not position

## Step 2: tool-status-store.js — track multiple concurrent tools

- Replace `#toolStatus` (single string) with `#activeTools` (Map of tool_call_id → status string)
- `tool_start`: add entry to map
- `tool_status`: update entry in map
- `tool_end`: remove entry from map
- Expose getter that returns a combined status or null if map is empty
- Update callers in tool-status-store to pass tool_call_id to message-store methods

## Step 3: tool-status-store.js — pass tool_call_id through to message-store

- `tool_start`: include tool_call_id in pushed message
- `tool_status`: call `updateToolCall(toolCallId, ...)` instead of `updateLastToolCall(...)`
- `tool_end`: call `replaceToolCall(toolCallId, ...)` instead of `replaceLastToolCall(...)`

## Step 4: Tests + verify

- Run make check-js (typecheck)
- Manual verification with concurrent tool scenario
