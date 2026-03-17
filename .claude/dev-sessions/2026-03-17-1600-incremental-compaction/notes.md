# Notes

## Session log

- Started session for incremental compaction (#57)
- Reviewed compaction.py and archive.py
- Planned 3-step implementation
- Implemented incremental compaction with timestamp-based boundary detection
- First test failed due to message-count boundary math; switched to timestamp approach
- Added 2 new tests, all 435 pass

---

# Retrospective: Stop Button (#55)

## Recap

Added an interactive Stop button to Mattermost agent turns. The button cancels the in-progress turn via the same asyncio.Event mechanism as the existing emoji reaction. Along the way, also fixed duplicate tool definitions in skill restoration, added LLM error body logging, and replaced "(message deleted)" placeholder behavior with inline error display.

PRs: #67 (stop button), merged with 3 squashed commits.

## Divergences

The original plan was straightforward: post a button, wire it to cancel. Reality:

1. **Separate message approach** → "(message deleted)" ghost on cleanup
2. **Attach to streaming messages via props on every edit** → buttons multiplied, none removed (Mattermost preserves props when you omit them from a PATCH)
3. **Move button between posts** → empty zero-width-space messages left behind
4. **Back to attached, with explicit strip** → `_force_edit` was sending `props={"attachments":[]}` on ALL edits, stripping confirmation buttons and causing "(message deleted)" on empty posts
5. **Text-only edits + explicit strip at transitions** → props-only strip call (no message) caused "(message deleted)"
6. **Final: fetch post text before stripping** → worked

Each iteration revealed a different Mattermost API behavior that wasn't documented.

## Insights

- **Mattermost attachment behavior**: omitting `props` from a PATCH preserves existing props. You must explicitly send `{"attachments": []}` to strip them. But sending a PATCH with only props and no `message` field clears the message text, showing "(message deleted)". Always include the message text when patching props.
- **Pre-existing bugs masked the feature**: The duplicate tool definition error (skill restoration extending tools every turn) and the "(message deleted)" on LLM errors were both pre-existing bugs that only became visible when testing the stop button end-to-end. Fixing them was necessary to validate the feature.
- **Mattermost's interactive button callback response** (`"update": {...}`) handles its own post update — the server-side finalize was double-stripping when the user clicked Stop.

## Cost

Not tracked.

## Efficiency

- The iterative Mattermost API exploration was slow but probably unavoidable — the attachment behavior isn't well documented and required empirical testing.
- The two side-fix bugs (duplicate tools, LLM error display) were discovered organically during testing but added significant scope to what was supposed to be a small feature.
- Squashing 11 commits into 3 logical ones at the end was the right call — the iteration history wasn't valuable to preserve.

## Process improvements

- **Test Mattermost API behavior in isolation before building features on assumptions.** A quick spike to verify "does PATCH preserve attachments?" / "does props-only PATCH clear message?" would have saved 3-4 iterations.
- **When a feature test fails for unrelated reasons, fix those first in a separate commit/branch** rather than folding them into the feature branch. The stop button PR ended up carrying 3 distinct fixes.
- **Document Mattermost API quirks** somewhere (CLAUDE.md or docs/) so future features don't rediscover them.

## Conversation turns

~25 exchanges for the stop button, plus debugging side issues.

## Other highlights

- The `_strip_stop_from` pattern (fetch post text, then edit with text + empty attachments) is a reusable recipe for any future Mattermost attachment management.
- The LLM error logging (retry non-streaming to capture error body) was a 14-line change that will pay dividends every time LiteLLM returns a cryptic error.
