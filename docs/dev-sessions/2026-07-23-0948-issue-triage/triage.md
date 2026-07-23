# Backlog triage & roadmap — 2026-07-23

Full triage of all open GitHub issues via an 11-agent fan-out, each cross-checking
its cluster against current code. This doc is the durable record of *why* — the
close/merge rationale, the epic groupings, and the strategic direction — since
that reasoning decays fastest from scattered issue-close comments.

**Outcome:** open issues pruned **208 → 181**, Ready column reshaped around an
instrumentation-first arc, one new umbrella (#621) created.

---

## Headline read

The backlog wasn't broken — it was **over-decomposed and under-pruned**. ~1/4 was
closeable (already shipped, or dead code from the workflow-engine pivot) or
mergeable (same work filed 2–5 times).

The dominant cross-cutting signal: **the project builds on impression, not
measurement.** Reflection, tool-scoring, and prompt-caching all have cheap
instrumentation sitting unbuilt while their speculative follow-ups pile up — and
those follow-ups are correctly *gated* on data that doesn't exist yet. Hence the
chosen next direction (below).

---

## Pruned

### Closed — done / superseded
| # | Why |
|---|---|
| #272 | MCP disconnect filtering already implemented (`mcp_client.py` filters on `status=="connected"`) |
| #402 | Per-conversation sidecar dir shipped (#578 migration, #588 removed fallback); `make migrate-sidecars` exists |
| #22 | Superseded by the workflow interview engine (`wf.user_input`) |
| #561–#564 | **Zombie follow-ups** to the LLM phase-state-machine (PR #557) that was *deleted* in the code-driven replay-engine pivot (commit `14b24ec`). Referenced mechanisms no longer exist. |

### Closed — stale / out of direction
#23 (conversation handoff — multi-user/support-desk shape), #26 (channel mgmt —
team-ops), #28 (infancy grab-bag), #461 (RL/benchmarking — "different mission"),
#462 (kanban board — covered by `delegate_task`), #102 (external eval, no action),
#478 (Spotify history covered by me-to-markdown; only playback remained).

### Merged — duplicates
#21→#39 · #96→#449 · #99→#472 · #32→#44 · #278→#274 · #243+#541→#364 ·
#287→#377 · #463→#447

### Consolidated
Five low-value presentational widgets (#412 link_card, #413 tree_view, #415
image_gallery, #417 date_picker, #418 rating) folded into new umbrella **#621**.

---

## Epics (parent + carve-outs — don't work the parent directly)

- **Pluggable reflection judges:** #591 (keystone `verifier_model`, do first) →
  #589 + #529 + #530, all gated on **#528** (eval infra). #532 (ceremony tuning)
  shares the #528 gate.
- **Tool-scoring framework:** #274 parent; discrete signals #270/#271 near-term;
  #275/#276/#277 gated research. #274 itself says "don't start yet."
- **MCP-overload consolidation:** #310 (telemetry) → #307 (audit) → #308/#309
  (reductions) → #311 (guidance). #308 + #311 shippable now without telemetry.
- **Tool-execution resilience (#7):** #325 → #326; #324 parallel sibling.
- **Time/calendar awareness (#97):** split when picked up (layer 1 = inject
  current date into prompt, confirmed NOT done today).
- **Personal-history ingest** is now owned by me-to-markdown + `meta-ingest`
  (Mastodon/Linkding/GitHub/Spotify/YouTube/Pocket Casts) — which guts the
  "skill per service" halves of #374/#376/#478/#286. Remaining real gap is
  *on-demand* transcript ingest, not history pulls.

---

## Strategic direction — where next

### 1. Instrumentation first ("measure before building") — CHOSEN NEXT
Reflection, tool-scoring, and prompt handling are argued from impression. Three
cheap items answer "does this earn its keep" before their expensive follow-ups
start:
- **#310** tool usage / unused-tool report
- **#409** reflection cost/effectiveness telemetry
- **#480 Phase 1** prompt-cache hit-rate measurement

Spec written: `docs/dev-sessions/2026-07-23-0917-instrumentation-measure-first/spec.md`.
Behavior change is explicitly out of scope; each becomes a follow-up issue once a
week of data exists.

### 2. Vault retrieval quality
#197 (dream/garden auto-fill frontmatter — upstream of retrieval *and* the vault
evals) → #306 (recency journal surfacing) → #318 (first-class tags).

### 3. Confirmation unification
#364 absorbing #243/#541 — retires the brittle `EndTurnConfirm` label hack,
unifies two human-input mechanisms, closes three issues in one session.

### 4. Event-reactive, not just polling
#449 routines/webhooks (unlocks #96) with #553 (structured status) as
prerequisite. **#447 (MCP-serve)** is the single highest-leverage integration
item — turns Claude Code into a native driver, obsoletes #463.

---

## Ready column (13, priority-ordered)

1. **Quick-win bugs** (cheap, verified against code): #526 (poisons the eval
   suite), #355, #587 (security — unsanitized `conv_id`), #535, #604
2. **Instrumentation:** #310, #409, #480
3. **Validated features:** #414 + #419 (pair), #285, #598, #605

---

## Flagged for Les's call (NOT auto-actioned)

- **#25** (bot/channel allowlists) — left open; genuine keep-or-close judgment.
- **#460** (multi-platform gateway), **#465** (Termux) — left open as research
  parking-lot; close if preferred.
- **#442** (In Progress) — spec-complete in a worktree but **zero code
  committed**; may belong back in Ready.
- **#40** (heartbeat media), **#468** (vision — passive multimodal already works)
  — stale premises; re-verify before picking up.
- **#598 ↔ #539** overlap on the loop-breaker — pick one mechanism before building.

---

## Other quick-win bugs staged behind Ready
#566 (skill-discovery env-only check swaps bundled→shadow), #137 (client-MIME
embedding hole), #146 (JSON arg repair), #431 (config-show skips scalars),
#600/#601 (vault-guide test + doc), #575 (workflow Context-kind ADR),
#166+#555 (web-UI a11y pass), #350/#531 (eval-infra additions).
