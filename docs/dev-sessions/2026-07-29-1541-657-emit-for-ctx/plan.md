# Extract the duplicated `_emit_for_ctx` helper — Implementation Plan

**Goal:** Replace four character-identical copies of the `_emit_for_ctx` helper with one shared
`emit_for_ctx` in `events.py`, imported absolutely by all four consumers.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/657 — **Tier:** `auto-ok`
(the criterion's oracle is a stdlib static probe that exists and fails today; no touched path is
auth / secrets / migration / deploy / dependency)

**Approach:** Add `emit_for_ctx` to `src/decafclaw/events.py` — the issue's stated preferred host,
and the right one on current structure: `events.py` imports nothing from `context` or `tools`, so it
cannot participate in the documented `context → context_composer → skill modules` cycle. Delete the
four local definitions and replace each with an absolute import. The 11 call sites keep their exact
shape; only the name loses its leading underscore.

**Criteria:** C1 — exactly one definition of the helper, obtained by import in all four consumers.

Full text + checks live in `checks.md`. Ids are assigned there and referenced here.

---

## Phase 0: Freeze the acceptance checks — **DONE**

Written before this plan, per `references/frozen-checks.md`, so no check was authored with the
implementation approach in view.

**Files:**
- Created: `docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/checks.md`
- Created: `docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/check_c1_emit_for_ctx.py`
- Created: `docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/guard_g2_skill_loader.py`
- Created: `docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/guard_g3_no_cycle.py`

