---
name: interview
description: A structured interview workflow that asks a series of questions on a topic, listens to your answers, and synthesises a summary when all topics are covered. The agent picks each question, you respond in your own words, and the workflow routes to the next topic, requests clarification, or produces a final summary.
kind: workflow
user-invocable: true
argument-hint: "topic=<topic>"
---

Start a structured interview now by calling `workflow_start("interview")`. The workflow will ask one question at a time, pausing for your text answer, then route to the next question, clarify a vague answer, or summarise once all topics are covered.

If the workflow is paused waiting for input, call `workflow_start` again — it's idempotent and will re-render the current question's widget.

Do not explain the workflow — just start it.

See [docs/workflows.md](../../../../docs/workflows.md) for the full step-by-step description if needed.
