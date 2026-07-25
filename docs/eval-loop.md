# Eval Loop

DecafClaw includes an eval harness for testing prompts and tools with real LLM calls. This lets you iterate on tool descriptions, system prompts, and agent behavior with measurable results.

## Running evals

```bash
make eval                                            # Run all YAML test files (default model)
make eval-history                                    # Print the pass-rate trend over recent runs
uv run python -m decafclaw.eval evals/               # Same, via Python invocation
uv run python -m decafclaw.eval evals/memory.yaml    # Run a specific file
uv run python -m decafclaw.eval evals/ --model gemini-2.5-pro  # Override model
uv run python -m decafclaw.eval evals/ --verbose     # Show truncated response snippets per test
uv run python -m decafclaw.eval evals/ --concurrency 1  # Run tests sequentially (default: 4)
uv run python -m decafclaw.eval --history            # Print history table, no run
uv run python -m decafclaw.eval --history --history-limit 5  # Last 5 runs only
```

## System prompt

Eval turns run with a real system prompt, assembled in `run_test` via
`load_system_prompt(config)` — the same call `decafclaw/__init__.py` makes at
startup.

Assembly happens **after** the per-case sandbox is applied, and the sandbox
clears two separate sources of machine-local state so the result is the
**bundled tier only** — bundled `SOUL.md` + `AGENT.md`, the `<skill_catalog>`,
and the bodies of always-loaded bundled skills:

| sandbox field | what it excludes |
|---|---|
| `agent.data_home` → tmp | per-agent prompt overrides under `data/{agent_id}/`, including `USER.md` |
| `extra_skill_paths` → `[]` | skills from configured external dirs (e.g. `~/.agents/skills`) |

