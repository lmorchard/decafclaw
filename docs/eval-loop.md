# Eval Loop

DecafClaw includes an eval harness for testing prompts and tools with real LLM calls. This lets you iterate on tool descriptions, system prompts, and agent behavior with measurable results.

## Running evals

```bash
make eval                                            # Run all YAML test files (default model)
uv run python -m decafclaw.eval evals/               # Same, via Python invocation
uv run python -m decafclaw.eval evals/memory.yaml    # Run a specific file
uv run python -m decafclaw.eval evals/ --model gemini-2.5-pro  # Override model
uv run python -m decafclaw.eval evals/ --verbose     # Show truncated response snippets per test
uv run python -m decafclaw.eval evals/ --concurrency 1  # Run tests sequentially (default: 4)
```

## Test case format

Tests are YAML files with a list of test cases. Single-turn form:

```yaml
- name: "saves to journal when asked"
  input: "Remember that my favorite color is blue"
  expect:
    response_contains: "blue"
    max_tool_calls: 5
    max_tool_errors: 0
```

### Test case fields

| Field | Description |
|-------|-------------|
| `name` | Test name (shown in output) |
| `input` | User message to send (single-turn) |
| `turns` | List of `{input, expect}` for multi-turn tests (see below) |
| `setup` | Fixture setup — see [Setup fields](#setup-fields) |
| `expect` | Assertions to check — see [Expect assertions](#expect-assertions) |
| `allowed_tools` | List of tool names; the agent can only call these. Unlisted tool calls return an error. |

### Setup fields

| Field | Description |
|-------|-------------|
| `setup.skills` | List of skill names to pre-activate before the test case (once, shared across turns) |
| `setup.memories` | List of `{content, tags}`; seeded as journal entries (and indexed for semantic search if the strategy is `semantic`) |
| `setup.workspace_files` | Map of `{relative_path: content}` to seed into the test workspace. Paths are sandboxed — no `..` escape |
| `setup.embeddings_fixture` | Path to a pre-built embeddings.db to copy into the workspace |
| `setup.auto_confirm` | Default `true`. Auto-approve (or deny) all tool confirmation requests (shell, email, `EndTurnConfirm`, etc.) |

### Expect assertions

| Field | Type | Semantics |
|-------|------|-----------|
| `response_contains` | str / list[str] / `"re:pattern"` | **OR semantics.** Matches if any listed string/regex is in the response. Case-insensitive for non-regex; regex uses `re:` prefix. |
| `response_not_contains` | str / list[str] | **AND semantics.** Fails if any listed string is in the response. Case-insensitive. |
| `max_tool_calls` | int | Fail if tool calls in this turn exceed the bound |
| `max_tool_errors` | int | Fail if tool results containing `[error` in this turn exceed the bound |
| `expect_tool` | str / list[str] | **OR semantics.** Fail if none of the listed tools were called this turn. |
| `expect_no_tool` | str / list[str] | **AND semantics.** Fail if any of the listed tools were called this turn. |
| `expect_tool_count_by_name` | dict[str, int] | Fail if any listed tool's call count this turn does not equal the mapped int. Tools not listed are unconstrained. Count `0` is allowed (overlaps `expect_no_tool`). |

Note that `response_contains` with a list uses OR semantics — to require several strings, use a single `re:(?s).*foo.*bar.*` regex, or use multiple assertions by splitting into separate test cases.

Tool-name assertions see only parent-agent tool calls; tools invoked inside child agents (via `delegate_task`) are not visible.

### Multi-turn tests

```yaml
- name: "save then recall"
  turns:
    - input: "Remember that my cat's name is Sassy"
      expect:
        response_contains: "Sassy"
    - input: "What's my cat's name?"
      expect:
        response_contains: "Sassy"
```

Each turn has its own `expect`; all must pass for the test to pass. History is shared across turns within a single test.

### Semantic search tests

To test semantic search, provide a pre-built embeddings fixture with distractor entries:

```yaml
- name: "finds relevant memory among distractors"
  setup:
    memories:
      - content: "User's cat is named Sassy"
        tags: ["pets"]
    embeddings_fixture: "evals/fixtures/cat-facts-embeddings.db"
  allowed_tools: ["vault_search"]
  input: "What's my cat's name?"
  expect:
    response_contains: "Sassy"
```

**Important:** proactive memory retrieval can inject seeded memories directly into context without the agent ever calling `vault_search`. Tests that intend to exercise search specifically should be designed so the answer isn't reachable from proactively-retrieved context (e.g. by using enough distractors, or relying on the embeddings fixture alone and not seeding memories that will surface via the retrieval window).

### User-invokable commands

The runner dispatches `/foo` and `!foo` commands before running the agent, so user-invokable skills can be tested end-to-end:

```yaml
- name: "postmortem produces a report"
  setup:
    skills: [postmortem]
  input: "/postmortem on this test pattern"
  expect:
    response_contains: "## Anomaly"
```

## Result bundles

Each eval run creates a result bundle at `evals/results/{timestamp}-{model}/`:

```
evals/results/
  2026-04-24-1015-default/
    results.json              # Full results with per-test details
    reflections/              # LLM-generated analysis of failures
      test-name.md
```

## Failure reflection

When tests fail, the eval runner sends each failure to a "judge" model for analysis. The judge explains why the test likely failed and suggests improvements to tool descriptions or prompts. Reflections are saved as markdown in the bundle.

```bash
uv run python -m decafclaw.eval evals/ --judge-model gemini-2.5-pro
```

## Building fixtures

```bash
make build-eval-fixtures    # Rebuild embedding fixtures from source data
```

The `cat-facts-embeddings.db` fixture contains ~97 cat facts as a distractor noise floor for semantic search tests.

## Tool-choice disambiguation eval

A separate, lighter-weight eval surface targeted at one specific question: **when two tool descriptions overlap, which one does the model actually pick?** Where the main eval loop runs full agent turns, this one makes a single LLM call per case and intercepts the first `tool_calls` entry — fast enough to run as a pre-flight check while you're editing a tool description.

```bash
make eval-tools                                            # Run against the default model
uv run python -m decafclaw.eval.tool_choice evals/tool_choice/  # Same
uv run python -m decafclaw.eval.tool_choice evals/tool_choice/ --model gemini-2.5-pro
uv run python -m decafclaw.eval.tool_choice evals/tool_choice/ --models gemini-2.5-flash,gpt-5
uv run python -m decafclaw.eval.tool_choice evals/tool_choice/ --matrix       # Add full confusion matrix
uv run python -m decafclaw.eval.tool_choice evals/tool_choice/ --include-mcp  # MCP tools too
```

### How it works

For each YAML case, the runner:

1. Builds the **fully-loaded** tool schema (every core tool + every discovered skill's `tools.py` exports; MCP off by default). No deferral, no activation gating — the eval measures description overlap under fair conditions.
2. Sends one chat completion with the production system prompt + the case's user message + the full tool schema.
3. Captures the first tool name from `tool_calls` (or `<no_tool>` if the model emits text only). No tool execution, no agent loop iteration — the overlap signal we care about lives in the *first* decision.
4. Aggregates results into a per-pair overlap report: for each declared `(expected, near_miss)` pair, what fraction of cases swapped to the near-miss?

### Case YAML format

Cases live under `evals/tool_choice/`:

```yaml
- name: vault-vs-conv-decisions
  scenario: "What did we decide about the auth middleware rewrite last month?"
  expected: vault_search
  near_miss: [conversation_search]
  notes: |
    Curated decisions live in the vault as pages — that's where past
    architectural choices get written down. conversation_search is
    tempting because "we decided" sounds like dialog, but raw chat is
    rarely the source of truth for resolved questions.
```

Required fields: `name`, `scenario`, `expected`, `near_miss` (list, ≥1 entry). Optional: `notes` — author's "why this case exists" prose, which matters when a case fails months later and someone wonders what the test is about.

Each case must have **exactly one** correct answer. If two answers are defensible, rephrase the scenario until one is right — cases with multiple valid answers don't measure disambiguation cleanly.

### When to add a case

Whenever you tighten a tool description to disambiguate it from another, add a case for the pair you're adjusting. The seed set ships ~12 canonical cases covering vault/workspace/conversation overlaps, web_fetch vs http_request, delegate_task vs activate_skill, plus a `<no_tool>` negative-control case (purely conversational reply, no tool needed — guards against tool-happy bias inflating pass rates elsewhere).

### Output

Default output prints per-case PASS/FAIL, a summary, and a sorted pair-overlap table. Pairs at ≥50% swap rate are flagged with `← tighten`. `--matrix` adds a confusion matrix surfacing **all** picks (including unexpected confusions outside the declared `near_miss`).

```
PASS  vault-vs-conv-decisions
FAIL  vault-read-vs-workspace-read    picked workspace_read; expected vault_read

Summary: 11/12 passed (92%)

Pair overlap (sorted by overlap %):
  vault_read ↔ workspace_read       1/1 swapped (100%)  ← tighten
  vault_search ↔ conversation_search 0/2 swapped (0%)
  ...
```

A failing case usually means a description-tightening opportunity, not an eval bug — fix the offending description (or the case) and re-run.

## Tips

- **Tool descriptions are the primary control surface.** Wording changes measurably affect behavior. Use evals to validate. Run `make eval-tools` whenever you edit a tool description.
- **Bound every test with `max_tool_calls` and `max_tool_errors`.** Unbounded tests pass silently on agent-loop regressions.
- **If a test targets a specific tool, make sure the agent actually has to call it.** Proactive context injection can make tests pass without exercising the tool they claim to exercise. Consider `allowed_tools: [the_tool]` or distractor-heavy fixtures.
- **Start with substring search tests**, then add semantic search tests with distractors.
- **Use `allowed_tools`** to constrain which tools the agent can reach for, testing specific behaviors.
- **Run evals after changing prompts or tool descriptions** — regressions are easy to introduce.
