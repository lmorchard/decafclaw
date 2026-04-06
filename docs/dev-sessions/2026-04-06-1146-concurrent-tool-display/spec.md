# Concurrent Tool Display Bug — Spec

## Problem

When the agent executes multiple tool calls concurrently (e.g., vault_search x2 + tabstack_research), the web UI displays them incorrectly:

1. Progress updates from slow tools (tabstack_research) don't appear
2. When a fast tool finishes, its `tool_end` replaces the wrong message (the slow tool's message)
3. When the slow tool finishes, its result overwrites a fast tool's result
4. The tool status bar tracks only one tool at a time — any `tool_end` clears it

## Root Cause

Three interacting bugs in the web UI JavaScript:

### 1. `message-store.js` — `updateLastToolCall()` and `replaceLastToolCall()` are position-based

Both methods find the "last" `tool_call` message without checking `tool_call_id`. When concurrent tools finish out of order, results get swapped.

### 2. `tool-status-store.js` — single `#toolStatus` string

One string can't represent multiple concurrent tools. Any `tool_end` sets it to `null`, clearing progress for still-running tools.

### 3. No tool_call_id correlation in the UI

The websocket handler already forwards `tool_call_id` on all tool events. The UI ignores it.

## Fix

- Key message updates on `tool_call_id`, not position
- Track multiple concurrent tool statuses
- Only clear status when ALL tools have completed
