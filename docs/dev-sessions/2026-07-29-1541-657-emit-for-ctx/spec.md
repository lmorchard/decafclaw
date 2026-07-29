# Refactor: extract duplicated `_emit_for_ctx` helper

**Source:** https://github.com/lmorchard/decafclaw/issues/657

Captured verbatim from the issue body (marker line stripped). This is an issue augmented in
place by `agent-session:triage`, not a spec written to the full template — the readiness gate
applied was the *augmented existing issue* variant.

---

Follow-up from #414 (PR #653).

The 3-line `_emit_for_ctx(ctx)` helper (`getattr(ctx, 'manager', None)` → `manager.emit`) is now duplicated across four modules: `tools/canvas_tools.py`, `tools/sticky_tools.py`, `tools/checklist_tools.py`, and `skills/project/tools.py`. At four copies this is past the extract-at-third-callsite threshold.

Extract to a single shared helper (e.g. `emit_for_ctx` in `events.py` or `context.py`) importable by both tools and skills (skills need an absolute import), and replace the four local copies. Kept local during #414 to avoid editing unrelated `canvas_tools.py`; this issue is the deliberate cleanup pass.

Flagged by both Copilot and the #414 whole-branch review.

---

<!-- Appended by agent-session:triage on 2026-07-29. Author's text above is unchanged. -->

## Verified-false claims

**None — every claim in this issue checks out.** Verified individually at triage:

- All four named paths exist and each holds a definition. **Count is exactly 4**, no fifth site.
- All four bodies are **character-identical** (confirmed by md5 over the extracted function bodies:
  `6de7e2806fade2122d6674a3ffc536ba` × 4). This is a true copy-paste duplication, not four
  near-variants that would need reconciling.
