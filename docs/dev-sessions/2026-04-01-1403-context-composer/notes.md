# Context Composer — Notes

## Session summary

Extracted scattered context assembly logic into a unified `ContextComposer` class (issue #182, phases 1-3). PR #195.

## Key actions

1. **Brainstormed spec** — 10 Q&A rounds covering ownership/lifecycle, scope, structured result, token budget model, mode awareness, compaction relationship, success criteria.
2. **Planned 6 steps** — skeleton → system prompt → memory/wiki → tools → full compose() → agent loop integration.
3. **Executed all 6 steps** — each with lint + test + commit. 966 → 988 tests.
4. **Opened PR #195**, got Copilot review comments.
5. **Post-PR review fixes** (4 additional commits):
   - Moved archiving out of composer (Les caught this)
   - Fixed token double-counting (Copilot caught this)
   - Decoupled skip_memory_context from HEARTBEAT mode (Copilot caught this)
   - Aligned history diagnostics items_included (Copilot caught this)
   - Added deprecation/safety/future-use documentation
6. **Filed #196** for wiki/memory → vault naming cleanup.

## Divergences from plan

- **Archiving pulled out of composer** — the plan had compose() handling archiving (since `_prepare_messages()` did). Les questioned this during PR review. The cleaner separation: composer decides what goes in, caller persists it. Added `messages_to_archive` field to `ComposedContext`.
- **`skip_memory_context` mode mapping changed** — plan mapped it to HEARTBEAT mode, but this incorrectly skipped wiki context too. Fixed to keep INTERACTIVE mode and let the composer check the flag directly.
- **System prompt simpler than planned** — discovered during implementation that skill bodies aren't injected into the system prompt per-conversation. They come back as tool results. So `_compose_system_prompt` just wraps the cached `config.system_prompt`.
- **`_prepare_messages()` kept as deprecated** — plan said to remove it, but wiki context tests still exercise it directly. Marked deprecated instead; full removal is a follow-up.

## Insights and lessons learned

- **Extraction refactors reveal design assumptions.** Moving code into a new module forces you to think about what's a responsibility vs a side effect. Archiving looked like it belonged in the composer until you questioned it.
- **Copilot review caught real bugs.** The token double-counting and mode mapping issues were subtle — both passed all tests but would have produced incorrect diagnostics and changed wiki behavior for heartbeat/scheduled tasks.
- **Mode enums need callers, not just definitions.** HEARTBEAT/SCHEDULED modes were defined but never wired. The `skip_memory_context` flag was doing double duty. Worth being more deliberate about when to use enums vs flags.
- **Review feedback loop is a recurring pattern.** We do "push PR → check comments → fix → push" a lot. Worth considering a command to streamline this.

## What's deferred

- Relevance scoring (recency + importance + similarity)
- Dynamic budget allocation across sources
- Budget-aware truncation/summarization
- Context stats command for surfacing diagnostics
- Token estimate calibration from actuals
- Model switching as alternative to compaction
- Full tool lifecycle in composer (per-iteration tool assembly)
- Remove deprecated `_prepare_messages()` and migrate its tests
- Wiki/memory → vault naming cleanup (#196)

## Stats

- **Commits:** 10 (6 planned steps + 4 review fixes)
- **Files changed:** 11 (+1539 / -11 lines)
- **New files:** `context_composer.py`, `test_context_composer.py`, `docs/context-composer.md`
- **Tests:** 966 → 988 (36 new, but also removed the NotImplementedError test = net +22)
- **Conversation turns:** ~35
- **Session duration:** ~2 hours (brainstorm + plan + execute + PR + review)

## Process observations

- **6-step incremental plan worked well.** Each step was testable and commitable independently. Steps 1-5 were additive (no existing behavior changes), step 6 was the swap. This meant regressions were easy to bisect.
- **Copilot review as a second pair of eyes.** Caught issues we missed — token double-counting and mode/flag conflation. Worth always checking PR comments before considering a PR done.
- **Brainstorm → plan → execute flow is solid.** The brainstorm surfaced the right design decisions (stateful vs stateless, mode awareness, compaction relationship). The plan mapped cleanly to implementation.
