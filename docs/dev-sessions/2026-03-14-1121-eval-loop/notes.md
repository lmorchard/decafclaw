# Session Notes — Eval Loop

## Session Info

- **Date:** 2026-03-14, started ~11:21
- **Branch:** `eval-loop`
- **Commits:** 5
- **New files:** `eval/` package (4 files), `evals/` (2 YAML files)
- **Tests:** 8 single-turn + 4 multi-turn = 12 eval cases

## Recap

Built a live eval harness and used it to iterate on prompts. The
eval loop proved its value immediately — detected failures, generated
actionable reflections, and guided prompt improvements across 10 runs.

### What we built

1. **eval/runner.py** — execute YAML test cases against real LLM
2. **eval/reflect.py** — judge model analyzes failures, suggests fixes
3. **eval/__main__.py** — CLI with --model, --judge-model, --verbose
4. **Multi-turn support** — turns share history for save→recall cycles
5. **Rich assertions** — response_contains (string/list), regex (re:),
   response_not_contains, max_tool_calls
6. **Result bundles** — per-run directory with results.json, reflections/
7. **Token accumulation** — ctx.total_prompt/completion_tokens in agent

### Playtesting results

| Run | Model | Tests | Pass | Change |
|-----|-------|-------|------|--------|
| 1 | Pro | 5 | 3 | Baseline |
| 2 | Flash | 5 | 4 | Fixed assertions |
| 3 | Flash | 5 | 4 | Stronger memory prompt |
| 4 | Flash | 5 | 5 | "MUST check memory" + memory_recent guidance |
| 5 | Flash | 8 | 7 | Expanded test cases |
| 6 | Flash | 8 | 6 | Stabilization check |
| 7 | Flash | 8 | 5 | Synonym teaching (reverted — teaching to test) |
| 8 | Flash | 8 | 5 | Idiom rephrasing in checklist |
| 9 | Flash | 8 | 5 | Stability check |
| 10 | Pro | 8 | 8 | Model comparison — confirms capability gap |

Multi-turn: 4/4 on Flash (save→recall cycles work reliably)

### Key findings

1. **Eval loop works as designed.** Detect → reflect → tweak → rerun.
   Reflections consistently identified the right fix area.
2. **Flash stable at 5/8, Pro at 8/8.** The 3 flaky tests all require
   multi-hop search or aggressive expansion — Flash follows the checklist
   inconsistently. This is model capability, not prompt quality.
3. **Multi-turn tests are the sweet spot.** 4/4 stable because the
   agent saves memories itself (good tags) and retrieves from its own
   recent saves. The full cycle works.
4. **Assertion design matters more than expected.** "save" vs "Python",
   "no" vs "don't" — small wording mismatches cause false failures.
   Lists, regex, and negative assertions help.
5. **Don't teach to the test.** Adding specific synonyms to the checklist
   (e.g., "living → job") improves the target test but doesn't generalize.
   The real fix is semantic search.
6. **Flash responds to "MUST" and "NEVER" better than "should" and "always".**
   Stronger imperatives in prompts measurably improved compliance.
7. **Pro is useful as a prompt vet.** Optimize for Flash (cost), validate
   on Pro (capability ceiling). If Pro fails, the prompt needs work. If
   only Flash fails, it's a model gap.

## Backlog items identified

- Parallel eval execution (asyncio.gather across tests)
- Multi-turn eval support (done in this session)
- LLM-as-a-judge for soft assertion (beyond substring matching)
- Eval concurrency for faster runs
- Model-specific prompt tuning (confirmed: Flash needs different prompts)
