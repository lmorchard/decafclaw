# Code Quality Sweep — Spec

## Status: Ready

## Items

### Moderate
1. Config mutated at runtime — move mutable state off Config
2. archive.py scope creep — extract skills/skill_data persistence
3. http_server.py has Mattermost concerns — move button builders
4. Heartbeat agent-turn logic duplicated
5. Context is a bag of arbitrary attrs — getattr everywhere, shared mutables in fork
6. websocket.py giant dispatch — extract handlers, deduplicate serialization
7. ~85 deferred imports for circular deps
8. Config default mismatch — max_tool_iterations 200 vs 30

### Minor
9. Private compaction functions imported cross-module
10. web_fetch is sync
11. Mixed tool return types (strings vs ToolResult)
12. Conversation serialization repeated 10+ times
13. Fire-and-forget create_task without references