The second one is easy to miss: `load_system_prompt` calls `discover_skills`,
and `extra_skill_paths` entries live *outside* `data_home`, so the tmp
redirect alone doesn't reach them. Left populated, a developer's personal
skills land in the `<skill_catalog>` of every eval prompt — measured at 113
extra skills against 12 bundled, a third of the prompt text
([#670](https://github.com/lmorchard/decafclaw/issues/670)).

Both are applied last in `_build_test_config`, so a case's
`setup.config_overrides` cannot opt itself back into either.

Net effect: eval results are reproducible across machines and in CI instead of
drifting with local agent state.

The tool-choice eval assembles the same prompt (see
[How it works](#how-it-works) below), so the two harnesses agree on this axis.

> **History discontinuity.** Runs before 2026-07-24 were measured with an
> **empty** system prompt — the eval CLI never went through the startup path
> that assembles it, so every case ran without SOUL.md, AGENT.md, the skill
> catalog, or any always-loaded skill body ([#670](https://github.com/lmorchard/decafclaw/issues/670)).
> Pass rates from before that date are not comparable to later ones, and cost
> per case roughly doubled once the prompt was actually being sent.

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
| `tests` | Failure-mode axis tag(s) for the scorecard — a string or list of strings. See [Axis tagging & the failure-mode scorecard](#axis-tagging--the-failure-mode-scorecard) |

### Setup fields

| Field | Description |
|-------|-------------|
| `setup.skills` | List of skill names to pre-activate before the test case (once, shared across turns) |
| `setup.memories` | List of `{content, tags}`; seeded as journal entries (and indexed for semantic search if the strategy is `semantic`) |
| `setup.workspace_files` | Map of `{relative_path: content}` to seed into the test workspace. Paths are sandboxed — no `..` escape |
| `setup.conversation_history` | List of message dicts (`{role, content, ...}`) written to `{workspace}/conversations/eval/archive.jsonl` *and* pre-loaded into the in-memory history before the first turn. Use to test `conversation_search` / `conversation_compact` without organically building up history. Each entry must have a `role`; timestamps are auto-stamped if missing. |
| `setup.embeddings_fixture` | Path to a pre-built embeddings.db to copy into the workspace |
| `setup.auto_confirm` | Default `true`. Auto-approve (or deny) all tool confirmation requests (shell, email, `EndTurnConfirm`, etc.) |
| `setup.config_overrides` | Map of dotted config paths to values, applied to the resolved `Config` for this test only. See [Config overrides](#config-overrides) |

**This table is the accepted set.** An unknown `setup.*` key raises and fails that case, listing the valid keys — a typo like `workspace_file` (missing the `s`) would otherwise return the `.get()` default, silently skip its fixture, and leave the case failing for a confusing reason or passing for the wrong one.

The allowlist (`_KNOWN_SETUP_KEYS` in `eval/runner.py`) is checked against this table by a unit test, so **adding a setup field means editing both** — the code and this table. Forgetting either fails `make test`.

### Config overrides

`setup.config_overrides` maps dotted paths to values and is applied to the resolved `Config` via recursive `dataclasses.replace`:

```yaml
- name: "recalls a vault page in headlines mode"
  setup:
    config_overrides:
      vault_retrieval.mode: headlines
      agent.max_tool_iterations: 3
      cleanup.enabled: false
```

Any field on `Config` or any of its nested sections is reachable. The runner does not enumerate the accepted paths, so a newly added config field works on arrival without touching `eval/runner.py`.

Notes:

- **Typos raise.** An unknown path fails that test with the available field names listed. Silently ignoring it would produce a green test measuring the wrong config — the whole point of the mechanism is to make the config under test explicit.
- **`config_overrides` is validated on presence, not truthiness.** A bare `config_overrides:` parses to YAML null, and `[]` / `0` / `""` are falsy too; all of them raise rather than quietly doing nothing. Write `config_overrides: {}` if you really mean "no overrides". (A bare `setup:` *is* tolerated as an empty block — an empty setup section is a normal authoring state, whereas an empty `config_overrides` block means the author intended overrides and lost them.)
- **Nesting comes from dots in the key, never from nested YAML.** A dict on the value side is a literal value, so plain-dict fields (`skills`, `providers`, `model_configs`) can be set wholesale.
- **The sandbox wins.** `agent.data_home` and `agent.id` are applied after your overrides, so a case cannot redirect itself out of its temp directory.
- Two earlier bespoke keys, `setup.max_tool_iterations` and `setup.reflection_enabled`, were folded into this mechanism. They now raise with a migration hint rather than being silently ignored.

Common paths:

| Path | Why |
|---|---|
| `reflection.enabled: false` | Required with `expect_no_tool` / tight `max_tool_calls` — reflection's judge can trigger retries that invoke unexpected tools ([#534](https://github.com/lmorchard/decafclaw/issues/534)) |
| `agent.max_tool_iterations` | Force budget exhaustion, e.g. the grace-turn eval ([#448](https://github.com/lmorchard/decafclaw/issues/448)) |
| `vault_retrieval.mode` | Exercise `always` / `headlines` / `on_demand` retrieval injection |

### Expect assertions

| Field | Type | Semantics |
|-------|------|-----------|
| `response_contains` | str / list[str] / `"re:pattern"` | **OR semantics.** Matches if any listed string/regex is in the response. Case-insensitive for non-regex; regex uses `re:` prefix. |
| `response_contains_all` | str / list[str] / `"re:pattern"` | **AND semantics.** Fails if any listed string/regex is missing from the response. Same item handling as `response_contains` (case-insensitive substring or `re:` regex). Use this when the test name implies "and" — `response_contains: [a, b]` would pass with only `a`. |
| `response_not_contains` | str / list[str] | **AND semantics.** Fails if any listed string is in the response. Case-insensitive. |
| `max_tool_calls` | int | Fail if tool calls in this turn exceed the bound |
| `max_tool_errors` | int | Fail if tool results containing `[error` in this turn exceed the bound |
| `expect_tool` | str / list[str] | **OR semantics.** Fail if none of the listed tools were called this turn. |
| `expect_no_tool` | str / list[str] | **AND semantics.** Fail if any of the listed tools were called this turn. |
| `expect_tool_count_by_name` | dict[str, int] | Fail if any listed tool's call count this turn does not equal the mapped int. Tools not listed are unconstrained. Count `0` is allowed (overlaps `expect_no_tool`). |
| `expect_tool_args` | list[{tool, args}] (or single dict) | The only argument-level assertion. Each spec passes if at least one call to `tool` this turn has matching values for **every** key in `args` (subset match — other args ignored). Use to disambiguate same-tool variants, e.g. `canvas_new_tab` with `widget_type: map` vs `iframe_sandbox`. |

Note that `response_contains` with a list uses OR semantics — to require several strings, use `response_contains_all`, a single `re:(?s).*foo.*bar.*` regex, or multiple test cases.

Tool-name assertions see only parent-agent tool calls; tools invoked inside child agents (via `delegate_task`) are not visible.

The eval harness wires a `ConversationManager` onto the parent context (#536) so `delegate_task` executes end-to-end — the child agent runs a real turn and returns its result to the parent. Confirmations that route through the manager's typed path (child-side tool confirmations) are auto-resolved per `setup.auto_confirm`, mirroring the legacy event-bus shim's behavior for parent-side tools. Tests that only care about the parent's tool-choice angle don't need any special setup — `expect_tool: delegate_task` works as before; the difference is that the delegation no longer surfaces a `[error: requires a ConversationManager]` tool result.

### Post-turn workspace assertions

`expect_workspace` sits at the test-case top level (parallel to `setup` / `expect`) and runs once at the end of the test, after all turns complete. Useful for tests that need to verify the agent's *side effects* rather than its response text.

| Field | Type | Semantics |
|-------|------|-----------|
| `workspace_files` | dict[str, str] | `{rel_path: content_match}`. Both existence AND content. Plain strings: case-insensitive substring. `re:` prefix opts into regex (case-insensitive, `re.DOTALL` so `.` matches newlines). |
| `workspace_file_exists` | list[str] | Each path must exist (existence only, no content check). |
| `workspace_file_absent` | list[str] | Each path must NOT exist. Useful for delete/move tests. |

All paths are relative to `config.workspace_path`. Absolute paths and `..` escapes raise `ValueError` — symmetric with `setup.workspace_files`.

Example: after the agent runs a section edit, verify other sections are untouched.

```yaml
- name: "section edit leaves other sections intact"
  setup:
    workspace_files:
      "page.md": "## A\n\noriginal a\n\n## B\n\noriginal b\n"
  input: "Update section A of page.md to say 'updated a'"
  expect:
    expect_tool: vault_section
  expect_workspace:
    workspace_files:
      "page.md": "re:## A.+updated a.+## B.+original b"
```

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

## Per-turn diagnostics

Each turn's result (each entry in `result["turns"]` for multi-turn tests, or the top-level `result` for single-turn tests) carries a `"diagnostics"` block built by `decafclaw.eval.diagnostics.build_turn_diagnostics`. It reuses the per-conversation context sidecar the agent already writes on turn-exit (see [Context inspection](context-composer.md#context-inspection)) — no separate recompute — plus a few fields derived from the turn's own history slice:

| Key | Source | Notes |
|-----|--------|-------|
| `tokens_by_section` | sidecar | `{source: tokens_estimated}` per context source (`system`, `tools`, `retrieved_context`, ...) |
| `total_tokens_estimated` / `total_tokens_actual` | sidecar | Whole-turn totals |
| `context_window_size` / `compaction_threshold` | sidecar | Model's configured limits |
| `active_tools` / `deferred_tools` | sidecar | `items_included` / `items_truncated` for the `tools` source |
| `retrieved_candidates` | sidecar | Memory-retrieval candidates with `file_path`, `composite_score`, `similarity`, `recency`, `importance` |
| `files_read` | derived | Vault/workspace paths read via tool calls this turn |
| `files_cited` | derived | Of `files_read` plus retrieved-candidate paths, which ones the response text actually references — case-insensitive substring match on the full path, basename, or stem, plus any `[[wiki link]]` mention in the response |
| `tool_calls` | derived | `{names: [...], count: N}` for this turn |

A missing or unreadable sidecar degrades to the derived fields only (`build_turn_diagnostics` never raises); sidecar-sourced fields come back `None`/empty.

This block is what turns a bare pass/fail into an actionable failure diagnosis, roughly along four lines (independent of, though often correlated with, the [axis tags](#axis-tagging--the-failure-mode-scorecard) above):

- **retrieval** — check `retrieved_candidates`: was the right page even a candidate, and how did it score?
- **routing** — check `tool_calls.names` and `files_read`: did the agent reach for the right tool at all?
- **answer** — check `files_cited` vs `files_read`: did the agent read the right thing but fail to ground the answer in it (or fabricate)?
- **bloat** — check `tokens_by_section` and `active_tools`/`deferred_tools`: is the turn drowning in context it didn't need?

Note what's *not* here: no per-tool-call durations (deferred — the sidecar doesn't currently time individual tool calls).

`--verbose` prints a compact summary of this block after each test's response snippet:

```
[3/12] retrieves the right page for a vague query ....... PASS  (4.2s, 3100 tokens, 1 tools)
         Response: Per the migration-plan page, the cutover is scheduled for...
         Tokens: system=2400  tools=1800  retrieved_context=900  (active=6, deferred=31)
         Candidates: agent/pages/migration-plan.md:0.87, agent/pages/rollout-notes.md:0.41
         Read: ['agent/pages/migration-plan.md']  Cited: ['agent/pages/migration-plan.md']  Tools: ['vault_read']
```

## Axis tagging & the failure-mode scorecard

A test case can tag itself with one or more failure-mode axes via the top-level `tests:` key — a string for a single axis, or a list for multiple:

```yaml
- name: "retrieves the right page for a vague query"
  input: "what did I write about the migration plan?"
  tests: retrieval
  expect:
    response_contains: "migration"

- name: "routes to the right tool and answers correctly"
  input: "what's on my calendar tomorrow?"
  tests: [routing, answer_quality]
  expect:
    max_tool_calls: 3
```

The four canonical axes (`decafclaw.eval.diagnostics.CANONICAL_AXES`) are:

| Axis | Covers |
|------|--------|
| `retrieval` | Finding/recalling the right memory, page, or context |
| `routing` | Picking the right tool/skill for the task |
| `answer_quality` | Correctness and completeness of the final response |
| `workflow_discipline` | Following multi-step procedures, confirmations, and guardrails correctly |

A case with no `tests:` key falls into the `untagged` bucket rather than being dropped from the scorecard. An axis value outside the canonical set raises `ValueError` and fails the run rather than silently vanishing.

### Behavioral suites (#528, #531)

Seven single-axis suites exercise the four canonical axes end-to-end with real LLM turns:

| Suite | Axis | Cases |
|-------|------|-------|
| `evals/vault_answering.yaml` | `retrieval` | 5 |
| `evals/tool_routing.yaml` | `routing` | 5 |
| `evals/source_grounding.yaml` | `answer_quality` | 5 |
| `evals/context_pressure.yaml` | `answer_quality` | 4 |
| `evals/clarification.yaml` | `workflow_discipline` | 4 |
| `evals/abort_recovery.yaml` | `workflow_discipline` | 4 |
| `evals/over_ceremony.yaml` | `workflow_discipline` | 5 |

`context_pressure.yaml` and `clarification.yaml` each landed at 4 cases rather than the 5 originally targeted — both suites' authoring comments document a fifth case that was tried against real LLM calls and dropped because it surfaced a genuine behavioral gap (not an eval-design artifact), per the no-silently-weakened-assertions rule. Existing pre-#528 suites (`evals/memory.yaml`, `evals/conversation.yaml`, etc.) are not retroactively axis-tagged; retagging them is a deferred follow-up (see the dev-session notes for #528/#531).

After each run, `results["summary"]["by_axis"]` in the result bundle's `results.json` maps each axis (plus `untagged`) to `{total, passed, failed, pass_rate}`. `make eval` also prints a scorecard to the console:

```
By axis (failure-mode scorecard):
  answer_quality         8/10  (80%)
  retrieval              5/6  (83%)
  routing                9/9  (100%)
  untagged               12/15  (80%)
```

A case tagged with multiple axes counts once toward *each* of its axes, so axis totals summed across the table can exceed the total test count — that's expected, not a bug.

Per-axis pass-rate trend over time (mirroring [Pass-rate history](#pass-rate-history) but broken out by axis) is a deferred follow-up, not yet implemented.

## Pass-rate history

After each `make eval` run, a one-line summary is appended to `evals/history.jsonl`. Both it and the detail bundles in `evals/results/` are gitignored, so the trend lives on whichever machine ran the suite. The record includes timestamp, model, total / passed / failed counts, pass-rate, duration, total tokens, and per-file pass/total breakdown.

View the trend with `make eval-history`:

```
Timestamp          Model                       Pass /  Total    Rate       Δ   Duration    Tokens
-------------------------------------------------------------------------------------------------
2026-05-16-1130    vertex-gemini-flash             26 /    30   86.7%     --        370s     1.05M
2026-05-16-1256    vertex-gemini-flash             25 /    29   86.2%   -0.5%        996s    1.32M
2026-05-16-1913    vertex-gemini-flash             41 /    42   97.6%  +11.4%        403s    1.22M
```

The Δ column is pass-rate delta from the previous row. Useful for spotting regressions when a tool-description change knocks the rate down.

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
2. Sends one chat completion with the system prompt + the case's user message + the full tool schema. Same `load_system_prompt(config)` assembly the full-agent runner uses (see [System prompt](#system-prompt)).
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
