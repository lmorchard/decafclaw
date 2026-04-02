# Context Composer — Notes

## Session summary

Extracted scattered context assembly logic into a unified `ContextComposer` class (issue #182, phases 1-3).

### What was done

1. **Skeleton** — Created `context_composer.py` with `SourceEntry`, `ComposedContext`, `ComposerMode`, `ComposerState` dataclasses and `ContextComposer` class. Added `ComposerState` to `Context`. Added `context_window_size` to `LlmConfig`.

2. **System prompt** — Added `_compose_system_prompt()` wrapping `config.system_prompt` with token estimation. Added `_get_context_window_size()` with fallback from `context_window_size` to `compaction_max_tokens`.

3. **Memory + wiki** — Added `_compose_memory_context()` (async, fail-open, mode-aware) and `_compose_wiki_context()` (page dedup, not-found handling). Both produce `SourceEntry` diagnostics.

4. **Tool assembly** — Added `_compose_tools()` replicating `_build_tool_list()` logic with diagnostics tracking active/deferred counts.

5. **Full compose()** — Implemented the complete orchestration: truncation, system prompt, wiki injection, memory injection, history filtering/remapping, tool classification, event publishing. Returns `ComposedContext`.

6. **Agent loop integration** — Replaced `_prepare_messages()` call with `composer.compose()` in `run_agent_turn()`. Added `record_actuals()` after LLM response. Initialized deferred message tracking from compose result. Updated CLAUDE.md and docs.

### Design decisions during implementation

- **System prompt is simpler than planned**: `config.system_prompt` already includes always-loaded skill bodies. Per-conversation activated skills get their body as `activate_skill` tool response, not system prompt injection. So `_compose_system_prompt` just wraps the cached value.

- **Memory context returns raw results**: Changed `_compose_memory_context` to return 4-tuple (msgs, formatted_text, raw_results, entry) so compose() can publish the raw results in the memory_context event for UI rendering.

- **Tool iteration stays in agent loop**: The iteration loop still calls `_build_tool_list()` per-iteration since fetched tools change mid-turn. The composer handles initial assembly and diagnostics only. Full tool lifecycle in the composer is a follow-up.

- **Deferred message handoff**: compose() may insert a deferred tool list as `messages[1]`. The agent loop detects this and initializes `deferred_msg` from it so subsequent iterations can update/remove it correctly.

### What's deferred

- Relevance scoring, dynamic budget allocation, budget-aware truncation
- Context stats command for surfacing diagnostics
- Token estimate calibration from actuals
- Model switching as alternative to compaction
- Full tool lifecycle in composer (per-iteration tool assembly)

### Test count

Started at 966 tests, ended at 988 tests (22 new tests for context composer).
