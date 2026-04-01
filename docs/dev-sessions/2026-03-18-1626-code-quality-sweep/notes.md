# Code Quality Sweep — Notes

## All 13 items complete

### Batch A — Safe renames/moves
- Item 7: Fixed max_tool_iterations default mismatch (env var "30" → "200")
- Item 3: Made compaction helpers public (flatten_messages, estimate_tokens)
- Item 1: Extracted persistence.py from archive.py
- Item 4: Added ConversationMeta.to_dict(), replaced 10+ inline dicts
- Item 2: Moved button builders + token registry to mattermost_ui.py

### Batch B — Config/Context cleanup
- Item 5: Stopped mutating Config (_preloaded_skill_defs → module-level cache)
- Item 10: Made web_fetch async
- Item 6: Context getattr cleanup — 60 sites replaced with direct access

### Batch C — Logic refactors
- Item 8: Deduplicated heartbeat (run_section_turn shared helper)
- Item 12: Moved 7 deferred imports to top-level, labeled remaining with # deferred: circular dep
- Item 11: Standardized 49 tool error returns to ToolResult

### Batch D — Structural
- Item 13: Stored create_task references (background task tracking)
- Item 9: Refactored websocket_chat (260-line elif chain → 11 handler functions + dispatch map)

## Stats
- 13 commits, 500 tests passing
- Branch: code-quality-sweep, PR #75
- New files: persistence.py, mattermost_ui.py
