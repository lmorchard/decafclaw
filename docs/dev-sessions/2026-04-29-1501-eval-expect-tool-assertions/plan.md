# Eval tool-name assertions Implementation Plan

**Goal:** Add `expect_tool`, `expect_no_tool`, and `expect_tool_count_by_name` assertion fields to the eval runner so tests can rigorously assert which tools the agent invoked.

**Approach:** Add a `_collect_tool_names(history) -> list[str]` helper that walks `role: assistant` messages and extracts `function.name` from `tool_calls`. Thread per-turn tool-name lists through `_check_assertions` as a new kwarg. Two phases — phase 1 ships presence/absence assertions with their unit tests and doc rows; phase 2 ships the count assertion with its unit tests and doc row.

**Tech stack:** Python (existing), pytest (existing). No new dependencies.

---

## Phase 1: Presence and absence — `expect_tool` / `expect_no_tool`

Adds the helper, extends `_check_assertions`, ships the two str-or-list assertions with OR/AND semantics, plus their docs.

**Files:**
- Modify: `src/decafclaw/eval/runner.py` — add `_collect_tool_names` helper near `_count_tool_calls` (~line 105); extend `_check_assertions` signature and body; thread per-turn tool names through both call sites.
- Modify: `docs/eval-loop.md` — add two new rows to the expect-fields table at lines 52-57; update the "list uses OR semantics" note at line 59 to mention the new `expect_tool` / `expect_no_tool` pair.
- Create: `tests/test_eval_runner_assertions.py` — direct unit tests for `_check_assertions` covering all existing fields (regression) and the two new fields.

**Key changes:**

New helper in `runner.py` (placed after `_count_tool_calls` and before `_count_tool_errors`):

```python
def _collect_tool_names(history: list) -> list[str]:
    """List tool names called in the (slice of) history, in call order, including duplicates."""
    names = []
    for msg in history:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            name = (call.get("function") or {}).get("name")
            if name:
                names.append(name)
    return names
```

Extended `_check_assertions` signature (`runner.py:133-134`):

```python
def _check_assertions(test_case: dict, response: str, tool_calls: int,
                      tool_errors: int = 0,
                      tool_names: list[str] | None = None) -> tuple[bool, str]:
```

New body branches (after the existing `max_tool_errors` branch at `runner.py:171-173`, before `return True, ""`):

```python
    names = tool_names or []
    called_repr = f"[{', '.join(names)}]" if names else "no tools were called"

    expect_tool = expect.get("expect_tool")
    if expect_tool is not None:
        wanted = [expect_tool] if isinstance(expect_tool, str) else list(expect_tool)
        if not any(w in names for w in wanted):
            return False, f"Expected one of {wanted} to be called, but tools called were {called_repr}"

    expect_no_tool = expect.get("expect_no_tool")
    if expect_no_tool is not None:
        forbidden = [expect_no_tool] if isinstance(expect_no_tool, str) else list(expect_no_tool)
        for f in forbidden:
            if f in names:
                return False, f"Unexpected tool called: '{f}' (called tools: {called_repr})"
```

Call-site updates in `run_test`:
- Line 271 (help/fork-mode early assertion check): pass `tool_names=[]` (no tools ran).
- Line 302-303 (per-turn check): compute `tool_names = _collect_tool_names(history[pre_turn_history_len:])` and pass it as the new kwarg.

Test scaffolding pattern:

```python
def _assistant(tool_calls):
    """Build a synthetic assistant message with the given tool calls."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": f"call_{i}", "function": {"name": n, "arguments": "{}"}}
            for i, n in enumerate(tool_calls)
        ],
    }

def _tool_result(call_id="call_0", content="ok"):
    return {"role": "tool", "tool_call_id": call_id, "content": content}
```

