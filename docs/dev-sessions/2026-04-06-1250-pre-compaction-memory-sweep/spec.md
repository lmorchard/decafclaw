# Pre-compaction Memory Sweep — Spec

## Summary

Before compaction summarizes conversation history, fire off a background agent turn that reviews the about-to-be-compacted messages and saves noteworthy insights to the vault. This prevents knowledge loss during summarization.

## Behavior

1. When `compact_history()` is triggered, snapshot the messages that will be compacted (the old history)
2. Fire off the sweep as a background `asyncio.Task` — do NOT block compaction
3. Compaction proceeds immediately as normal
4. The sweep runs as an isolated child agent turn:
   - Gets the history snapshot as context in its prompt
   - Has access to vault tools only (vault_write, vault_journal_append, vault_search)
   - Vault tools are preapproved (no confirmation prompts)
   - `is_child=True`, `skip_reflection=True`, `skip_vault_retrieval=True`
   - Does NOT write to the main conversation's archive
5. System prompt for the sweep is loaded from `data/{agent_id}/MEMORY_SWEEP.md` with a bundled default fallback

## Configuration

- `compaction.memory_sweep_enabled` (bool, default `true`) — toggle the feature
- Sweep prompt file: `data/{agent_id}/MEMORY_SWEEP.md`, fallback to bundled default

## Error handling

- Fail-open: if the sweep errors, log a warning and discard — compaction is unaffected
- No timeout needed since it runs in the background
- Log start/completion/error for observability

## Non-goals

- The sweep does not affect compaction timing or behavior
- The sweep does not write to the conversation archive
- No UI indication of the sweep (it's invisible to the user)
