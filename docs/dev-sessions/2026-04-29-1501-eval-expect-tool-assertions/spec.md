# Eval tool-name assertions Spec

**Goal:** Let eval YAML files assert *which* tools the agent called (or didn't), and how many times — strengthening tool-deferral evals and replacing today's indirect "tool count + response text" approximation.

**Source:** [#349](https://github.com/lmorchard/decafclaw/issues/349) (split from #240; missing-feature flagged in audit doc on #338).

## Current state

- `_check_assertions` (`src/decafclaw/eval/runner.py:133-175`) inspects `expect.response_contains`, `expect.response_not_contains`, `expect.max_tool_calls`, `expect.max_tool_errors`. It receives counts only — no tool-name visibility.
- Per-turn slicing already works: `run_test` takes a snapshot at `pre_turn_history_len` (`runner.py:246`) and uses it to compute deltas (lines 290, 302) and to slice errors (line 307). Same approach can deliver per-turn tool names.
- Tool-call data lives on `assistant.tool_calls[i].function.name` (research §2 — `agent.py:1133-1147`, `openai_compat.py:113-123`). `role: tool` messages only have `tool_call_id`/`content`, so a name-extraction helper must walk **assistant** messages, not tool messages.
- Failure messages flow through `runner.py:309-311`: `f"Turn {turn_idx + 1}: {reason}"`, optionally with appended error details.
- No unit tests exist for `_check_assertions` today (research §5). `tests/test_eval_tool_choice_runner.py` covers a different harness.
- `docs/eval-loop.md:52-57` has the canonical `expect` field table — three rows to add.

## Desired end state

Three new fields, all nested inside `expect:`:

```yaml
expect:
  expect_tool: vault_search                 # str OR list[str]; OR semantics
  expect_no_tool: [web_fetch, shell_exec]   # str OR list[str]; AND semantics
  expect_tool_count_by_name:                # dict[str, int]; exact match
    vault_search: 2
    web_fetch: 0
```

**Semantics**

- `expect_tool`: fail if **none** of the listed tools were called this turn. Single string is treated as a one-element list.
- `expect_no_tool`: fail if **any** of the listed tools were called this turn. Single string is treated as a one-element list.
- `expect_tool_count_by_name`: fail if any listed tool's call count this turn does not equal its mapped int. Tools not listed are unconstrained. Count of 0 is allowed (overlaps `expect_no_tool` — tolerant input).

**Scope** (matches existing `max_tool_calls` semantics): parent-agent tool calls only. Tool calls inside child agents spawned by `delegate_task` are not visible here — they live in the child's separate history. Documented in `docs/eval-loop.md`.

**Failure messages** are specific and list what was actually called this turn:

- `expect_tool`: `"expected one of [vault_search] but tools called were [web_fetch]"` (or `"... but no tools were called"` if empty)
- `expect_no_tool`: `"unexpected tool called: web_fetch (called tools: [web_fetch, vault_read])"`
- `expect_tool_count_by_name`: `"tool count mismatch for 'vault_search': expected 2, got 1 (called tools: [vault_search, web_fetch])"`

These flow through the existing `Turn N: {reason}` wrapper unchanged.

**Doc update.** `docs/eval-loop.md:52-57` table gets three new rows describing the assertions.

## Design decisions

- **Decision:** Field names match the issue text verbatim (`expect_tool`, `expect_no_tool`, `expect_tool_count_by_name`).
  - **Why:** Issue is explicit; the `expect_` prefix on tool-name assertions reads naturally as "I expect this tool to be called" and disambiguates from response assertions. Honors author intent.
  - **Rejected:** `tool_called` / `tool_not_called` / `tool_call_counts` (drop redundant prefix). Read more like state assertions than expectations and didn't match the issue.

- **Decision:** `expect_tool` and `expect_no_tool` accept str OR list[str], with OR / AND semantics respectively.
  - **Why:** Symmetry with `response_contains` (str/list, OR) and `response_not_contains` (str/list, AND) — users already know this shape. List-of-one collapses to the singular form, so we never lose expressiveness.
  - **Rejected:** Singular-only (per issue text). Forces splitting into separate eval cases or waiting on count-by-name for any "either of these" assertion.
  - **Rejected:** AND semantics on `expect_tool` (every listed tool must be called). Different from `response_contains` precedent; `expect_tool_count_by_name: {a: 1, b: 1}` covers this case once it lands.

- **Decision:** `expect_tool_count_by_name` is exact-match only (`{name: int}`), no min/max range form yet.
  - **Why:** Issue's acceptance criteria explicitly defer min/max. Exact form is sufficient for the tool-deferral evals that prompted this work. Polymorphic value (int vs `{min, max}`) opens a YAML-schema design question better answered when a real test needs it.
  - **Rejected:** Polymorphic value now. Premature — no concrete use case yet, and `count: 0` already covers the most common range case ("must not be called").

- **Decision:** Count of 0 is allowed in `expect_tool_count_by_name`, even though it overlaps with `expect_no_tool`.
  - **Why:** Tolerant input. Users pick whichever reads better in context. No validation cost.
  - **Rejected:** Reject 0 with an error. Adds friction without preventing any real bug.

- **Decision:** Tool-name extraction walks assistant messages and reads `function.name` from `tool_calls`.
  - **Why:** That's where names live (research §2). `role: tool` messages only carry `tool_call_id`.
  - **Rejected:** Reverse-resolving from `tool_call_id` back to the assistant message. More work, no benefit.

- **Decision:** Scope is parent-level only — child-agent tool calls inside `delegate_task` are invisible to these assertions.
  - **Why:** Matches existing `max_tool_calls` semantics. Child-agent histories are separate; surfacing them would require deeper plumbing and wasn't requested. Documented in `eval-loop.md`.
  - **Rejected:** Recursing into child histories. Out of scope; can be added later if needed.

- **Decision:** Failure messages list the called-tools set so the reader sees what *did* happen.
  - **Why:** Acceptance criterion ("Test failure messages are specific"). Mirrors `response_contains` failure that quotes the expected list.
  - **Rejected:** Bare `"expected_tool not called"`. Less actionable; user has to dig into history.

## Patterns to follow

- **Per-turn delta extraction:** mirror the snapshot-then-subtract pattern at `runner.py:246-302`. Add a `_collect_tool_names(history) -> list[str]` helper next to `_count_tool_calls` (`runner.py:103-105`); slice with `pre_turn_history_len` like the existing error collector at `runner.py:120-130`.
- **Helper signatures:** keep top-level module-private functions returning plain types (cf. `_count_tool_calls`, `_collect_tool_errors`). New helper returns `list[str]` (one entry per tool call, in call order, including duplicates).
- **`_check_assertions` extension:** add one new keyword argument carrying the per-turn tool-name list. Existing call sites at `runner.py:271, 303` get the new arg threaded through.
- **Failure-message style:** match the existing format strings at `runner.py:156, 165, 169, 173` — short, parenthetical context, no log-level prefixes.
- **Test pattern:** add `tests/test_eval_runner_assertions.py` (or similar) that calls `_check_assertions` directly with synthetic histories. Follow the pattern in `tests/test_eval_tool_choice_runner.py` for synthetic-message construction. No end-to-end YAML loading needed.

## What we're NOT doing

- **No min/max range form** for `expect_tool_count_by_name`. Defer until a concrete eval needs it.
- **No order assertions** (e.g. "tool A before tool B"). Out of scope.
- **No call-argument assertions** (e.g. "vault_search called with `query: foo`"). Out of scope.
- **No recursion into child-agent histories** from `delegate_task`. Parent-level only.
- **No new MCP-tool special-casing.** MCP tool names (`mcp__server__tool`) are matched as exact strings.
- **No refactor of existing assertions** (`response_contains`, `max_tool_calls`, etc.). Touch them only if the new arg threading requires it.
- **No changes to eval discovery / loading** (`__main__.py`). All changes are inside `runner.py` plus tests plus docs.
- **No new YAML schema validation pass.** Tolerant input — bad shapes (e.g. `expect_tool_count_by_name: 5`) raise a TypeError at runtime; that's fine.

## Open questions

None blocking the plan.