**Verification — automated:**
- [x] C1's check runs and **fails for the expected reason** — exit 1, `AssertionError: expected 1
      definition, found 4`; matches the issue's recorded discriminating failure exactly
- [x] Every guard runs and **passes** — G1 `69 passed`, G2 exit 0, G3 exit 0, G4 pyright
      `0 errors, 0 warnings, 0 informations` + ruff `All checks passed!`, G5 `3608 passed, 2 skipped`
- [x] Freeze commit made (`39f5ff5`); sha recorded in `checks.md` in follow-up commit `aef51e2`

---

## Phase 1: Extract `emit_for_ctx` into `events.py` and re-point all four consumers

One vertical slice. The issue says explicitly: *"Size XS is right … Do not split."* Splitting the
host-module addition from the consumer updates would leave an intermediate commit with five
definitions, which is strictly worse than one atomic move.

**Advances:** C1 (fully).

**Files:**
- Modify: `src/decafclaw/events.py` — add the public `emit_for_ctx` helper plus the
  `TYPE_CHECKING`-guarded `Context` import it needs for the forward ref
- Modify: `src/decafclaw/tools/sticky_tools.py` — delete local def (line 21), add import
- Modify: `src/decafclaw/tools/canvas_tools.py` — delete local def (line 22), add import
- Modify: `src/decafclaw/tools/checklist_tools.py` — delete local def (line 27), add import
- Modify: `src/decafclaw/skills/project/tools.py` — delete local def (line 98), add **absolute**
  import
- Modify: `docs/dev-sessions/2026-07-24-1133-progress-tracker/plan.md` — line 18 instructs
  "replicate the 3-line `_emit_for_ctx` locally where needed", which this issue reverses. Annotate
  it as superseded rather than rewriting history. Other `docs/dev-sessions/**` hits are historical
  transcripts and are left alone.
- Test: none added. **TDD opt-out, deliberate:** this is a pure refactor with no behaviour delta —
  the four bodies are character-identical, so there is no new behaviour to drive out with a test.
  Coverage already exists on both branches of the helper and is pinned as G1 + G2.

**Key changes:**

In `src/decafclaw/events.py`, appended after `EventBus`:

```python
def emit_for_ctx(ctx: "Context"):
    """The manager's emit callable for this ctx, or None when there's no manager.

    Shared by every producer that mirrors state into a UI surface (canvas,
    sticky slot, checklist, project skill) so the fail-open None case is
    written once. Kept as `getattr` rather than `ctx.manager` on purpose:
    the sticky/canvas producers are also called with lightweight stand-in
    ctx objects that carry no manager attribute at all.
    """
    manager = getattr(ctx, "manager", None)
    if manager is None:
        return None
    return manager.emit
```

and the guarded import `events.py` does not currently have:

```python
if TYPE_CHECKING:
    from decafclaw.context import Context
```

Three constraints on that, all from `CLAUDE.md`:
- The `Context` import **must** stay `TYPE_CHECKING`-guarded. `events.py` is imported by
  `context.py`, so a runtime import here would create a hard cycle.
- The annotation **must** stay quoted (`ctx: "Context"`), and `from __future__ import annotations`
  must **not** be added — the skill loader `exec`s modules without registering them in
  `sys.modules`, which breaks string-annotation resolution for any `@dataclass` in such a file.
- The `getattr(..., None)` form is **preserved verbatim**. The issue flags this specifically: every
  test ctx currently carries a `manager` attribute, so the default is unreachable-by-test
  defensiveness and "simplifying" it to `ctx.manager` would pass every existing test. Not
  simplified.

In the three `tools/*.py` consumers, the local def is replaced by a relative-package import beside
the existing `from .. import …` lines:

```python
from ..events import emit_for_ctx
```

In `skills/project/tools.py` the import **must be absolute**, beside the existing
`from decafclaw import sticky as sticky_mod`:

```python
from decafclaw.events import emit_for_ctx
```

The 11 call sites change only by dropping the leading underscore: `_emit_for_ctx(ctx)` →
`emit_for_ctx(ctx)`. Canvas `44, 59, 70, 85`; sticky `32, 42`; checklist `75, 82`; project
`148, 159`.

**Verification — automated:**
- [x] C1's check passes: `uv run python docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/check_c1_emit_for_ctx.py`
      → `DEFS = 1 ['src/decafclaw/events.py:43']`, `IMPORTS = 4`, `USES = 4`, exit 0
- [x] G1 passes: `uv run pytest tests/test_canvas_tools.py tests/test_sticky_tools.py tests/test_checklist_tools.py tests/test_project_tools.py -q`
      → `69 passed in 1.40s`, exit 0 (same 69 as freeze; none lost, none newly skipped)
- [x] G2 passes: `uv run python docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/guard_g2_skill_loader.py`
      → `emit helper present: True ['emit_for_ctx']` (public spelling, resolved through the real
      loader), `helper semantics OK under loader-exec`, exit 0. This is the guard that would have
      caught a relative import; the absolute import passes it.
- [x] G3 passes: `uv run python docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/guard_g3_no_cycle.py`
      → all six modules import, `imports OK, no cycle`, exit 0
- [x] G4 passes: `uv run pyright` over the four consumers + `events.py` + `context.py`
      → `0 errors, 0 warnings, 0 informations`; and
      `uv run ruff check src/decafclaw/tools/ src/decafclaw/skills/project/ src/decafclaw/events.py`
      → `All checks passed!`, exit 0
- [x] G5 passes: `make test` → `3608 passed, 2 skipped in 17.18s` — byte-identical counts to the
      freeze baseline
- [x] `make check` passes — ruff `All checks passed!`, pyright `0 errors`, message-types drift check
      clean, `tsc --noEmit` clean; ran to completion with no `make: *** Error`

**Verification — manual:**
- None. C1 is fully mechanical and no criterion is human-judgment, which is why the tier is
  `auto-ok`. Nothing here needs eyeballing.

---

## Plan self-review

Ran per `phases/plan.md` step 10. Findings, and what was done about them:

1. **Criteria coverage, both directions** — C1 is the only criterion and Phase 1 advances it fully.
   Phase 1 advances C1, so no phase advances nothing. Phase 0 is the freeze, which the template
   exempts. **Clean, no fix needed.**
2. **Checks cited by command** — every automated checkbox in Phase 1 carries the exact command from
   `checks.md`, not "tests pass". **Clean.**
3. **Placeholder scan** — no TBD/TODO; the helper body and both import forms are shown literally
   rather than described. **Clean.**
4. **Type consistency** — one symbol, one spelling throughout: `emit_for_ctx` (public, no leading
   underscore) in the definition, in all four imports, and in all 11 call sites. The private
   `_emit_for_ctx` spelling appears in this plan only when naming what is being *deleted*.
   **Clean.**
5. **Fixed inline during self-review:** the first draft named only `events.py` and the four
   consumers under **Files**, omitting the stale
   `docs/dev-sessions/2026-07-24-1133-progress-tracker/plan.md:18` line that the issue explicitly
   asks to update in the same pass. Added, with the "annotate as superseded, don't rewrite history"
   decision recorded — an unannotated historical plan that instructs the opposite of current
   convention is exactly the doc-drift Copilot catches.
6. **Scope discipline** — no drive-by cleanup. `events.py` gains one function and one guarded
   import and nothing else. The 11 call sites change only in the name's leading underscore. No test
   is added, and the opt-out is stated with its reason rather than left implicit.

No design decision surfaced that the spec doesn't cover, so per `express` this run continues
without stopping.
