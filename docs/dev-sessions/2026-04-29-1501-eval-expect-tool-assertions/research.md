# Eval Runner Architecture Research

## 1. Runner Structure & Assertions

**Location:** `src/decafclaw/eval/runner.py:133-175`

Function `_check_assertions(test_case: dict, response: str, tool_calls: int, tool_errors: int = 0) -> tuple[bool, str]` receives:
- `test_case`: Full test case dict containing `expect` field
- `response`: Final assistant text response (string)
- `tool_calls`: Integer count of tool calls in this turn
- `tool_errors`: Integer count of tool results containing `[error` (default 0)

Returns `(passed: bool, failure_reason: str)`.

**Caller signature** (`run_test`, line 178-341):
- Single-turn format: `{"input": "...", "expect": {...}}`
- Multi-turn format: `{"turns": [{input, expect}, ...]}`
- Per-turn assertion check at line 303: `passed, reason = _check_assertions(turn, response, tool_calls, tool_errors)`

**Current assertion fields** (lines 142-173):
- `response_contains`: str/list/regex (`"re:"` prefix). OR semantics: matches if any string found (case-insensitive) or regex matches (line 149: `re.search(c[3:], response, re.IGNORECASE)`).
- `response_not_contains`: str/list. AND semantics: fails if any listed string is present (line 164).
- `max_tool_calls`: int. Fails if `tool_calls > max_tools` (line 168-169).
- `max_tool_errors`: int. Fails if `tool_errors > max_errors` (line 171-173).

**Failure reporting** (lines 309-311): Constructs `f"Turn {turn_idx + 1}: {reason}"` with optional error details appended: `f"\n         Errors: {detail_str}"`.

## 2. Tool Call Data Shape

**Assistant message with tool calls** (`src/decafclaw/agent.py:1133-1147`):
```python
assistant_msg = {"role": "assistant", "content": iter_content}
assistant_msg["tool_calls"] = tool_calls
```

**Tool call structure** (from `src/decafclaw/llm/providers/openai_compat.py:113-123`):
Each tool call has: `{"id": "...", "function": {"name": "...", "arguments": "..."}, ...}`
- `id`: Tool call ID (sanitized at line 116 to strip `__thought__` data)
- `function.name`: Tool function name
- `function.arguments`: JSON string of arguments

**Tool result message** (`src/decafclaw/agent.py:802-808`):
```python
tool_msg = {
    "role": "tool",
    "tool_call_id": tool_calls[i]["id"],  # Matches assistant's tool_call["id"]
    "content": "[error: ...]" or actual result
}
```

**Provider format** (OpenAI-compatible): Returns `{"content", "tool_calls", "role": "assistant", "usage"}` — internal normalized format across all providers (Vertex, OpenAI, OpenAI-compat).

## 3. Eval Test Format

**Location:** `evals/` (YAML files)

**Single-turn example** (`evals/memory.yaml:1-9`):
```yaml
- name: "finds preference by direct term"
  setup:
    memories:
      - tags: [preference, cocktail]
        content: "Favorite cocktails are Boulevardier and Old Fashioned"
  input: "What are my favorite cocktails?"
  expect:
    response_contains: "Boulevardier"
    max_tool_calls: 8
```

**Multi-turn example** (`evals/memory-multi-turn.yaml:1-8`):
```yaml
- name: "save then recall hobby"
  turns:
    - input: "Remember that I play guitar on weekends"
      expect:
        response_contains: "guitar"
    - input: "What are my hobbies?"
      expect:
        response_contains: "guitar"
```

**Loading** (`src/decafclaw/eval/__main__.py:38-51`):
- Lines 39-42: Discover YAML files in directory or use single file
- Lines 47-50: Load via `yaml.safe_load()` and extend `all_cases` list
- Each case dict is passed to `run_test(config, test_case)` at line 379

## 4. Failure Reporting

**Format strings**:
- `runner.py:156`: `f"Expected one of {contains} in response"`
- `runner.py:165`: `f"Response should not contain '{nc}'"`
- `runner.py:169`: `f"Too many tool calls: {tool_calls} > {max_tools}"`
- `runner.py:173`: `f"Too many tool errors: {tool_errors} > {max_errors}"`
- `runner.py:309`: `f"Turn {turn_idx + 1}: {reason}"`
- `runner.py:311` (with errors): `f"\n         Errors: {detail_str}"` (errors truncated to 200 chars per line 129)

**Surfacing** (`src/decafclaw/eval/__main__.py:81-104`):
- Results dict saved to JSON at line 98: `bundle_dir / "results.json"`
- Failures printed to stdout at lines 425-426: `print(f"         {result.get('failure_reason', '')}")`
- Full history appended to result dict if failed (line 339: `result["history"] = history`)

## 5. Existing Tests for Assertions

**No direct unit tests for `_check_assertions` found.**

Tests for tool-choice eval runner (`tests/test_eval_tool_choice_runner.py`) test a *different* eval harness (tool disambiguation, line 1-2: "tool-choice eval runner (#303)"). The main `run_test` and `_check_assertions` functions in `runner.py` have no corresponding test file — testing relies on end-to-end eval runs with real YAML test cases and mock tools.

## 6. Doc State

**File:** `docs/eval-loop.md:52-57`

Current expect field table:

```
| Field | Type | Semantics |
|-------|------|-----------|
| `response_contains` | str / list[str] / `"re:pattern"` | **OR semantics.** Matches if any listed string/regex is in the response. Case-insensitive for non-regex; regex uses `re:` prefix. |
| `response_not_contains` | str / list[str] | **AND semantics.** Fails if any listed string is in the response. Case-insensitive. |
| `max_tool_calls` | int | Fail if tool calls in this turn exceed the bound |
| `max_tool_errors` | int | Fail if tool results containing `[error` in this turn exceed the bound |
```

No other doc page explicitly describes eval assertions; the table at line 52-57 is the authoritative reference.
