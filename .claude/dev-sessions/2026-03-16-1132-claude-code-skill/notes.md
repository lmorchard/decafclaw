# Claude Code Skill — Retrospective

## Session Overview

- **Date:** 2026-03-16
- **Duration:** ~3 hours (Les task-switching between other work)
- **Branch:** `claude-code-skill`
- **PR:** #51 (merged)
- **Issue filed:** #53 (upstream SDK permission bug)
- **Conversation turns:** ~45
- **SDK API costs during testing:** ~$0.85 across test sessions

## SDK API Findings (Phase 1)

**Package:** `claude-code-sdk` v0.0.25 (NOT `claude-agent-sdk` — research was wrong on name)

**Two API patterns:**
1. `query(prompt, options)` — one-shot, returns `AsyncIterator[Message]`. Supports `resume=session_id` for multi-turn.
2. `ClaudeSDKClient` — persistent client with connect/query/receive_response/disconnect.

**Decision:** Use `query()` + `resume` — simpler, no connection lifecycle.

**Key types:** `ClaudeCodeOptions`, `ResultMessage` (session_id, total_cost_usd), `AssistantMessage` (TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock), `PermissionResultAllow`/`Deny`.

## Recap of Key Actions

1. **Brainstorm** — iterative Q&A to develop spec: session management, permission model, output logging, config, cost controls
2. **SDK research** — verified API surface, found `query()` + `resume` simpler than `ClaudeSDKClient`
3. **Built core components** (Phases 2-4): SessionManager (18 tests), permission bridge (11 tests), output logger (9 tests)
4. **Wired tools** (Phase 5): four tools (start, send, stop, sessions), config fields, skill activation
5. **Live testing & debugging** (~6 iterations):
   - Fixed relative imports (skill loader uses `importlib` without package context)
   - Fixed async prompt format (SDK expects `{type, message, ...}` not `{role, content}`)
   - Discovered API key balance issue (helpful — confirmed plumbing worked)
   - Fixed `permission_mode` and `can_use_tool` interaction
   - Tried PreToolUse hook workaround from upstream issue #24607
   - Discovered upstream SDK bug is in CLI's JS control protocol, not Python SDK
   - Settled on `bypassPermissions` + upfront confirmation
6. **Sandbox fix** — constrained `cwd` to workspace directory
7. **Output logging fix** — proper serialization of UserMessage/ThinkingBlock
8. **Research & documentation** — found 7+ upstream issues, updated PR description, filed issue #53

## Divergences from Plan

- **Phases 6 and 7 compressed** — SKILL.md written in Phase 1, config in Phase 5
- **Permission bridge built but unusable** — code is ready for when upstream bug is fixed
- **Manual smoke test script skipped** — went straight to live Mattermost testing
- **Session resume not tested** — deferred
- **~1.5 hours of unplanned debugging** on the upstream permission bug

## Key Insights & Lessons Learned

1. **Verify SDK APIs hands-on before building.** Research agent said `claude-agent-sdk`, reality was `claude-code-sdk`. API details (message format, streaming requirements) also differed. Plan's Phase 1 gate caught this.

2. **Skills loaded via `importlib.spec_from_file_location` can't use relative imports.** Must use absolute imports. Should document as convention.

3. **The Claude Code SDK's `can_use_tool` is broken upstream.** CLI's control protocol closes stream on repeated tool calls. Known issue across 7+ GitHub issues. PreToolUse hook workaround doesn't fix it — bug is in CLI's JavaScript. This is the session's biggest finding.

4. **Upfront confirmation is arguably better UX than per-tool.** One "approve this task" vs 6 individual popups. Even when the SDK bug is fixed, may want to keep upfront as primary.

5. **`bypassPermissions` = `--dangerously-skip-permissions`.** Real security consideration. Les considers this experimental until finer-grained control is available.

6. **Live testing with real API calls found bugs unit tests couldn't.** Prompt format, import issues, permission flow all required real SDK calls. Budget API costs for integration testing.

## Architecture Decisions

1. **`query()` + `resume` over `ClaudeSDKClient`** — independent calls, no connection lifecycle, simpler error handling
2. **Budget enforcement on our side** — SDK has no `max_budget_usd`, we track from `ResultMessage`
3. **Lazy session expiration** — checked on access, no background timer
4. **Upfront confirmation per `claude_code_send`** — pragmatic workaround for broken per-tool callbacks
5. **Workspace sandbox** — `cwd` resolved relative to workspace, path traversal blocked

## Process Observations

- **Brainstorm was efficient** — thorough spec in ~15 minutes
- **Plan's Phase 1 gate was valuable** — prevented building on wrong SDK assumptions
- **Debugging cycle was frustrating but necessary** — 6 iterations to find an upstream bug. Could have found it faster with more SDK source reading upfront, but the "Stream closed" error was ambiguous.
- **Push-test-report via Mattermost** worked naturally with Les's task-switching
- **Filing the issue immediately** while context was fresh was the right call

## Efficiency Notes

- Building (Phases 1-5): ~1 hour
- Debugging upstream bug: ~1.5 hours
- Brainstorm + retro: ~30 minutes
- 38 new tests, all passing
- ~$0.85 in API costs during testing

## Status

**Merged to main. Feature is functional but experimental.**

| Feature | Status |
|---------|--------|
| File creation/reading | Working |
| Bash execution | Working (bypassPermissions) |
| Session management | Working (resume/expiration untested) |
| Upfront confirmation | Working via Mattermost reactions |
| Per-tool permission | Blocked by upstream SDK bug (#53) |
| Output logging/summaries | Working |
| Workspace sandbox | Working |

## Still To Do

- Test session resume (multi-turn context)
- Test session idle expiration
- Deploy to `lmorchard@decafclaw`
- Monitor upstream SDK issues for `can_use_tool` fix
- Consider `disallowed_tools` as lighter-weight safety layer
- Add convention to CLAUDE.md: skills must use absolute imports
