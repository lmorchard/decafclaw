# Session notes — #595/#596 skill validation

## Summary

Implemented two related fixes from the `web-lmorchard-fa5ec853` postmortem (an agent
burned ~150 messages because a workspace skill it authored — missing YAML frontmatter —
was silently rejected on every `refresh_skills`):

- **#595** — `refresh_skills` now reports skills found-but-rejected during discovery, with
  the reason, under a `Rejected (found but not loaded):` section.
- **#596** — new `skill_validate(path)` pre-flight lint tool: validates one workspace skill
  dir deeply (imports `tools.py` to catch `SyntaxError`/`NameError`/`ImportError`,
  introspects but does not call `get_tools`'s signature, checks `tools.py` vs `main.py`),
  returns a ✓/✗ checklist as a `ToolResult` with structured `data`.

**Core design (the load-bearing decision):** one `validate_skill_md()` is the single source
of truth for the discovery-level accept/reject decision. `parse_skill_md` became a thin
`validate → build_skill_info` wrapper (behavior unchanged); `discover_skills` and
`skill_validate` both route through `validate_skill_md`. Rejection reasons and lint checks
therefore cannot drift from the actual accept/reject decision — the exact failure mode the
postmortem warned about.

## What shipped

| Commit | What |
|--------|------|
| `31301df` | refactor: extract `validate_skill_md` / `build_skill_info`; `parse_skill_md` → wrapper |
| `489a95e` | feat: `refresh_skills` reports rejections (#595) via optional `rejections` accumulator threaded through `discover_skills` → `load_system_prompt` |
| `fe33ee8` | feat: `skill_validate` tool (#596) + `docs/skills.md` |
| `17ccd2d` | test: assert `skill_validate` no-ctx message mentions ctx |
| `8546589` | test(evals): `tool_choice` case `skill_validate` vs `refresh_skills` |
| `c29ec62` | test(prompts): update `discover_skills` monkeypatch fakes for the new `rejections` kwarg |

Plus the spec/plan doc commits (`f69154e`, `cd2ebe0`).

## Verification

- `make check` green (ruff, pyright 0 errors, message-types drift, tsc).
- Full `make test`: **2942 passed**.
- `make eval-tools`: the new `skill-validate-vs-refresh-preflight` case passed (model chose
  `skill_validate` over `refresh_skills`, no overlap). 3 unrelated pre-existing eval failures
  (tabstack / ask_user_multiple_choice) — out of scope.

## Architecture / process notes

- Execution was subagent-driven: 4 implementer tasks, a spec+quality review after each, and
  an opus whole-branch review at the end. Final verdict: ready to merge, no Critical/Important.

## Lessons / what to carry forward

- **Per-task test scope missed an integration break.** Implementers ran only
  `tests/test_skills.py` (as the plan's per-task steps said). The signature change to
  `load_system_prompt` broke 3 `tests/test_prompts.py` tests whose monkeypatched
  `discover_skills` fakes didn't accept the new `rejections` kwarg. The controller's final
  full `make test` caught it (fixed in `c29ec62`). **Carry-forward:** when a task changes a
  shared function signature, the per-task test step should run the suite for *every file that
  patches or calls that function*, not just the feature's own test file. A grep for the
  patched symbol across `tests/` would have flagged `test_prompts.py` up front.
- The optional-accumulator plumbing (vs. changing return shapes) paid off: zero churn across
  the ~dozen `discover_skills` tests and the other `load_system_prompt`/`discover_skills`
  call sites. The only fallout was the monkeypatch fakes above — inherent to any signature
  change, not the accumulator choice.

## Open / deferred (Minor findings, reviewer verdict = leave)

1. `SkillValidation.ok` / `_render_validation` are vacuously `True` on an empty checks list
   (`all([])`). Unreachable from current callers (both always append ≥1 check first). Latent
   foot-gun for future direct construction.
2. `_lint_tools_py` ignores a stray `main.py` when `tools.py` also exists (only warns when
   `tools.py` is absent). Harmless — the correct `tools.py` is used.
3. `test_validate_missing_description` doesn't assert the `name` check passed (its sibling
   `test_validate_missing_name` does assert short-circuit order). Cheap symmetry add.
4. Frontmatter rejection message ("must start with a '---' block") is slightly imprecise for
   the malformed-YAML-inside-delimiters case; the parse detail is still logged by
   `_split_frontmatter`, just not surfaced in the checklist.

## Related, not in scope (same postmortem cluster)

- **#597** — surface the authoring contract in-context at skill-creation time.
- **#598** — agent diagnostic-discipline guardrails (loop-breaker, verify-tool-fired,
  apology spiral) + a "pivots to diagnosis on an unloadable skill" behavior eval. The bigger,
  fuzzier, model-conditional one — its own session.
