---
name: research_brief
description: Multi-phase research brief generator. Gathers sources via a subagent, then produces an outline, draft, and critique cycle before publishing the brief as a workflow artifact.
kind: workflow
user-invocable: true
argument-hint: "topic=<topic>"
---

Start the research brief workflow now by calling `workflow_start("research_brief")`. The workflow will gather sources, draft a brief, critique it (cycling back if revision is needed), and publish the final brief as a workflow artifact.

If the workflow is paused waiting for input, call `workflow_start` again — it's idempotent and will re-render the current prompt's widget.

Do not explain the workflow — just start it.

**Known limitation:** the gather step uses training-knowledge sources rather than live web research. A tabstack-backed variant is future work.

See [docs/workflows.md](../../../../docs/workflows.md) for the full step-by-step description.
