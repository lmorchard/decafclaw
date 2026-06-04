---
name: workflow_hello
description: Smallest possible workflow — one llm_call step that generates a greeting.
kind: workflow
user-invocable: true
argument-hint: "[topic]"
---

A minimal hello-world workflow that exercises the step-primitive engine.
Runs a single `llm_call` step that generates a short greeting and writes
the structured output to workflow state.

Use this to verify the engine is working end-to-end.
