# Workflows

Workflows are declarative multi-phase agent procedures authored as
`kind: workflow` skills. The workflow engine drives a state machine,
constrains tool catalogs per phase, applies context-composer
overrides per phase, dispatches subagent phases to isolated children,
and routes edges via the dynamically-generated `phase_advance` tool.

See [`spec`](dev-sessions/2026-05-19-2121-workflow-engine/spec.md) for
the design rationale and [`research_brief`](../src/decafclaw/skills/workflow_demo/)
for a working example.

## Authoring a workflow

A workflow lives in a single skill directory:

```
skills/{name}/
  SKILL.md            # workflow shell
  phases/
    phase_a.md
    phase_b.md
    ...
```

### SKILL.md

Sets `kind: workflow` and points at the initial phase. The body is the
optional user-invocable command handler text.

```yaml
---
name: my_workflow
description: Short description.
kind: workflow
user-invocable: true
workflow:
  initial-phase: gather
---

Optional command-handler prose. Use `$ARGUMENTS` for command args.
```

### Phase files

Each `phases/<stem>.md` defines a phase with `id: <stem>`. Frontmatter
holds wiring; body holds the prompt.

#### Inline phase (default)

```yaml
---
kind: inline                   # default; can omit
tools: [vault_read, vault_write]
context-profile:
  memory-retrieval: off        # inherit | off
  notes-injection: inherit     # inherit | off
  clear-prior-phase-tools: true  # default true
next-phases:
  - id: review
    when: "Draft complete, ready for user review."
  - id: research
    when: "Source material is thin — gather more."
---

Prompt body. Tell the agent what this phase does and how to know when
to call phase_advance.
```

#### Subagent phase

```yaml
---
kind: subagent
tools: [tabstack_*, vault_read]
outputs: [sources.md]
next-phases:
  - id: draft
---

Prompt body — instructions for the subagent. The engine spawns a
child agent with the listed tools, runs this prompt, then verifies
the listed output files exist before advancing.
```

Subagent phases must have **exactly one** `next-phases` edge and **no
gates** on outgoing edges (gates are user-facing; the user does not
see the subagent).

`subagent-skill: <name>` is the escape hatch — instead of an inline
prompt, the subagent activates a named skill.

#### Edge-level gates

```yaml
next-phases:
  - id: publish
    when: "User has reviewed and approved the draft."
    gate:
      type: review
      message: "Approve the draft?"
      approve-label: "Looks good"
      deny-label: "Needs changes"
      on-deny: draft   # implicit on-approve = edge.id
```

When the agent calls `phase_advance(publish, ...)`, the engine fires
the gate. Approve → transition to `publish`; Deny → transition to
`draft`.

## Engine tools

Always loaded:

| Tool | Purpose |
|---|---|
| `workflow_start(name, slug)` | Start a new run. |
| `workflow_list(workflow, status)` | List runs across all conversations. |
| `workflow_switch(run_id)` | Set the conversation's current run. |
| `workflow_status` | Show current run, valid transitions with `when:` text, recent history. |
| `workflow_artifact_read/write` | I/O scoped to the run's `artifacts/` directory. |

Dynamically injected when a run is active:

| Tool | Purpose |
|---|---|
| `phase_advance(target_phase_id, reason)` | Canonical transition tool. Schema enum + descriptions reflect the current phase's `next-phases`. |

## Validation

The loader rejects (logs warning, skips the workflow):

- Missing `workflow.initial-phase`
- Undefined edge targets (`next-phases.id` not in phases)
- Multi-edge phases missing `when:` on any edge
- Subagent phases with multiple edges or gated edges
- Subagent phases missing `outputs:` (unless `subagent-skill:` is set)
- Gate `on-deny` targets that don't exist

## Run state

Each run lives at `workspace/workflows/{name}/runs/{run-id}/` with
`state.json` (current phase, transition history, status) and
`artifacts/` (phase outputs). Runs survive across conversations —
`workflow_switch <run-id>` reattaches.

## Cross-phase context

Phase-boundary tool-result clearing (default on) prunes the prior
phase's tool outputs from the composer's view. To carry forward
non-trivial findings, instruct the agent to use the always-loaded
`notes_append` before calling `phase_advance` — notes survive both
tool clearing and compaction.

## Limitations (v1)

- Only `gate: review` is supported (no input widgets yet)
- Edges use LLM-routed `when:` strings; no code-evaluated conditions
- Workflows can't nest (no sub-workflows)
- `workflow_list` walks the filesystem — fine for tens of runs, not
  hundreds
