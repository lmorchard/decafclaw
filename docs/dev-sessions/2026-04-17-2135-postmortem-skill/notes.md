# Notes: Postmortem skill

Filled in during/after execution.

## Decisions (2026-04-17)

- Name: `postmortem` (confirmed).
- Persistence: always write to `agent/pages/postmortems/YYYY-MM-DD-HHMM-slug.md`.
- Context mode: `inline`.
- Eval scope: one case — seeded three-repeat tool error.
- Folder path and blameless-framing rule: proceed with defaults; revisit during prompt tuning if output disagrees.

## Surprises / pivots

- **Eval harness didn't dispatch user-invokable commands.** Raw input
  was passed directly to `run_agent_turn`, so `/postmortem` was read
  as plain text. Transports (web, Mattermost) all route through
  `dispatch_command` first. Added that path to the eval runner in a
  separate commit — small (~40 lines) and unblocks evals for every
  user-invokable skill going forward. Scope creep acknowledged; it
  was on the critical path.
- **`response_contains` is an OR match**, not AND. Caught this before
  running — wrote a single `(?s)`-enabled regex asserting all five
  section headings in order rather than a list of five patterns.
- **`vault_write` parameter is `page`, not `path`.** First eval run
  surfaced this as a tool error; fixed SKILL.md.
- **First eval pass on gemini-flash produced exactly the wanted
  output:** all five sections, blameless framing, specific proposed
  patch tagged Systemic, concrete next steps for the user. The SKILL.md
  body did not need tuning on this input. Manual smoke-test (Phase 2)
  will validate richer conversations — that's where tuning may still
  happen.

## What's still open

- **Phase 2 manual smoke test.** Les exercises `/postmortem` in his
  running web UI against a real conversation with messier failure
  patterns. May surface SKILL.md tuning needs.

## Final summary

Shipped v1 of the `postmortem` skill: SKILL.md-only, user-invokable
via `/postmortem` and `!postmortem`, produces a structured five-section
report (Anomaly / Root cause hypotheses / Proposed patches / Systemic
vs session-specific / Next steps), writes it to
`agent/pages/postmortems/` for later consolidation by dream/garden.
Blameless framing enforced in the prompt. Eval case passes on the
default eval model. Side benefit: eval runner now exercises user-invokable
commands end-to-end.
