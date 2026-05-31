---
name: spike_brief
description: "Code-driven workflow spike — runs a 4-phase research brief end-to-end. Code drives transitions; the LLM is called per phase as a structured-output worker. Throwaway proof-of-mechanism, not a permanent skill."
user-invocable: true
argument-hint: "<topic>"
allowed-tools: [spike_brief_run]
---

You MUST call `spike_brief_run(topic="$ARGUMENTS")` immediately, with no other tool calls and no prose before the call. The tool runs all four phases (gather → draft → review → publish) internally and returns the full transcript plus the final brief.

Do not narrate the request. Do not summarize what you're about to do. Do not call any other tool. Invoke `spike_brief_run` and return its result.
