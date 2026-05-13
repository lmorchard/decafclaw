# Agent.py split — session notes

## Outcome
- PR: https://github.com/lmorchard/decafclaw/pull/483
- Issue: #438 (auto-closes on merge via `Closes #438`)
- Status: opened, Copilot review clean (no inline comments), board moved to "In review"

## Line counts
| File | Before | After |
|---|---|---|
| `src/decafclaw/agent.py` | 1451 | 1000 |
| `src/decafclaw/tool_definitions.py` | — | 166 |
| `src/decafclaw/tool_execution.py` | — | 364 |

The 250-line gap vs. the spec's "~750" target on `agent.py` is the wiki/attachment helper cluster (`_resolve_attachments`, `_parse_wiki_references`, `_read_wiki_page`, `_get_already_injected_pages`, `_WIKI_MENTION_RE`) — explicitly deferred to #439 in the spec's "What we're NOT doing" section.

## Phase summary
- **Phase 1** — `tool_definitions.py` created. Registry cluster moved (`collect_all_tool_defs`, `build_tool_list`, `refresh_dynamic_tools`, `invalidate_skill_cache`, `_skill_def_cache`). Imports updated in `agent.py`, `context_composer.py`, `tools/skill_tools.py`. Test patch targets updated in `test_context_composer.py` (24 sites) + `test_orphaned_tool_results.py` (1 site).
- **Phase 2** — `tool_execution.py` created. Invocation cluster moved (`execute_tool_calls`, `execute_single_tool`, `process_tool_media`, `resolve_widget`, `_media_placeholder_pattern`). `_archive`, `_check_cancelled`, `_conv_id` duplicated into the new module (22 lines, intentional, comment documents why). Test patch targets updated across 4 test files.
- **Phase 3** — Docs sync. CLAUDE.md "Core" key-files list updated with the two new modules; `docs/architecture.md` tool-execution-concurrency snippet drops the leading underscore on `execute_single_tool`.

## Verification
- `make check` — green (lint + pyright + tsc + message-types drift)
- `make test` — 2419 passed, 0 failed
- Smoke tests for new public surface + `eval.runner.run_agent_turn` — all OK

## Surprises / learnings
1. **Test patch targets matter more than expected.** 27 test sites referenced `decafclaw.agent._collect_all_tool_defs` via `unittest.mock.patch(...)`. The first `make test` after Phase 1 failed loudly because patch targets are strings, not symbols — the linter and pyright can't catch them. Grep before committing if you touch a module that tests patch.
2. **`functools` / `json` / `dataclasses.replace` all became dead imports in `agent.py` after Phase 2.** Ruff caught them via the import-organization rule once Phase 1's check-message-types step completed. Good signal that the relocation was clean — nothing else in agent.py needed those.
3. **Spec's "~750 lines" target wasn't a hard floor.** The wiki/attachment helpers (~250 lines) are #439's scope. Recognizing the deferred-work boundary kept this PR focused; landing both halves together would have made the diff harder to review.

## Plan adaptations
- Plan's Phase 2 anticipated the `_archive` / `_check_cancelled` circular-import problem and resolved it inline (duplicate the two helpers into `tool_execution.py` rather than carve out a third module). Worked exactly as planned — no surprises during execution.

## What's next
- Issue #439 will take the wiki/attachment helpers into `context_composer.py`. The #439 agent has been deferring its work pending this PR's `tool_definitions.py` landing — once #483 merges, #439's import target exists.
