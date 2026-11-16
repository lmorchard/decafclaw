# Code Debt & Refactoring Backlog

A consolidated code-quality review of the DecafClaw codebase, decomposed into
**14 epics**, each holding small, independently shippable issues (sized XS–M).
Each epic's file in this directory holds filing-ready issue drafts.

> Status: DRAFT — reviewed locally, not yet filed on GitHub. All open decisions
> are resolved; priorities are the only thing left to set before filing.

## Decisions (resolved)

- **2.1 (friction skill): repair.** The draft registers the missing tool and lifts
  `tests/test_friction.py` out of `ruff.extend-exclude`.
- **4.5 (Config split):** scoped to a `ConfigRuntime` dataclass (see Epic 4).
- **11.12 (`response_model`):** scoped to slice 1, the conversation-listing
  endpoints (see Epic 11).

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

### Machine-readable manifest

`filing-manifest.json` is the machine-readable projection of these files
(generated, single source = the markdown). `FILING.md` gives the filing worker a
deterministic procedure. File from the manifest, in list order — it is already
dependency-safe (a blocked item always has a lower number than its prerequisite).

## Triage summary (117 issues)

| Ep | Title | Issues | Ready | Blocked |
|---|---|---|---|---|
| 1 | Security & path-containment | 6 | 5 | 1 (1.3) |
| 2 | Dead code & latent bugs | 11 | 10 | 1 (2.8) |
| 3 | LLM provider consolidation | 9 | 8 | 1 (3.2) |
| 4 | Config resolution | 5 | 5 | — |
| 5 | Tool failure contract | 4 | 3 | 1 (5.2) |
| 6 | Tools registry & dispatch | 8 | 6 | 2 (6.3, 6.6) |
| 7 | ConversationManager | 9 | 6 | 3 (7.3, 7.5, 7.6) |
| 8 | Context classification | 6 | 4 | 2 (8.2, 8.4) |
| 9 | Skills cleanup | 14 | 13 | 1 (9.7) |
| 10 | Enums sweep | 10 | 10 | — |
| 11 | HTTP/web split | 16 | 16 | — |
| 12 | Frontend consolidation | 9 | 9 | — |
| 13 | Frontend robustness | 4 | 4 | — |
| 14 | Test hygiene | 6 | 6 | — |

**105 ready, 12 blocked (sequenced), 0 needs-input, 0 needs-definition.** Blocked
items are filed with a native `blocked-by` link and return to ready after the
prerequisite lands (`sweep-blocked`).

## Unattended-fit

All **105 ready** items are unattended-fit: acceptance is checked by a gate command
(`make test`, `make check`, `make test-js`), no new service/credential/account, no
open decision, and no cross-issue dependency. The UI items (Epic 13) verify through
the Playwright browser harness (`tests/test_web_browser_scenarios.py`), which runs
in CI — no human or live service.

The **12 blocked** items become unattended-fit only after their prerequisite lands;
sequence them in a later batch (see ordering below).

**Size flag (larger than the 50–200 line default):** these are mechanical *moves*
whose diff is large but whose risk is low. Name them when curating, at most one per
batch per the queue guidance:

- 11.1–11.8 (per-resource route extraction) — moved lines dominate the diff
  (~200–400 lines each).
- 14.1–14.3 (test-file splits) — moved test lines (~1,000+ lines relocated).

Everything else is ≤ M with an effective change well under 200 lines.

## Suggested sequencing

1. **Epics 1–2** — small defects, do first (Epic 1 has a security patch).
2. **Epics 3–6, 8** — consolidation; highest drift-removal per effort.
3. **Epic 10** — enums; low-risk parallel fill.
4. **Epics 7, 9, 11, 12** — structural splits; the numbered order inside each epic
   is already PR-order.
5. **Epics 13–14** — wherever bandwidth allows.

**For an unattended batch,** order within each epic dependency-first so blocked
items only appear in the batch *after* their prerequisite: 1.2→1.3, 2.7→2.8,
3.1→3.2, 5.1→5.2, 6.1→6.3 and 6.5→6.6, 7.1/7.2→7.3 and 7.4→7.5/7.6,
8.1→8.2 and 8.3→8.4, 9.6→9.7.

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