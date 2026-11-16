# Code Debt & Refactoring Backlog

A consolidated code-quality review of the DecafClaw codebase, decomposed into
**14 epics**, each holding small, independently shippable issues (sized XS–M).
Each epic's file in this directory holds filing-ready issue drafts.

> Status: DRAFT — reviewed locally, not yet filed on GitHub. File from this list
> once Les confirms the open decision below and priority.

## Open decision to confirm before filing

- **Issue 2.1 (friction skill): repair vs remove.** The draft repairs it (register
  the missing tool; the logic already exists and `tests/test_friction.py` passes).
  The alternative is to delete the skill. Confirm one before filing.

## Size legend

- **XS** — trivial/mechanical: delete a dead line, rename, add a prop, add an enum.
- **S** — focused: one bug fix, or one helper adopted at 1–2 call sites.
- **M** — bounded refactor: helper consolidation across modules, one module split,
  or one per-resource route extraction. Largest size we file; nothing above M.

## Filing convention

Each epic file lists its issues as a `### E.N — Title` section with:

- **Size** — XS/S/M.
- **Triage** — `ready`, `blocked-by #E.N` (native blocked-by at filing),
  `needs-input`, or `needs-definition`.
- **Problem / Result / Scope / Verify / Tests / Deps** — the fields ghflow's
  `triage:ready` requires (scope + verification criteria concrete enough to implement).

`Verify` names the gate command; `Tests` names the test to add (or the existing
test that already protects). `Deps` uses `E.N` ids until real issue numbers exist;
at filing, replace `blocked-by #E.N` with the resolved issue number.

## Triage summary (~115 issues)

| Ep | Title | Issues | Ready | Blocked | Needs-input | Needs-definition |
|---|---|---|---|---|---|---|
| 1 | Security & path-containment | 6 | 5 | 1 (1.3) | — | — |
| 2 | Dead code & latent bugs | 11 | 10 | 1 (2.8) | 1 (2.1) | — |
| 3 | LLM provider consolidation | 9 | 8 | 1 (3.2) | — | — |
| 4 | Config resolution | 5 | 4 | — | — | 1 (4.5) |
| 5 | Tool failure contract | 4 | 4 | — | — | — |
| 6 | Tools registry & dispatch | 8 | 6 | 2 (6.3, 6.6) | — | — |
| 7 | ConversationManager | 9 | 8 | 1 (7.3) | — | — |
| 8 | Context classification | 6 | 4 | 2 (8.2, 8.4) | — | — |
| 9 | Skills cleanup | 14 | 13 | 1 (9.7) | — | — |
| 10 | Enums sweep | 10 | 10 | — | — | — |
| 11 | HTTP/web split | 16 | 15 | — | — | 1 (11.12) |
| 12 | Frontend consolidation | 9 | 9 | — | — | — |
| 13 | Frontend robustness | 4 | 4 | — | — | — |
| 14 | Test hygiene | 6 | 6 | — | — | — |

Blocked items are filed with a native `blocked-by` link and return to ready after
the prerequisite lands (`sweep-blocked`). The two `needs-input`/`needs-definition`
items carry their decision/design question inline.

## Suggested sequencing

1. **Epics 1–2** — small defects, do first (Epic 1 has a security patch).
2. **Epics 3–6, 8** — consolidation; highest drift-removal per effort.
3. **Epic 10** — enums; low-risk parallel fill.
4. **Epics 7, 9, 11, 12** — structural splits; the numbered order inside each epic
   is already PR-order.
5. **Epics 13–14** — wherever bandwidth allows.

## Unattended-fit note

`curate-ready-queue`'s unattended bar is stricter than `triage:ready`: acceptance
must be checked by gate commands, not a person/Dev server/browser. Epics 1–10 and
the helper consolidations clear it. A few frontend items (13.1, 13.2, 13.3) and
11.12 need a unit test rather than manual UI verification to qualify — noted in
their `Verify` lines.

## Epics

- [Epic 1 — Security & path-containment](01-security-path-containment.md)
- [Epic 2 — Dead code & latent bugs](02-dead-code-latent-bugs.md)
- [Epic 3 — LLM provider consolidation](03-llm-provider-consolidation.md)
- [Epic 4 — Config resolution](04-config-resolution.md)
- [Epic 5 — Tool failure contract & batch execution](05-tool-failure-contract.md)
- [Epic 6 — Tools registry & dispatch](06-tools-registry-dispatch.md)
- [Epic 7 — ConversationManager decomposition](07-conversation-manager.md)
- [Epic 8 — Context classification & model resolution](08-context-classification.md)
- [Epic 9 — Skills cleanup](09-skills-cleanup.md)
- [Epic 10 — Stringly-typed → enums](10-enums-sweep.md)
- [Epic 11 — HTTP/web split](11-http-web-split.md)
- [Epic 12 — Frontend consolidation](12-frontend-consolidation.md)
- [Epic 13 — Frontend robustness](13-frontend-robustness.md)
- [Epic 14 — Test hygiene](14-tests-hygiene.md)