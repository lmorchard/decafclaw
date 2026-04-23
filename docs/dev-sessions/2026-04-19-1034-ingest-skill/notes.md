# Notes: /ingest skill

Filled in during/after execution.

## Decisions (2026-04-19)

- Accepts URL, workspace file, attachment. No pasted text, no multi-URL.
- Agent picks the folder (no forced prefix like bookmarks/ or mastodon/).
- Agent picks the fetch mechanism. Tabstack first for URLs, web_fetch fallback.
- Cap total pages touched at ~5 per ingest.
- End turn after change summary.
- No `log.md` / `index.md` integration (deferred to #288, #289).
- Cross-graph link backfill stays with garden (#287).

## Verifications (pre-execute)

- Tool names confirmed: `workspace_read`, `web_fetch`, `tabstack_extract_markdown`, `list_attachments`, `get_attachment`.
- Binary (PDF etc.) attachments return only base64 metadata from `get_attachment` — skill body explicitly notes this limitation and tells the agent to stop.

## Surprises / pivots

- **`workspace/` prefix double-application.** First eval pass failed with
  a not-found because `workspace_read(path="workspace/imports/foo.md")`
  resolves to `{workspace_path}/workspace/imports/foo.md`. The tool
  expects paths relative to the workspace root. Fixed in the SKILL.md
  with an explicit "strip the leading `workspace/` prefix" instruction
  and an example.
- **Eval-harness gap: no workspace-file fixtures.** `_setup_workspace`
  only supported embeddings and journal memories. Added a
  `setup.workspace_files` map (`path: content`) — small surgical
  extension, same justification as the dispatch_command fix in the
  postmortem session. Unblocks any eval that needs arbitrary seed files.
- **First passing run was clean.** gemini-flash chose a sensible folder
  (`agent/pages/tools/ripgrep`, inferred) and produced the expected
  change-summary shape on one shot. No body tuning needed for the
  workspace-file path — the real tuning signal will come from Les's
  manual URL and attachment smoke tests.

## What's still open (for Les)

- **URL path.** Tabstack happy path + fallback to web_fetch on failure.
  Eval can't cover this reliably.
- **Attachment path.** Web UI upload → `/ingest` with no args or with a
  filename. Needs manual smoke test.
- **Focus arg effect.** Does `— focus on X` visibly shape output tone?
- **Folder sprawl check.** After a few ingests, does the agent pick
  coherent folders or spray singletons?

## Final summary

Shipped v1 of the `ingest` skill: user-invokable via `/ingest` and
`!ingest`, accepts URL / workspace file / attachment (one source per
invocation), agent-chosen fetch mechanism and output folder, up to ~5
pages touched per ingest, ends the turn with a change summary. Eval
covers the workspace-file path on the default model. Side benefit: eval
harness now supports arbitrary workspace-file fixtures via
`setup.workspace_files`.
