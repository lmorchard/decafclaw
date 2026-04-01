# Concurrent Tool Calls & Delegate Simplification — Notes

## Session Log

### Phase 1 — tool_call_id plumbing (done)
- Added `current_tool_call_id` to Context, auto-included in `publish()`
- Agent loop sets/clears around each tool call
- Confirmation matching uses tool_call_id when available

### Phase 2 — config (done)
- Added `max_concurrent_tools` (default 5) to Config

### Phase 3 — concurrent execution (done)
- Rewrote `_execute_tool_calls` with `asyncio.gather` + semaphore
- Added `fork_for_tool_call` to Context — copies all fields tools need
- Cancel watcher pattern for emergency stop
- 4 new tests: concurrent timing, semaphore, partial failure, ordering

### Phase 4 — UI updates (done)
- Mattermost ConversationDisplay tracks by tool_call_id dict
- Confirmation flow threads tool_call_id through emoji polling, HTTP buttons, web websocket

### Phase 5 — delegate simplification (done)
- Renamed delegate → delegate_task, flat schema: just `task: str`
- Child inherits all parent tools/skills, no more tools/system_prompt params

### Phase 6 — docs (done)
- Updated delegation.md, README.md, CLAUDE.md

### Bug fixes found during QA
- **MCP shutdown traceback**: `CancelledError` is `BaseException`, not `Exception`
- **Uvicorn shutdown traceback**: use `server.should_exit = True` instead of task cancel
- **Confirmation broken by strict tool_call_id matching**: relaxed to require match only when both sides have it
- **Frontend not echoing tool_call_id**: confirm_request/response now carries tool_call_id through the browser
- **Child agent events invisible to parent UI**: added `event_context_id` so children publish under parent's context_id
- **Child agents missing skill instructions**: include activated skill SKILL.md bodies in child system prompt
- **Child system prompt too minimal**: added explicit "check tools, don't refuse" instructions
- **All confirmations disappearing on click**: `respondToConfirm` was filtering by context_id; now filters by tool_call_id
- **Wrong command confirmed on click**: confirm-view now passes tool_call_id directly from the confirm card
- **Text appearing after tool results**: added `text_before_tools` event to flush assistant text before tools start
- **respondToConfirm signature mismatch**: added default params for cache resilience
- **activated_skills not copied in fork_for_tool_call**: children had tools but no skill context; the root cause of skill-ignoring behavior
- **Zero-tolerance convention**: added to CLAUDE.md

### Filed
- #72 — Add unit tests for web UI headless logic

## Key Lessons

- **fork_for_tool_call field list is fragile**: missing `activated_skills` caused the most confusing bug of the session. Any new Context field that tools or child agents depend on must be added here. Consider a shallow-copy approach in the future.
- **Prompt nudging is not a substitute for data**: the child agents weren't ignoring instructions — they literally didn't have them. The real fix was always the missing `activated_skills` copy, not stronger wording.
- **Browser cache breaks API changes**: changing JS function signatures without cache busting causes silent failures. Need a versioning strategy for the web UI static assets.
- **Live QA catches what unit tests don't**: the entire confirmation flow (event → websocket → browser → response → matcher) had multiple interacting bugs that no single unit test would have found.

## Summary

All planned phases complete plus 12 bug fixes found during live QA. 449 tests passing, lint and typecheck clean. Branch `concurrent-tools-delegate` with 18 commits ready for review and merge.
