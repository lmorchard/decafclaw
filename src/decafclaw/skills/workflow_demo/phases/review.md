---
kind: inline
tools: [vault_read, workflow_artifact_read]
next-phases:
  - id: publish
    when: |
      The user has approved the draft and it can be published to the vault.
    gate:
      type: review
      message: "Approve the research brief?"
      approve-label: "Looks good"
      deny-label: "Needs changes"
      on-deny: draft
---

Read the draft from `artifacts/draft/brief.md` and present it to the user in
your response, prefaced with a single sentence framing what they're about to
read.

Then call `phase_advance` with target `publish` and a brief `reason` — the
gate will surface the Approve / Needs Changes buttons. If the user approves,
the workflow continues to `publish`. If they deny, you'll re-enter `draft`
to revise.