- "Follow-up from #414 (PR #653)" — PR 653 is MERGED ("feat(widgets): progress_tracker widget +
  checklist/project auto-emit (#414)"); issue 414 is CLOSED. Correct lineage.
- "skills need an absolute import" — corroborated by `CLAUDE.md:63` and by the loader at
  `src/decafclaw/tools/skill_tools.py:110` (`spec_from_file_location` with no package context).
- Both suggested host modules exist: `src/decafclaw/events.py` (37 lines, `EventBus` only) and
  `src/decafclaw/context.py`.
- Cosmetic only: the issue calls it a "3-line helper"; the body is 4 statements (5 lines with the
  `def`). No criterion depends on this.

**Actual duplicate sites (4 definitions):** `src/decafclaw/tools/sticky_tools.py:21`,
`src/decafclaw/tools/canvas_tools.py:22`, `src/decafclaw/tools/checklist_tools.py:27`,
`src/decafclaw/skills/project/tools.py:98`.
**11 call sites** across those same four files: canvas `44, 59, 70, 85`; sticky `32, 42`; checklist
`75, 82`; project `148, 159`. No other module in `src/`, `tests/` or `tui/` inlines
`getattr(ctx, "manager", ...)` — **the four definitions are the complete population.**

## Verifiable acceptance criteria

- CRITERION: The codebase SHALL contain exactly one definition of the `emit_for_ctx` helper, AND each
  of the four consumer modules SHALL obtain it by import rather than by local definition.
  CHECK (single exit-coded command, run from the repo root):
  a stdlib-only static probe that counts `def _?emit_for_ctx` definitions under `src/decafclaw`,
  counts which of the four named consumers import the symbol, and counts which still call it —
  asserting `DEFS == 1`, `IMPORTS == 4`, `USES == 4`.
  VERIFIED DISCRIMINATING: run at triage →
  `DEFS = 4` (the four sites above), `IMPORTS = 0 []`, `USES = 4`, exit 1
  (`AssertionError: expected 1 definition, found 4`).

  **Why the criterion is stated this way rather than as a bare count:** it is an invariant over *where
  the logic lives*, so "touch the count" does not satisfy it — deleting a call site drops `USES` below
  4 and fails; adding an import while keeping the local definition leaves `DEFS` at 4 and fails.
  It is also **host-agnostic**: `events.py`, `context.py`, or a new module all pass identically, so
  that open choice is implementation style and does not affect the tier.
  **Fail mode is loud, not vacuous:** it reads files by hardcoded path with zero fixture setup, so if
  a consumer is renamed it raises `FileNotFoundError` rather than passing silently. No
  `-k`-matches-nothing or uninitialised-registry false green is reachable.
  The one thing this criterion cannot see is a **wrong body** in the extracted helper — which is
  exactly what the first guard covers. They are a pair; do not freeze one without the other.

## Regression guards

- GUARD (**the behavioural pair for the criterion**):
  `uv run pytest tests/test_canvas_tools.py tests/test_sticky_tools.py tests/test_checklist_tools.py tests/test_project_tools.py -q`
  — protects the helper's semantics on **both** branches. `tests/test_canvas_tools.py:72` asserts
  `manager_mock.emit.assert_awaited_once()` (manager-present branch genuinely wired), and
  `tests/test_project_tools.py:37` builds its ctx with `manager=None` plus
  `TestProgressTrackerEmit::test_emit_failure_is_fail_open` (`:444`) covering the None/fail-open
  branch. Observed at triage: `67 passed in 2.05s`. Invariant: no test lost, newly skipped, or newly
  failing.
- GUARD (**skill-loader import path — the `CLAUDE.md:63` rule**): exec
  `src/decafclaw/skills/project/tools.py` through `spec_from_file_location` the way the real loader
  does, and confirm the helper resolves. This protects against the specific regression this refactor
  invites: **a *relative* import (`from ..events import ...`) would still satisfy the criterion and
  still pass an ordinary package import, but breaks under the real skill loader.** Observed at triage:
  `loader-path exec OK; emit helper present: True`.
- GUARD (**no import cycle**): importing all four consumers plus `decafclaw.events` succeeds.
  `events.py` currently imports nothing from `context`/`tools`, so hosting the helper there is
  cycle-free; this pins that. Observed at triage: `imports OK, no cycle`.
- GUARD (**types/lint on touched files**): `uv run pyright` over the four consumers + `events.py` +
  `context.py` → observed `0 errors, 0 warnings, 0 informations`; and `uv run ruff check` over
  `src/decafclaw/tools/`, `src/decafclaw/skills/project/`, `src/decafclaw/events.py` → observed
  `All checks passed!`. Live concern: the helper's `ctx: "Context"` annotation sits behind
  `TYPE_CHECKING`, so the shared version must keep its forward ref resolvable from the new host — and
  HEAD (`28d2f38`) was itself a pyright/ctx fix.
- GUARD (invariant): full suite — no test lost, newly skipped, or newly failing versus HEAD.
  **UNRUN (needs a serial full-suite run)** — deliberately not attempted. **Do not read this as verified.**

## Tier: `auto-ok`

Neither trigger fires.

**Trigger 1:** the criterion's oracle is a stdlib-only static probe that exists and runs today; it
failed today; its cheapest green is the extraction itself; and its answer does not depend on unwritten
harness state. The one decision the issue leaves open — host module, `events.py` vs `context.py` —
**does not change which criteria apply**, so it is implementation style, not a withheld goal.

**Trigger 2:** touched paths are `src/decafclaw/tools/` and `src/decafclaw/skills/project/` plus one
host module. No auth/authorization, no secrets/credentials, no data migration or deletion, no
deploy/infra/CI config, no dependency change (the helper uses stdlib `getattr` only).

## Notes for the implementer

- **Naming:** the four copies are private (`_emit_for_ctx`); the shared one must be **public**
  (`emit_for_ctx`) to be imported across packages. The criterion's regex accepts either spelling so it
  does not over-constrain, but a leading underscore on a cross-module import would trip ruff
  conventions in review.
- **Preserve the `getattr` form verbatim.** Every test ctx already carries a `manager` attribute (real
  `Context` via `tests/conftest.py:33`; the `SimpleNamespace` at `tests/test_project_tools.py:37`), and
  `Context.__init__` sets `self.manager = None` at `src/decafclaw/context.py:131` — so the
  `getattr(..., None)` default is currently unreachable-by-test defensiveness. **If it is "simplified"
  to `ctx.manager`, no existing test would catch the difference.** No check enforces this; it is stated
  here deliberately.
- **Preferred host (non-blocking):** `events.py` is the better fit on current structure — already
  dependency-free, no cycle risk, and `context.py` is what the annotation forward-refs. The criterion
  passes either way.
- **Stale doc to update in the same pass:**
  `docs/dev-sessions/2026-07-24-1133-progress-tracker/plan.md:18` explicitly instructs "replicate the
  3-line `_emit_for_ctx` locally where needed", which this issue reverses. Other `docs/dev-sessions/**`
  hits are historical plan transcripts and should be left alone.
- Size XS is right: 4 deletions, 1 addition, 4 import lines, 11 call sites unchanged in shape. Do not split.
