# Eval Loop

## Overview

A lightweight eval harness for testing prompt and tool behavior with
real LLM calls. Run YAML test cases against the agent, collect results,
and get automated reflection on failures. No changes to agent code —
exercises `run_agent_turn` from outside.

## Goals

- Test prompt/tool changes before deploying to Mattermost
- Compare model behavior (Flash vs Pro)
- Detect regressions in tool usage and response quality
- Generate improvement suggestions from failures automatically
- Build a history of eval results for comparison over time

## Test Case Format

YAML files in `evals/` at project root:

```yaml
- name: "finds cocktail preference"
  setup:
    memories:
      - tags: [preference, cocktail, drink]
        content: "Favorite cocktails are Boulevardier and Old Fashioned"
  input: "What are my favorite cocktails?"
  expect:
    response_contains: "Boulevardier"
    max_tool_calls: 5

- name: "recalls recent memory"
  setup:
    memories:
      - tags: [project]
        content: "Working on the event bus refactor"
  input: "What was I working on?"
  expect:
    response_contains: "event bus"
```

### Fields

- **name** — human-readable test name
- **setup** — fixture data to create before running
  - **memories** — list of `{tags, content}` entries to save via `memory.save_entry`
  - Future: could add `archive`, `files`, etc.
- **input** — the user message to send to the agent
- **expect** — assertions on the result
  - **response_contains** — string that must appear in the final response (case-insensitive)
  - **max_tool_calls** — maximum number of tool calls allowed (optional)

### Future assertion types (deferred)

- `tool_called` — assert a specific tool was used
- `response_excludes` — assert something is NOT in the response
- `tool_order` — assert tools were called in a specific sequence
- `think_used` — assert the agent used the think tool

## CLI

```
uv run python -m decafclaw.eval <file_or_dir> [options]
```

### Arguments

- `file_or_dir` — path to a YAML file or directory of YAML files.
  If directory, runs all `.yaml` files in it.

### Options

- `--model MODEL` — override the LLM model (default: from config/env)
- `--judge-model MODEL` — model for failure reflection (default: same as --model)
- `--verbose` — show full responses and tool call details on each test

### Examples

```bash
# Run all evals with default model
uv run python -m decafclaw.eval evals/

# Run one file with a specific model
uv run python -m decafclaw.eval evals/memory.yaml --model gemini-2.5-pro

# Compare models
uv run python -m decafclaw.eval evals/memory.yaml --model gemini-2.5-flash
uv run python -m decafclaw.eval evals/memory.yaml --model gemini-2.5-pro
# Then diff the results bundles
```

## Output

### Console (terse)

```
decafclaw eval — gemini-2.5-flash — evals/memory.yaml

[1/3] finds cocktail preference ............ PASS  (2.1s, 847 tokens, 3 tools)
[2/3] recalls recent memory ................ PASS  (1.3s, 412 tokens, 1 tool)
[3/3] finds memory with plural query ....... FAIL  (4.2s, 1203 tokens, 5 tools)

3 tests, 2 passed, 1 failed (7.6s, 2462 tokens)
Results: evals/results/2026-03-14-1130-gemini-2.5-flash/
```

### Result Bundle

Each eval run produces a self-contained directory:

```
evals/results/{timestamp}-{model}/
  results.json          # full results: pass/fail, timing, tokens, responses
  workspace/            # temp workspace used during the run (memories, archives)
  reflections/          # judge feedback per failed test (if any)
```

#### results.json

```json
{
  "timestamp": "2026-03-14T11:30:00",
  "model": "gemini-2.5-flash",
  "judge_model": "gemini-2.5-flash",
  "source": "evals/memory.yaml",
  "summary": {
    "total": 3,
    "passed": 2,
    "failed": 1,
    "duration_sec": 7.6,
    "total_tokens": 2462
  },
  "tests": [
    {
      "name": "finds cocktail preference",
      "status": "pass",
      "duration_sec": 2.1,
      "prompt_tokens": 623,
      "completion_tokens": 224,
      "tool_calls": 3,
      "response": "Your favorite cocktails are..."
    },
    {
      "name": "finds memory with plural query",
      "status": "fail",
      "duration_sec": 4.2,
      "prompt_tokens": 890,
      "completion_tokens": 313,
      "tool_calls": 5,
      "response": "I couldn't find any memories...",
      "failure_reason": "Expected 'Boulevardier' in response",
      "reflection_file": "reflections/finds-memory-with-plural-query.md"
    }
  ]
}
```

#### Reflection files

On failure, the judge model is asked to analyze why the test failed:

```
reflections/finds-memory-with-plural-query.md
```

The judge receives:
- The test case (input, expected result, setup)
- The agent's actual response
- The tool calls made
- The system prompt and tool descriptions

And is asked: "Why did the agent fail this test? What specific changes
to the system prompt, tool descriptions, or tool behavior would fix it?"

The reflection is saved as markdown for human review.

## Token Tracking

`run_agent_turn` may make multiple LLM calls per turn (tool iterations).
To get total token usage, accumulate on the context during the turn:

- Add `ctx.total_prompt_tokens` and `ctx.total_completion_tokens`
  (initialized to 0 at the start of each eval test)
- After each `call_llm` in `run_agent_turn`, the usage is already
  tracked via `prompt_tokens` — but we need the cumulative total
- For eval purposes, inspect the history list after the turn to count
  tool calls (`role == "tool"` messages), and read token totals from
  the context

This requires a small addition to `run_agent_turn`: accumulate tokens
on the context. This is a minor agent code change but justified — it's
useful beyond evals (debug_context_stats, observability).

## How It Works

For each test case:

1. **Setup** — create a temp workspace directory. Write fixture memories
   using `memory.save_entry` with the temp config.
2. **Build context** — create a Config pointing at the temp workspace,
   create an EventBus and Context, set conv_id and other context fields.
3. **Run** — call `await run_agent_turn(ctx, input, history)`.
   Track the history list to count tool calls after.
4. **Assert** — check response against expectations.
5. **Record** — capture timing, token usage, response, pass/fail.
6. **Reflect** (on failure) — send the test case + result to the judge
   model for analysis. Save reflection to the bundle.
7. **Bundle** — copy the temp workspace into the result bundle directory.

## Implementation

- **`src/decafclaw/eval.py`** or **`src/decafclaw/eval/__main__.py`** —
  the CLI entry point, invocable via `python -m decafclaw.eval`
- Uses `argparse` for CLI options
- Loads YAML with `pyyaml` (new dependency) or stdlib `json` if we
  convert to JSON format
- Creates real Config/Context/EventBus for each test
- Calls real `run_agent_turn` with real LLM
- No mocking — this is a live integration test

## Scope

### Housekeeping

- `evals/results/` added to `.gitignore` — results contain LLM responses
  and temp workspace data, not for version control

### In scope

- YAML test case format with setup/input/expect
- CLI with --model, --judge-model, --verbose
- Terse console output with timing and tokens
- Per-run result bundle with results.json, workspace, reflections
- Failure reflection via judge model
- `evals/` directory with initial test cases for memory search

### Out of scope (future)

- Record/replay for deterministic runs
- Rich assertions (tool_called, response_excludes, tool_order)
- Parallel test execution
- CI integration
- Comparison/diff tool across result bundles
- Cost budgets or spending limits per run
