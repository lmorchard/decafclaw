# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/657
**Frozen at:** `39f5ff5` (2026-07-29)
**Branch base:** `origin/main` @ `637a8db`

**Check files — read-only from Phase 1 onward:**
- `docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/check_c1_emit_for_ctx.py`
- `docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/guard_g2_skill_loader.py`
- `docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/guard_g3_no_cycle.py`

The issue states C1's check as a *described* probe ("a stdlib-only static probe that counts …")
rather than a literal command line, so the probe script had to be authored at freeze. It was
written by a **check-author subagent** given the criterion text and the loud-fail / host-agnostic
constraints, and explicitly **not** given any implementation approach — the plan below was written
after the freeze, for that reason. G2 and G3 are likewise described-not-literal in the issue and
were authored the same way. G1 and G4 *are* literal commands in the issue and are reproduced here
byte-identically.

Because `Check files` is non-empty, the real `git diff <freeze-sha> -- <check files>` tamper check
applies to C1/G2/G3 rather than the command-only substitutes.

---

## C1

CRITERION: The codebase SHALL contain exactly one definition of the `emit_for_ctx` helper, AND each
of the four consumer modules SHALL obtain it by import rather than by local definition.

CHECK (single exit-coded command, run from the repo root):
```
uv run python docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/check_c1_emit_for_ctx.py
```
a stdlib-only static probe that counts `def _?emit_for_ctx` definitions under `src/decafclaw`,
counts which of the four named consumers import the symbol, and counts which still call it —
asserting `DEFS == 1`, `IMPORTS == 4`, `USES == 4`.

AT FREEZE: **fails, for the correct reason** — exit 1, genuine `AssertionError` on the definition
count, not an import error / typo'd path / missing fixture:

```
DEFS = 4 ['src/decafclaw/skills/project/tools.py:98', 'src/decafclaw/tools/canvas_tools.py:22',
          'src/decafclaw/tools/checklist_tools.py:27', 'src/decafclaw/tools/sticky_tools.py:21']
IMPORTS = 0 []
USES = 4 [all four consumers]
AssertionError: expected 1 definition, found 4
exit=1
```

This reproduces the issue's `VERIFIED DISCRIMINATING` record exactly (`DEFS = 4`, `IMPORTS = 0 []`,
`USES = 4`, exit 1), so the gap the issue describes is still present at `637a8db`.

**Teeth, verified at freeze by the check-author:**
- Adding an import while keeping the local definition leaves `DEFS` at 4; the definition assertion
  fires first, before `IMPORTS` is consulted. Cannot be satisfied by adding imports on top.
- Removing all calls from any one consumer drops `USES` to 3 and fails, naming the file.
- Host-agnostic: `decafclaw.events`, `decafclaw.context`, an aliased import, and a brand-new module
  all register as imports. No host module name appears in the probe.
- A `def` line counts as neither an import nor a call, so a local definition cannot masquerade as
  half-satisfying the criterion.