Required unit tests in `tests/test_eval_runner_assertions.py`:
1. `test_expect_tool_string_match_passes` — `expect_tool: "vault_search"` with history containing one `vault_search` call → passes.
2. `test_expect_tool_string_no_match_fails` — `expect_tool: "vault_search"` with history calling only `web_fetch` → fails, message names both expected list and called list.
3. `test_expect_tool_list_or_semantics_passes` — `expect_tool: ["a", "b"]` with `b` called → passes.
4. `test_expect_tool_no_tools_called_fails` — `expect_tool: "x"` with empty history → fails, message says "no tools were called".
5. `test_expect_no_tool_string_blocks_match` — `expect_no_tool: "web_fetch"` with `web_fetch` called → fails.
6. `test_expect_no_tool_string_passes_when_absent` — `expect_no_tool: "web_fetch"` with `vault_search` called → passes.
7. `test_expect_no_tool_list_and_semantics` — `expect_no_tool: [a, b]` with `c` called → passes; with `b` called → fails.
8. Regression tests: one each for `response_contains`, `response_not_contains`, `max_tool_calls`, `max_tool_errors` (so the new kwarg threading doesn't silently break existing fields).

Helpers in tests should call `_collect_tool_names` to convert a synthetic history into the kwarg, exercising the helper too.

Doc rows added to `docs/eval-loop.md:52-57` table (preserving existing rows):

```
| `expect_tool` | str / list[str] | **OR semantics.** Fail if none of the listed tools were called this turn. |
| `expect_no_tool` | str / list[str] | **AND semantics.** Fail if any of the listed tools were called this turn. |
```

Plus, after the table (around line 59), add a one-line note: `Tool-name assertions see only parent-agent tool calls; tools invoked inside child agents (via delegate_task) are not visible.`

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (2260 passed)
- [x] `make check` passes (0 errors / warnings)
- [x] `uv run pytest tests/test_eval_runner_assertions.py -v` — 17 passed; both new fields and the four regression tests covered.

**Verification — manual:**
- [x] Spec compliance review: ✅ all 8 numbered requirements satisfied, no extras.
- [x] Code quality review: ✅ approved, no critical / important issues; helper style matches existing idioms; Phase-2 reusability of `names` / `called_repr` confirmed.

---

## Phase 2: Counts — `expect_tool_count_by_name`

Adds the dict-valued count assertion using the same per-turn `tool_names` already threaded through in phase 1.

**Files:**
- Modify: `src/decafclaw/eval/runner.py` — add one branch in `_check_assertions` after the `expect_no_tool` branch from phase 1.
- Modify: `docs/eval-loop.md` — add the third row to the field table.
- Modify: `tests/test_eval_runner_assertions.py` — add count-assertion tests.

**Key changes:**

New branch (placed after the `expect_no_tool` branch from phase 1, before `return True, ""`):

```python
    count_by_name = expect.get("expect_tool_count_by_name")
    if count_by_name is not None:
        for name, want in count_by_name.items():
            got = sum(1 for n in names if n == name)
            if got != want:
                return False, (
                    f"Tool count mismatch for '{name}': expected {want}, got {got} "
                    f"(called tools: {called_repr})"
                )
```

Required unit tests added to `tests/test_eval_runner_assertions.py`:
1. `test_count_exact_match_passes` — `expect_tool_count_by_name: {a: 2, b: 1}` with history `[a, b, a]` → passes.
2. `test_count_too_few_fails` — `{a: 2}` with history `[a]` → fails, message names tool, expected, got.
3. `test_count_too_many_fails` — `{a: 1}` with history `[a, a]` → fails.
4. `test_count_zero_means_not_called` — `{web_fetch: 0}` with history `[vault_search]` → passes; with `[web_fetch]` → fails.
5. `test_count_unlisted_tool_unconstrained` — `{a: 1}` with history `[a, b, b, b]` → passes (b unconstrained).
6. `test_count_with_other_assertions_combine` — same `expect` block has `expect_tool: x` and `expect_tool_count_by_name: {x: 2}`; both pass when satisfied; one failing fails the whole check.

Doc row added to the field table at `docs/eval-loop.md`:

```
| `expect_tool_count_by_name` | dict[str, int] | Fail if any listed tool's call count this turn does not equal the mapped int. Tools not listed are unconstrained. Count `0` is allowed (overlaps `expect_no_tool`). |
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (2268 passed)
- [x] `make check` passes (0 errors / warnings)
- [x] `uv run pytest tests/test_eval_runner_assertions.py -v` — 25 passed (17 from Phase 1 + 8 new).

**Verification — manual:**
- [x] Spec compliance review: ✅ all requirements satisfied; only the three planned files changed.
- [x] Code quality review: ✅ approved, no critical / important issues; reuses Phase 1 locals cleanly. Minor (deferred, ship-it): `names.count(name)` over `sum(1 for ...)`; first-mismatch-order test.

---

## Out of scope (locked by spec)

- Min/max range form for counts.
- Order or argument assertions.
- Recursing into child-agent histories.
- Touching `__main__.py` or YAML loading.
- Refactoring existing assertion branches.
