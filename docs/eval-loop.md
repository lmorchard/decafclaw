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
| `setup.skills` | List of skill names to pre-activate before the turn |
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

Note that `response_contains` with a list uses OR semantics — to require several strings, use a single `re:(?s).*foo.*bar.*` regex, or use multiple assertions by splitting into separate test cases.

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

## Tips

- **Tool descriptions are the primary control surface.** Wording changes measurably affect behavior. Use evals to validate.
- **Bound every test with `max_tool_calls` and `max_tool_errors`.** Unbounded tests pass silently on agent-loop regressions.
- **If a test targets a specific tool, make sure the agent actually has to call it.** Proactive context injection can make tests pass without exercising the tool they claim to exercise. Consider `allowed_tools: [the_tool]` or distractor-heavy fixtures.
- **Start with substring search tests**, then add semantic search tests with distractors.
- **Use `allowed_tools`** to constrain which tools the agent can reach for, testing specific behaviors.
- **Run evals after changing prompts or tool descriptions** — regressions are easy to introduce.
