# Plan — writing-clearly plan-then-execute

Implementation plan for the spec at `./spec.md`. One PR scope. Commit per phase.

## Phase 1 — Rewrite the child prompt template as a planner prompt

Goal: child returns a JSON plan, not revised prose.

1. In `contrib/skills/writing-clearly/tools.py`, replace `_CHILD_PROMPT_TEMPLATE` with a planner prompt that:
   - Establishes the persona (Strunk-style copy editor producing a plan, not a revision).
   - Puts `<draft>` at the top, immediately under the persona instructions (carries forward the small-model attention lesson from v1).
   - Tells the child to produce a structured plan only — every entry includes `kind`, `rule`, `before`, `after`, `note`.
   - **Critical** rule: `before` MUST be copied **verbatim** from `<draft>`, character-for-character, including whitespace, punctuation, and any markdown formatting. The tool will apply edits via exact string match — any deviation makes the entry unmatchable.
   - Forbids overlapping/cascading entries unless ordered (later entries may target earlier `after` text, applied in plan order).
   - Tells the child to return an empty `edits` array if the draft is already clean by Strunk's standards.
2. Build the `return_schema` dict matching the plan shape from the spec:
   ```python
   _RETURN_SCHEMA = {
       "summary": "one-line description",
       "edits": [
           {
               "kind": "substitution|rewrite",
               "rule": "Strunk rule name",
               "before": "verbatim text from draft",
               "after": "replacement text",
               "note": "short rationale",
           }
       ],
   }
   ```
3. Pass `return_schema` to `tool_delegate_task(ctx, task=..., return_schema=_RETURN_SCHEMA)`.

Commit: `feat(writing-clearly): replace child template with planner prompt`

## Phase 2 — Implement the deterministic apply step

Goal: tool code converts a parsed plan into a revision via string replace.

1. Add `_apply_plan(draft: str, edits: list[dict]) -> tuple[str, list[dict], list[dict]]`:
   - Iterates `edits` in order.
   - For each entry, validates `before` is non-empty and present in the current working text. Skip with reason if not.
   - Replaces **first occurrence** of `before` with `after` in working text.
   - Returns `(revised_text, applied_entries, skipped_entries)`.
   - `skipped_entries` keep the original plan entry with an added `_skip_reason` field (`"before_not_found"`, `"before_empty"`, or `"noop"` if `before == after`).
2. In `tool_edit_with_strunk`:
   - Publish a "planning" progress event before delegating: `ctx.publish("tool_status", tool="edit_with_strunk", message="Planning edits...")`.
   - After awaiting `tool_delegate_task`, inspect the returned `ToolResult`.
   - If `result.data` is `None` (parse failure): v1 fallback — return the result unchanged (prose-only). Log a debug message about the fallback.
   - If `result.data` is present: pull `summary` and `edits`. Publish an "applying N edits" event. Call `_apply_plan(draft, edits, publish=ctx.publish)` so the apply step can emit per-entry progress. Build new `ToolResult` with `text=revised_text` and `data={"summary": ..., "applied": [...], "skipped": [...]}`. Publish a final "done" event summarizing applied/skipped counts.
   - If `edits` is empty: return draft unchanged with a `data` payload noting "no edits proposed." Publish a "no edits proposed" event.
3. `_apply_plan` accepts an optional `publish` callable. When provided, before each successful replacement it emits `await publish("tool_status", tool="edit_with_strunk", message=f"Applying {entry['rule']}: {short_before}→{short_after}")` where `short_before`/`short_after` are truncated to ~40 chars so the event message stays compact. Skipped entries emit a similar event with a skip reason. No-op when `publish` is None (keeps `_apply_plan` testable in isolation).

Commit: `feat(writing-clearly): deterministic plan-apply step in tool code`

## Phase 3 — Tests

Goal: cover the apply step deterministically and the v1 fallback.

1. Add unit tests at `contrib/skills/writing-clearly/test_apply.py` (mirroring the contrib/kindle test pattern):
   - `_apply_plan` with one substitution entry produces the expected revision.
   - `_apply_plan` with multiple ordered entries (later targets earlier `after` text) applies in order.
   - `_apply_plan` with a `before` that doesn't appear in the draft → skipped, working text unchanged.
   - `_apply_plan` with `before == after` → skipped as noop.
   - `_apply_plan` with empty `edits` → working text unchanged.
   - `_apply_plan` with `before` appearing multiple times → only the first occurrence replaced; a second entry can target the second occurrence.
2. Wire into `make test-contrib` discovery (it already picks up `contrib/**/test_*.py` per kindle precedent — verify).
3. Run `uv run pytest contrib/skills/writing-clearly/ -v` to confirm tests pass.

No new tests for the delegate path (it's exercised end-to-end by the smoke test in Phase 5; mocking delegate_task in unit tests is brittle and the v1 PR already validated the plumbing).

Commit: `test(writing-clearly): unit tests for deterministic plan-apply`

## Phase 4 — Documentation

Goal: SKILL.md and contrib README reflect the new behavior.

1. Update `contrib/skills/writing-clearly/SKILL.md`:
   - Replace the "How to use" section with a description of the plan-then-execute flow.
   - Document the new `ToolResult.data` shape (`summary`, `applied`, `skipped`).
   - Keep the existing "What to pass as `draft`" guidance — still applies.
   - Add a short "How edits are applied" section explaining the deterministic string-replace approach and the implications (verbatim `before`, no second LLM pass, skipped entries logged).
2. Update `contrib/skills/writing-clearly/README.md` (the human-facing one) to mention the structured return.
3. No change needed to `contrib/skills/README.md` (install instructions unchanged).

Commit: `docs(writing-clearly): document plan-then-execute behavior and return shape`

## Phase 5 — Smoke test

Goal: end-to-end validation against a real piece of prose.

1. Run a smoke test using the Agent tool (mirroring the v1 smoke test): spawn a child with the new planner prompt + a paragraph from one of Les's blog posts, get back the structured plan.
2. Manually verify: every entry in `data.applied` corresponds to a visible change in the revision (substitutions appear as expected; rewrites replace the full source sentence).
3. Check the `skipped` list — ideally empty on a clean run. Non-empty skipped lists in smoke testing reveal planner-prompt issues (whitespace drift, markdown handling) to iterate on before merging.

Capture findings in `notes.md` — what worked, what surprised us, what the planner got wrong.

No commit unless the smoke test surfaces a needed prompt fix.

## Phase 6 — Lint, typecheck, PR

1. `make lint` and `make typecheck` clean.
2. `make test` clean — full suite, in case `_apply_plan` collides with anything.
3. Push branch, open PR against `main` with `Closes #545` in the body.
4. PR body covers: motivation (single self-reported edit list = hallucination risk), architecture (planner LLM + deterministic apply), failure modes (v1 fallback, skipped entries), and the smoke-test result.

## Out of scope (deferred to follow-ups)

- Plan editing/approval UI (let users approve/reject entries before apply).
- Multiple-pass iteration (apply plan, then re-plan against the revision).
- Verification that the planner didn't omit visible passages — currently we trust the plan to be complete; entries that aren't in the plan won't get edited.

## Risks revisited from spec

- **Planner produces non-verbatim `before` substrings.** Mitigation: very explicit instruction in the planner prompt + smoke-test iteration. Worst case skipped entries are logged, not silent — caller can see and react.
- **Whitespace/markdown drift in `before`.** Same mitigation. The skipped list surfaces this clearly.
- **Plan JSON bloat for long drafts.** Acceptable — we measure during smoke test and revisit if it bites.
