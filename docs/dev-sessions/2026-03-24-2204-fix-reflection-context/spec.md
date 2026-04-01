# Reflection Context Fix — Spec

**Issue:** [#124](https://github.com/lmorchard/decafclaw/issues/124)

## Problem

The reflection judge evaluates responses using only the current turn's tool results, truncated to 500 characters. In multi-turn conversations where the assistant accumulates knowledge across many tool calls, the judge incorrectly flags legitimate prior-turn information as hallucination. This causes a death spiral: the assistant apologizes, strips valid info, gets rejected again, and repeats.

## Changes

### 1. Prior-turn tool summary for the judge

New function `build_prior_turn_summary(history, turn_start_index, max_turns=3, max_result_len=200)` in `reflection.py`:

- Scans `history[:turn_start_index]` for tool call/result pairs
- Turn boundaries defined by user-role messages (walk backwards to find the last N turns)
- Includes tool name + key args + truncated result snippet (first 200 chars)
- Covers only the last N turns (default 3) to keep it bounded
- Returns a formatted string like "Tools used in prior turns:\n...", or empty string if no prior tools
- Returns empty string if `turn_start_index` is 0 (first turn, no prior context)

Called from `agent.py` alongside existing `build_tool_summary`, passed as a new `prior_turn_summary` parameter to `evaluate_response`.

### 2. Increase and make `MAX_TOOL_RESULT_LEN` configurable

- Add `max_tool_result_len: int = 2000` to `ReflectionConfig` in `config_types.py`
- Change `build_tool_summary` signature to accept `max_result_len: int = 2000` parameter (instead of reading from config directly — keeps it a pure function, caller passes the value from config)
- Remove the module-level `MAX_TOOL_RESULT_LEN = 500` constant

### 3. Prompt update (`REFLECTION.md`)

Update the judge prompt to:

- Add a `{prior_turn_tools}` template variable, clearly labeled as prior-turn context
- Instruct the judge that referencing information from prior turns is legitimate, not hallucination
- Distinguish "info not in this turn's results" (acceptable) from "info contradicting this turn's results" (fail)
- Keep the existing guidance about common failure modes

### 4. `evaluate_response` signature change

Add `prior_turn_summary: str = ""` parameter. Format it into the prompt template as the `{prior_turn_tools}` block. Empty string = no prior context (backwards compatible).

## Files touched

- `src/decafclaw/reflection.py` — new `build_prior_turn_summary`, update `build_tool_summary` to accept configurable max_result_len, update `evaluate_response` signature, remove hardcoded `MAX_TOOL_RESULT_LEN`
- `src/decafclaw/prompts/REFLECTION.md` — prompt rewrite with prior-turn guidance and new template variable
- `src/decafclaw/config_types.py` — add `max_tool_result_len` to `ReflectionConfig`
- `src/decafclaw/agent.py` — call `build_prior_turn_summary`, pass both summaries to `evaluate_response`, pass config to `build_tool_summary`
- `tests/test_reflection.py` — new tests for prior-turn summary, configurable truncation, prompt integration

## Tests

All unit tests, no live LLM:

1. `build_prior_turn_summary` — pulls tool calls from prior turns, respects max_turns limit, truncates results
2. `build_tool_summary` — respects configurable `max_result_len`
3. `evaluate_response` — verify both tool summary and prior-turn summary appear in the formatted prompt

## Edge cases

- **First turn (turn_start_index=0):** No prior context — `build_prior_turn_summary` returns empty string, prompt omits the section
- **Override REFLECTION.md files:** Python's `str.format()` silently ignores extra kwargs, so overrides without `{prior_turn_tools}` still work — they just don't show prior context (fail-open)
- **No tools in prior turns:** `build_prior_turn_summary` returns empty string, same as first turn
- **Compacted history:** If earlier turns were compacted into a summary message, those won't have tool_calls — the prior-turn summary only captures what's still in raw history, which is fine

## Out of scope

- Cross-turn rejection cap (the per-turn cap via `max_retries` already exists; the root cause is the missing context, not the cap)
- Compaction-aware summaries (compaction already summarizes; this fix targets the reflection judge specifically)
