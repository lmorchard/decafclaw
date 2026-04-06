# Pre-compaction Memory Sweep — Plan

## Step 1: Add config field and bundled prompt

- Add `memory_sweep_enabled: bool = True` to `CompactionConfig` in `config_types.py`
- Create bundled default prompt at `src/decafclaw/prompts/MEMORY_SWEEP.md`
- Add prompt loading helper in `compaction.py` (check agent path, fall back to bundled)

## Step 2: Implement the sweep function

In `compaction.py`, add `_run_memory_sweep(ctx, messages_to_compact)`:
- Build a child context using `Context.for_task()` pattern
- Only allow vault tools (vault_read, vault_write, vault_journal_append, vault_search)
- Format the messages as readable text for the prompt
- Run `run_agent_turn` with the sweep prompt + formatted history
- Wrap in try/except, log start/complete/error

## Step 3: Hook into compact_history

In `compact_history()`, after partitioning old turns but before summarization:
- If `config.compaction.memory_sweep_enabled`, snapshot old messages
- Fire off `asyncio.create_task(_run_memory_sweep(...))` 
- Proceed with compaction immediately (don't await)

## Step 4: Tests

- Test that sweep is triggered when enabled (mock run_agent_turn)
- Test that sweep is skipped when disabled
- Test that compaction proceeds even if sweep errors
- Test that the sweep gets the correct messages and tools

## Step 5: Documentation

- Update CLAUDE.md with the new feature
- Update docs/memory.md or relevant doc
