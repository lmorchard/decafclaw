# Eval Loop

DecafClaw includes an eval harness for testing prompts and tools with real LLM calls. This lets you iterate on tool descriptions, system prompts, and agent behavior with measurable results.

## Running evals

```bash
uv run python -m decafclaw.eval evals/             # Run all YAML test files
uv run python -m decafclaw.eval evals/memory.yaml   # Run a specific file
uv run python -m decafclaw.eval evals/ --model gemini-2.5-pro  # Override model
uv run python -m decafclaw.eval evals/ --verbose     # Show full responses
```

## Test case format

Tests are YAML files with a list of test cases:

```yaml
- name: "saves to journal when asked"
  prompt: "Remember that my favorite color is blue"
  expect_tool: "vault_journal_append"
  expect_contains: "blue"

- name: "recalls from vault"
  prompt: "What's my favorite color?"
  setup:
    memories:
      - content: "User's favorite color is blue"
        tags: ["preference"]
  expect_contains: "blue"
```

### Test case fields

| Field | Description |
|-------|-------------|
| `name` | Test name (shown in output) |
| `prompt` | User message to send (single-turn) or first message (multi-turn) |
| `turns` | List of `{prompt, expect_contains, expect_tool}` for multi-turn tests |
| `expect_contains` | Response must contain this string (case-insensitive) |
| `expect_tool` | Agent must call this tool |
| `expect_no_tool` | Agent must NOT call this tool |
| `setup.memories` | Pre-populate memory entries before the test |
| `setup.embeddings_fixture` | Path to a pre-built embeddings.db to copy in |
| `allowed_tools` | Restrict which tools the agent can use |

### Multi-turn tests

```yaml
- name: "save then recall"
  turns:
    - prompt: "Remember that my cat's name is Sassy"
      expect_tool: "vault_journal_append"
    - prompt: "What's my cat's name?"
      expect_contains: "Sassy"
```

### Semantic search tests

To test semantic search, provide a pre-built embeddings fixture with distractor entries:

```yaml
- name: "finds relevant memory among distractors"
  prompt: "What's my cat's name?"
  setup:
    memories:
      - content: "User's cat is named Sassy"
        tags: ["pets"]
    embeddings_fixture: "evals/fixtures/cat-facts-embeddings.db"
  allowed_tools: ["vault_search"]
  expect_contains: "Sassy"
```

## Result bundles

Each eval run creates a result bundle at `evals/results/{timestamp}-{model}/`:

```
evals/results/
  20260315-013000-gemini-2.5-flash/
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
- **Start with substring search tests**, then add semantic search tests with distractors.
- **Use `allowed_tools`** to constrain which tools the agent can reach for, testing specific behaviors.
- **Run evals after changing prompts or tool descriptions** — regressions are easy to introduce.
