# Conversation Resume and Graceful Shutdown

## Overview

When the agent restarts (e.g., `make dev` file watcher restart), resume
existing conversations by replaying their JSONL archive into the in-memory
history. If the replayed history is too long, compact it immediately.

## How It Works

In `mattermost.py`, when a conversation's history is first accessed
(currently `if conv_id not in histories`), check for an existing archive:

1. Call `read_archive(config, conv_id)`
2. If non-empty, use it as the conversation history
3. Check if compaction is needed (token budget can't be checked without
   an LLM call, so use message count or character estimate as proxy)
4. If history is very large, trigger compaction before the first agent turn

For thread forks: if the thread has its own archive, replay that. If not,
fork from the channel as before.

For interactive mode: same logic — check for `interactive` conv_id archive
on startup.

## Scope

- Replay archive into history on first access per conv_id
- Works for both Mattermost and interactive mode
- Compact if replayed history is large
- Log when resuming a conversation

## Graceful Shutdown

Handle SIGTERM/SIGINT cleanly instead of dropping everything.

### Behavior

1. On signal, set a shutdown flag
2. Stop accepting new messages (ignore incoming websocket events)
3. Wait for any in-flight agent turns to complete (they're already
   archiving messages, so no data loss)
4. Close the websocket connection cleanly
5. Exit

### Implementation

- Register signal handlers in `_run_mattermost` or `main()`
- Use an `asyncio.Event` as the shutdown flag
- The websocket listen loop checks the flag and breaks
- In-flight `run_agent_turn` calls complete normally (they're awaited)
- Log "Shutting down gracefully..." on signal

### Interactive mode

- Already handles KeyboardInterrupt in the input loop
- Just ensure cleanup (unsubscribe) happens in the finally block (already does)

## Scope

- Replay archive into history on first access per conv_id
- Works for both Mattermost and interactive mode
- Compact if replayed history is large
- Log when resuming a conversation
- Graceful shutdown: finish in-flight turns, close websocket cleanly

## Out of scope

- Cleaning up old archive files
- Pruning archives by age
- UI indication in Mattermost that a conversation was resumed
