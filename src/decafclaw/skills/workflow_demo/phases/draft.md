---
kind: inline
tools: [vault_read, workflow_artifact_read, workflow_artifact_write, notes_append, notes_read]
context-profile:
  memory-retrieval: off
  clear-prior-phase-tools: true
next-phases:
  - id: review
    when: |
      The draft is written, covers the topic clearly, and is ready for the
      user to review.
  - id: gather
    when: |
      The source material is too thin to support a clear brief — go back
      and fetch more research before drafting can finish.
---

You are drafting a research brief on the topic. Read the source summary from
`artifacts/gather/sources.md` (use `workflow_artifact_read`).

Compose a brief of 400-600 words covering:
1. A one-paragraph framing of the topic.
2. 2-3 sections of body covering the main themes.
3. A short "Open questions" list at the end.

Write the draft to `artifacts/draft/brief.md` via `workflow_artifact_write`.

Before advancing:
- Use `notes_append` to record a 1-2 sentence summary of what you wrote and any
  decisions you made (these notes persist across phase boundaries).
- Then call `phase_advance` with target `review` if you're satisfied, or
  `gather` if the sources turned out to be insufficient.
