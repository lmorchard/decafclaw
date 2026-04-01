# Interactive Buttons — Retrospective

## Session Overview

- **Date:** 2026-03-16
- **Duration:** ~1.5 hours
- **Branch:** `interactive-buttons`
- **PR:** #54
- **Commits:** 15
- **Conversation turns:** ~25
- **Tests:** 384 → 404 (+20 new)

## Recap of Key Actions

1. **Brainstorm** — spec'd out HTTP server infrastructure + button confirmation UI in ~10 minutes
2. **Phase 1:** Config fields + starlette/uvicorn dependencies
3. **Phase 2:** HTTP server module — Starlette app with `/health` and `/actions/confirm`, token registry, button builder. 14 tests.
4. **Phase 3:** Wired HTTP server into Mattermost bot lifecycle (asyncio task alongside websocket)
5. **Phase 4+5:** Button confirmation UI integrated into `on_confirm_request`, shell tool "always" removed (approve/deny/pattern only), emoji auto-hidden when HTTP enabled
6. **Live testing & debugging:**
   - Mattermost "Allow untrusted internal connections" setting required for LAN callbacks
   - `MATTERMOST_DISABLE_EMOJI_CONFIRMS` flipped to positive `MATTERMOST_ENABLE_EMOJI_CONFIRMS` with auto-detection
   - Per-confirmation tokens replaced static secret (HMAC with server secret)
   - Allow Pattern button silently broken — 4 iterations to discover Mattermost drops callbacks for button IDs with underscores
7. **Docs:** `docs/http-server.md` with setup, routes, Mattermost config, gotchas

## Divergences from Plan

- **Phases 4 and 5 merged** — button integration and shell tool cleanup done together since they're the same code path
- **Token security upgrade unplanned** — Les asked during live testing to use dynamic per-confirmation tokens instead of a static secret. Better design, small scope increase.
- **Emoji config flag flipped** — plan had `MATTERMOST_DISABLE_EMOJI_CONFIRMS`, Les requested positive flag with auto-detection from `HTTP_ENABLED`. Better UX.
- **Button ID debugging unplanned** — 4-5 iterations to find the underscore issue

## Key Insights & Lessons Learned

1. **Mattermost silently drops button callbacks when action ID contains underscores.** No error, no log, nothing. Changed `add_pattern` to `allowpattern`. This is completely undocumented. Worth checking Mattermost server logs next time — Les has access but hasn't used them recently.

2. **"Allow untrusted internal connections"** is a Mattermost setting that blocks outbound HTTP to LAN addresses by default. Without it, button clicks silently fail. Must be documented for any HTTP callback integration.

3. **Single-use tokens are better than static secrets** for callback URLs. Each confirmation gets a unique HMAC token that's consumed on use. No replay, no forgery, and the static secret becomes optional defense-in-depth.

4. **Positive config flags with auto-detection** are better UX than negative flags. `MATTERMOST_ENABLE_EMOJI_CONFIRMS` with auto-detection from `HTTP_ENABLED` means zero config in the common case.

5. **Starlette + uvicorn as asyncio task** works cleanly alongside the websocket client. No event loop conflicts, graceful shutdown via task cancellation. Good foundation for the future web gateway UI.

6. **Event bus as the bridge** — the HTTP handler publishes the same `tool_confirm_response` event as emoji polling. Zero changes needed to `request_confirmation()` or any tool code. The decoupling paid off.

## Process Observations

- **Brainstorm was fast and focused** — clear prior art (emoji flow) made the design obvious
- **Building was fast** (Phases 1-5: ~45 minutes) — well-scoped plan with clear integration points
- **Debugging was the time sink** (~30 minutes) — the underscore issue required systematic elimination. Could have been faster with Mattermost server log access.
- **Live testing found real issues** that unit tests couldn't: Mattermost config requirements, button ID restrictions, UX preferences

## Efficiency Notes

- 6 phases planned, executed in ~45 min of building + ~30 min debugging + ~15 min brainstorm/retro
- Per-button tokens added 6 new tests
- The debug commits (reorder, rename, style) are noise in the git log but tell the story of the investigation

## Status

**PR #54 open.** Feature fully functional:

| Feature | Status |
|---------|--------|
| HTTP server lifecycle | Working |
| Health endpoint | Working |
| Approve button | Working |
| Deny button | Working |
| Allow Pattern button | Working (after underscore fix) |
| Always button | Working (non-shell tools) |
| Single-use tokens | Working |
| Emoji fallback | Working |
| Auto-hide emoji with HTTP | Working |
| Message update on click | Working |

## Still To Do

- Merge PR and deploy
- Test "Always" button on skill activation and claude_code_send
- Consider Mattermost server log monitoring for future debugging
- Future: web gateway UI using the same HTTP server
