# Tool-choice disambiguation eval harness

Tracking issue: #303

## Problem

The codebase has 50+ tools across 14 modules with nontrivial semantic
overlap — `vault_search` vs `conversation_search`, `workspace_read` vs
`vault_read`, `web_fetch` vs the HTTP tool, `delegate_task` vs
`activate_skill`. `CLAUDE.md` already flags tool descriptions as "a
control surface," but there's no quantitative check on whether the
model actually picks the right tool in ambiguous cases. Description
edits land on vibes.

Anthropic's [Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
names "bloated tool sets with ambiguous decision points" as a primary
tool anti-pattern. This eval is the measurement infrastructure that
unblocks the rest of the context-engineering cluster (#298, #302,
#301) — those interventions can't be validated without it.

## Goal

A harness that presents the agent with engineered ambiguity scenarios,
records what tool the model reaches for, and reports overlap so we can
tell which tool descriptions actually compete with each other.

Key design intent: **fast enough to run as a pre-flight check** when
editing a tool description, not slow enough that it only gets run
before releases.

## Architecture

### What we measure: one-turn tool-call intercept

For each case, build a single LLM request mirroring the production
first turn: real system prompt, real tool schema, real descriptions.
Send it. Pull `tool_calls` off the assistant response. Stop — no tool
execution, no second turn, no agent loop iteration. Record the first
tool call's name as the model's pick.

Why this and not the existing full-agent-loop eval style: tool-
selection ambiguity lives in the model's *first* decision. A wrong-
first-call-then-self-correct pattern hides the overlap. Running the
full loop also costs real tool I/O and 5-30× the latency for a signal
we can isolate cleanly with a single call.

If the model emits zero tool calls (chooses to respond in text), record
that as `<no_tool>` — it's a valid eval outcome and worth surfacing.
If the model emits multiple parallel tool calls, the **first** one is
the recorded pick (tool_calls in API responses come in a deterministic
order). The full list is also kept in the per-case record so the
matrix view can highlight "model picked A and B simultaneously" as a
separate signal.

### Tool schema loadout

Every case sees the **fully loaded** schema:

- All discovered skills are activated for the eval session so their
  tools are in scope.
- All tools are forced into the active set (no `tool_search`
  deferral) — the eval measures description overlap under fair
  conditions, not deferral interaction.
- MCP tools are excluded by default (deployment-specific, noise);
  `--include-mcp` opts them in for an MCP-aware run.

This is a deliberate departure from production conditions. The
production deferral / activation logic has its own evals (separately).
Here we want the cleanest possible signal on description overlap.

### Case schema

YAML cases live at `evals/tool_choice/*.yaml`, list-of-mappings:

```yaml
- name: vault-vs-conv-for-decisions
  scenario: "Find the decision we made about the auth middleware last month"
  expected: vault_search
  near_miss: [conversation_search]
  notes: |
    Curated decisions live in the vault, not raw chat logs.
    conversation_search is tempting because "we made" implies
    a discussion happened in conversation.
```

Fields:

- `name` (str, required) — stable id used in reports.
- `scenario` (str, required) — the user message the model sees.
- `expected` (str, required) — the single tool name the case asserts is
  correct.
- `near_miss` (list[str], required, ≥1 entry) — the specific tool(s)
  this case is testing against. Used for the per-pair overlap report.
- `notes` (str, optional) — author's "why this case exists" prose.

Each case must have exactly one correct answer. Cases with multiple
defensible answers don't measure disambiguation cleanly — file them
elsewhere or rephrase the scenario until one tool is right.

### Seed cases

Ship 10–15 canonical pairs in the initial commit, covering the overlap
zones called out in the issue:

- `vault_search` vs `conversation_search`
- `vault_read` vs `workspace_read`
- `web_fetch` vs the HTTP request tool
- `delegate_task` vs `activate_skill`
- a couple of the always-loaded skill tool overlaps the author
  surfaces while writing seeds (e.g. `vault_*` family internal
  ambiguity)

The seed set is the floor, not the ceiling — anyone tightening a
description is expected to add a case or two for the pair they're
adjusting.

### Models

Single model per run by default — pulls from `config.default_model`,
override via `--model NAME`. The eval is deliberately fast because
the iteration loop is "edit description → re-run → look at overlap".

`--models a,b,c` opts into a sweep across multiple models (cartesian
with cases). Output groups results per-model so you can spot "this
description fix helps model X, regresses on model Y" robustness
issues. Sweep is opt-in because per-flight should stay snappy;
robustness audits are a different cadence.

### Output

Default output (post-run summary):

```
PASS  vault-vs-conv-for-decisions
FAIL  vault-read-vs-workspace-read    picked workspace_read; expected vault_read
PASS  ...

Summary: 12/15 passed (80%)

Pair overlap (sorted by overlap %):
  vault_read ↔ workspace_read       2/3 swapped (67%)  ← tighten
  vault_search ↔ conversation_search 1/3 swapped (33%)
  delegate_task ↔ activate_skill     0/2 swapped  (0%)
```

`--matrix` adds a confusion matrix below the summary, surfacing **all**
picks (including outside the declared `near_miss`). Useful for
catching unexpected confusions the case author didn't anticipate.

`--verbose` (matching existing eval CLI) shows the actual tool_calls
list per case rather than just the first pick.

### Code layout

`src/decafclaw/eval/tool_choice/` — submodule alongside the existing
runner, distinct execution shape:

- `__init__.py` — empty package marker.
- `__main__.py` — CLI entry: `python -m decafclaw.eval.tool_choice <path>`.
- `runner.py` — case loading, intercept call, scoring, report.
- `report.py` (or part of runner.py) — formatting helpers; promote to
  its own file only if runner.py exceeds ~300 lines.

`evals/tool_choice/` — YAML cases.

`Makefile` — `eval-tools` target invoking the new CLI on the seed
directory.

The two eval runners share config loading (`from ..config import
load_config`) and the LLM client. Nothing else. Don't force a one-
runner abstraction across operations that genuinely don't share
execution shape.

## CLI

```
python -m decafclaw.eval.tool_choice <path> [options]

  path                    YAML file or directory of YAMLs

  --model NAME            Single model to evaluate (default: config.default_model)
  --models A,B,C          Sweep across multiple models (overrides --model)
  --include-mcp           Include MCP tools in the loadout (off by default)
  --matrix                Show full confusion matrix in addition to pair overlap
  --verbose               Show full tool_calls list per case
  --concurrency N         Parallel cases (default: 4, matching existing eval)
```

## Acceptance criteria

- Running `make eval-tools` against `evals/tool_choice/` exits with a
  non-zero status if any case fails (so it can gate a CI step later if
  desired) and prints the summary + pair-overlap report shown above.
- All seed cases pass on `config.default_model` at merge time. Any
  failures surface a description-tightening opportunity, not a
  scoring bug — fix the description (or the case) before merge.
- `--models a,b,c` produces grouped output per model.
- `--matrix` shows confusion entries beyond the declared `near_miss`.
- `--include-mcp` includes MCP tools when MCP servers are configured;
  silently no-ops when none are.
- Single-LLM-call architecture: no production tool side effects fire
  during a run (verified manually — no files written under workspace,
  no embeddings DB updates, etc.).

## Out of scope

- **CI integration.** Tool descriptions change rarely; gating CI on
  this eval is friction without value. Run by hand when you're
  editing tool descriptions. Re-evaluate when there's a concrete
  reason.
- **Cost / latency budget enforcement.** The eval runs ~15 LLM calls
  by default. Cheap enough that we don't need quotas yet.
- **Failure-reflection LLM judge.** The existing eval's reflect.py
  inspects ambiguous failures; tool-choice failures are unambiguous
  (model picked X instead of Y), no judge needed.
- **Schema for `near_miss` requiring exactly one entry vs. many.**
  Multiple near_miss tools per case are allowed in the schema (e.g.
  for three-way ambiguity); a case with three near_miss tools just
  contributes to three pair-overlap rows in the report.

## Testing

- **Unit tests** for the case loader, scorer, and report formatter
  using fixed in-memory case lists and synthetic intercept results.
  These don't make LLM calls.
- **Integration test** that runs the full CLI against a one-case
  fixture using a faked LLM client (`monkeypatch` the provider to
  return a canned `tool_calls` response). Asserts exit code, stdout
  shape, and per-pair report numbers.
- **No real-LLM CI test.** Cost + flakiness tradeoff isn't worth it
  for what would mostly verify "the API still works." Manual `make
  eval-tools` is the actual validation.

## Files touched

- `src/decafclaw/eval/tool_choice/` — new submodule.
- `evals/tool_choice/` — new YAML case directory with 10-15 seed
  cases.
- `Makefile` — new `eval-tools` target.
- `tests/test_eval_tool_choice.py` (new) — unit + integration tests.
- `docs/eval.md` (new or extend existing eval section) — document the
  CLI, case format, when to add cases.
- `CLAUDE.md` "Tool descriptions are a control surface" bullet —
  extend with a one-sentence pointer at the eval as the validation
  surface for description edits.