**Known granularity limit, recorded rather than papered over:** `USES` is a count of *consumers that
still call*, not of call expressions — which is what the criterion says ("counts which of the four
named consumers … still call it") and what the triage run's `USES = 4` over four files reports. So
deleting *one of several* calls inside a file that has others leaves `USES` at 4. C1 is therefore not
a per-call-site regression guard; **G1 is** — `tests/test_canvas_tools.py` asserts the emit wiring is
actually awaited. This is the criterion-plus-guard pair the issue says not to freeze one half of.

---

## Guards

Pass today; must keep passing. Not criteria — they can't fail at freeze.

- **G1** (the behavioural pair for C1) — literal command from the issue:
  `uv run pytest tests/test_canvas_tools.py tests/test_sticky_tools.py tests/test_checklist_tools.py tests/test_project_tools.py -q`
  Protects the helper's semantics on both branches: `tests/test_canvas_tools.py` asserts
  `manager_mock.emit.assert_awaited_once()` (manager-present), and `tests/test_project_tools.py`
  builds its ctx with `manager=None` plus `test_emit_failure_is_fail_open` (None / fail-open).
  **AT FREEZE: passes — `69 passed in 1.64s`, exit 0.**
  (Triage observed 67; `origin/main` has advanced to `637a8db` since, adding two. The invariant is
  "no test lost, newly skipped, or newly failing" — **69 is this run's baseline**, not 67.)

- **G2** (skill-loader import path — the `CLAUDE.md:63` rule):
  `uv run python docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/guard_g2_skill_loader.py`
  Execs the project skill's `tools.py` through the real loader (`_import_tools_module` →
  `spec_from_file_location`, no package context) and confirms the helper resolves *and* behaves on
  both branches. Catches the specific regression this refactor invites: a **relative** import would
  satisfy C1 and pass an ordinary package import, but break under the real loader.
  **AT FREEZE: passes — `loader-path exec OK`, `emit helper present: True ['_emit_for_ctx']`,
  `helper semantics OK under loader-exec`, exit 0.**

- **G3** (no import cycle):
  `uv run python docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/guard_g3_no_cycle.py`
  Imports all four consumers plus `decafclaw.events` and `decafclaw.context` in a fresh interpreter.
  `events.py` imports nothing from `context`/`tools` today, so hosting the helper there is cycle-free;
  this pins that.
  **AT FREEZE: passes — `imports OK, no cycle`, exit 0.**

- **G4** (types/lint on touched files) — literal commands from the issue:
  `uv run pyright` over the four consumers + `events.py` + `context.py`, and
  `uv run ruff check src/decafclaw/tools/ src/decafclaw/skills/project/ src/decafclaw/events.py`.
  Live concern from the issue: the helper's `ctx: "Context"` annotation sits behind `TYPE_CHECKING`,
  so the shared version must keep its forward ref resolvable from the new host.
  **AT FREEZE: passes — pyright `0 errors, 0 warnings, 0 informations`; ruff `All checks passed!`.**

- **G5** (full-suite invariant): `make test` — no test lost, newly skipped, or newly failing.
  The issue marked this **UNRUN** and said not to read it as verified. **It has now been run:**
  **AT FREEZE: passes — `3608 passed, 2 skipped in 21.40s`.** That is this run's baseline.

---

## Pre-squash tamper verdict

Recorded here because `git reset --soft origin/main` collapses the freeze commit away: afterwards
`39f5ff5` is a dangling local object, not an ancestor of the branch and absent from `origin`, so
nobody downstream can re-run the command. **This record is the evidence.**

**Verdict: `clean`** — and clean by the real mechanism, not by substitute. `Check files` is non-empty
(three authored probe scripts), so the genuine tamper diff applies:

```
git diff 39f5ff5 -- <the three Check files>   →  empty (0 bytes)
```

None of `check_c1_emit_for_ctx.py`, `guard_g2_skill_loader.py`, `guard_g3_no_cycle.py` changed after
the freeze. Independently confirmed by the verifier subagent, which ran the same diff from a fresh
context.

`git diff 39f5ff5 --stat` — 10 files, all accounted for, **no file under `tests/`**:

| File | Accounted for as |
|---|---|
| `src/decafclaw/events.py` | the single new definition (+ its docstring) |
| `src/decafclaw/tools/{canvas,checklist,sticky}_tools.py` | consumers: local def out, import in, calls renamed |
| `src/decafclaw/skills/project/tools.py` | same, with the absolute import form |
| `CLAUDE.md` | key-files line for `events.py` (self-review addition) |
| `docs/dev-sessions/2026-07-24-1133-progress-tracker/plan.md` | the stale "replicate locally" line, annotated superseded |
| `docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/plan.md` | this session's plan — checkboxes ticked from observed output |
| `docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/notes.md` | this session's notes. **Added after the first draft of this table said "9 files"** — the miscount was caught by the post-squash verifier re-run, not by me, and is corrected here rather than left standing. A tamper record that miscounts its own diff is worth less than one that doesn't. |
| `docs/dev-sessions/2026-07-29-1541-657-emit-for-ctx/checks.md` | this manifest: the sanctioned `Frozen at` sha append, plus this section. No CRITERION line, CHECK command, or guard command differs from the freeze version. |

**Could the diff satisfy C1 without doing the work?** Assessed by the verifier, since the probe is a
static text probe and therefore the gameable kind. Answer: **no, concretely not** on this diff. The
extracted body is the same three statements in the same order as the four removed copies
(`getattr(ctx, "manager", None)` → `if manager is None: return None` → `return manager.emit`); the
only changes are the dropped underscore and an added docstring. All 10 call sites remain in the
`emit=` keyword position they held before — no result discarded, no `emit=` argument dropped or
replaced with a literal. The theoretical cheap pass (stub helper + unused imports + broken wiring)
is what G1 and G2 exist to catch, and both ran green **unmodified**.

## Amendments

Append-only. **Empty** — no amendment was made during this run, so the `auto-ok` tier stands
undowngraded.
