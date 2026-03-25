# Reflection Context Fix — Session Notes

**Date:** 2026-03-24
**Branch:** `fix-reflection-context`
**PR:** [#125](https://github.com/lmorchard/decafclaw/pull/125)
**Issue:** [#124](https://github.com/lmorchard/decafclaw/issues/124)

## Recap

Fixed the reflection judge's lack of multi-turn context, which caused it to flag legitimate prior-turn knowledge as hallucination and trigger death-spiral retries.

### Key actions

1. **Brainstorm** — 5 iterative questions to scope the fix: prioritization of the 5 proposed improvements, prior-turn summary depth (option B: calls + truncated results, last 3 turns), `MAX_TOOL_RESULT_LEN` bump (configurable, default 2000), prompt framing (prior-turn = legit, contradictions = fail), `evaluate_response` signature (option A: caller builds both summaries)
2. **Spec review** — self-review caught 3 gaps: turn boundary definition, override template compatibility, `build_tool_summary` signature details
3. **4-step execution:**
   - Step 1: Config + `build_tool_summary` signature (configurable `max_tool_result_len`)
   - Step 2: `build_prior_turn_summary` + helper extraction + 5 tests
   - Step 3: `REFLECTION.md` prompt rewrite + `evaluate_response` signature + 2 tests
   - Step 4: Wired into agent loop
4. **PR** — pushed and created [#125](https://github.com/lmorchard/decafclaw/pull/125)

## Divergences from plan

Essentially none. The only unplanned moment was a ruff import-ordering fix in Step 4 (auto-fixed in one command). The plan mapped cleanly to implementation.

## Key insights

- The original `MAX_TOOL_RESULT_LEN = 500` was way too aggressive — 500 chars doesn't even cover a short wiki page. The 2000 default is much more reasonable.
- The death spiral in the issue (8 rejections) wasn't a `max_retries` bug — `max_retries=2` caps per-turn, but each new user message resets the counter. The real fix is giving the judge better context, not a harder cap.
- Extracting `_format_tool_args` and `_extract_tool_lines` as shared helpers cleaned up the code nicely — `build_tool_summary` went from 20+ lines of inline logic to a 4-line wrapper.
- Python's `str.format()` silently ignoring extra kwargs means override `REFLECTION.md` files are backwards-compatible for free — no migration needed.

## Efficiency

- **Turns:** ~10 user turns (brainstorm through PR)
- **Commits:** 4 (one per step, as planned)
- **Tests added:** 7 new, 703 total passing
- **Files changed:** 5 (`config_types.py`, `reflection.py`, `agent.py`, `REFLECTION.md`, `test_reflection.py`)
- **Cost:** Not available from this session
- The brainstorm was efficient — 5 focused questions, no wasted rounds. The spec self-review was worth it (caught real gaps). Execution was smooth because the plan was detailed enough to follow mechanically.

## Possible process improvements

- Could have combined Steps 3 and 4 — they're both small and tightly coupled. Four steps was slightly over-granular for this size of change.
- Live Mattermost testing is still a manual TODO on the PR checklist — would be nice to have an automated integration test for reflection, but that's a bigger investment.

## Still TODO

- [ ] Live test in Mattermost with a multi-turn research conversation
- [ ] Update docs if reflection gets its own `docs/` page (currently doesn't have one)
