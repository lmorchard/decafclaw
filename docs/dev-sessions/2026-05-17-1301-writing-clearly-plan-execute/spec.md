# writing-clearly: plan-then-execute editing

Closes #545.

## Problem

The current `edit_with_strunk` tool delegates editing to a child agent which returns only the revised prose. There's no way to tell which Strunk rules were applied where. Returning a self-reported edit list alongside the prose would not solve this — the report is a hallucination risk since the model that produced the revision is also reporting on it, with no ground truth.

## Goal

Restructure the skill so the **edit plan IS the ground truth** and the revision is mechanically derived from the plan. The tool returns both, with the plan auditable against the visible changes in the revision.

## Architecture (resolved from #545 design Q&A)

**One LLM call.** The child agent acts as a **planner only**: it produces a structured plan and nothing else. The "execute" phase runs in tool code, not in another LLM call.

**Every plan entry carries its full before/after, including structural rewrites.** A "substitution" entry replaces a substring; a "rewrite" entry replaces a full sentence or passage. Both are applied identically — string replace. The planner commits to the exact rewritten text upfront, so there's nothing to drift.

This collapses the "hybrid: deterministic vs LLM execution" question: the planner does the cognitive work of writing the rewrite *as part of planning*, and tool code applies all entries deterministically. Strunk's structural rules (related words together, emphatic words at end) are handled the same way as substitutions — just with a longer `before` and `after`.

### Why not a second LLM call for structural edits

The original "hybrid" framing assumed structural edits would need a follow-up LLM pass because the planner couldn't write the rewrite at planning time. That's not true — the planner has already decided what the rewrite should be (otherwise it can't commit to including the entry in the plan). Writing it down is cheaper than re-invoking the LLM with the same context.

Tradeoff accepted: plan output is larger (each structural entry contains the full rewritten sentence). In exchange we get a fully deterministic execution phase, no second-call cost, no plan/revision drift, and a plan that's self-contained as a teaching artifact or audit trail.

### Why not enforce plan-then-execute by structuring the prompt

We considered having one delegate_task whose prompt walks the model through "first produce plan, then apply it." That's softer than tool-code enforcement — the model can still produce a revision that doesn't match its plan, and we'd have to detect and reconcile. Putting the apply step in tool code makes the relationship enforceable: the revision is literally `apply(plan, draft)`.

## Plan shape

```json
{
  "summary": "One-line description of the editing pass.",
  "edits": [
    {
      "kind": "substitution",
      "rule": "Rule 13 — Omit needless words",
      "before": "Things got done fast",
      "after": "Work got done fast",
      "note": "'Things' is vague."
    },
    {
      "kind": "rewrite",
      "rule": "Rule 18 — Place emphatic words at end of sentence",
      "before": "This is a really important point that you should remember.",
      "after": "Remember this: it's the point that matters.",
      "note": "Reordered to land 'matters' at the end."
    }
  ]
}
```

`kind` is informational only (lets the UI/audit distinguish surgical edits from sentence rewrites). Both kinds are applied identically.

## Execution algorithm

1. Call `delegate_task` with a planning prompt + `return_schema` matching the plan shape.
2. Parse `ToolResult.data` from the child. If parse fails, fall back to returning the child's raw text as the revision (existing fail-open behavior — degrades to v1).
3. For each entry in `plan.edits`:
   - If `entry.before` is found in the current working text: replace the **first** occurrence with `entry.after`. Track success.
   - If not found (planner hallucinated a passage that doesn't exist): skip the entry, record it in a `skipped` list, log a warning. Do not error.
4. Return `ToolResult(text=revised_prose, data={"plan": plan, "applied": [...], "skipped": [...]})`.

## Tool surface

`edit_with_strunk(draft, focus="")` — same name, same args (per Q&A: "replace in place"). New return shape:

- `ToolResult.text` = revised prose (pasteable, same as before).
- `ToolResult.data` = `{summary, edits, applied, skipped}`. Auto-rendered as a JSON block alongside the prose so the parent agent sees both and can summarize for the user.

## Failure modes and behaviors

| Scenario | Behavior |
|---|---|
| Child returns malformed JSON | Existing delegate_task fallback returns prose-only. We treat it as v1 behavior. `data` is empty/None. |
| Plan is valid but `before` substring doesn't appear in draft | Skip the entry, list in `data.skipped`, continue. |
| Plan is empty (`edits: []`) | Return the draft unchanged. Valid Strunk outcome: "draft already clean." |
| Same `before` substring appears multiple times | Replace only the first occurrence per entry. Multiple plan entries can target later occurrences if needed. |
| Plan entries conflict (one entry's `after` is another entry's `before`) | Apply in plan order. Document this as an authoring constraint on the planner prompt: order matters; cascading rewrites must be ordered. |
| Empty `draft` | Existing guard returns `[error: draft is required]` before delegation. |

## Out of scope

- Plan editing/approval UI (let users approve/reject individual entries). Future work; the plan-as-ground-truth structure enables it but doesn't require it now.
- Multiple Strunk passes (e.g., one for verbs, one for sentence structure). The `focus` parameter still biases the planner, but we don't iterate.
- Deterministic verification that the revision matches `apply(plan, draft)` — by construction it does, since tool code is the applier. No verification step needed.

## Acceptance criteria

- [ ] `edit_with_strunk` returns `ToolResult` with `text` = revised prose and `data` = `{summary, edits, applied, skipped}`.
- [ ] Plan entries with kind=substitution and kind=rewrite are both applied by deterministic string replace.
- [ ] Smoke test against a sample paragraph: every visible change in the revision corresponds to exactly one entry in `data.applied`, and the revision matches `apply(applied, draft)`.
- [ ] Parse failures degrade to prose-only output without raising (v1 fallback path).
- [ ] SKILL.md documents the plan-then-execute behavior and the v1 fallback.
- [ ] Existing import / lint / typecheck / module-load tests still pass.
- [ ] One new test exercising the deterministic apply step (plan in → known revision out).

## Risks I want to flag before planning

1. **Planner output bloat.** Putting full rewrites in every structural entry inflates the plan JSON. For a long draft with many structural edits, the child response could approach 1k–2k tokens of plan alone. Tolerable; worth measuring on smoke tests.
2. **Whitespace and punctuation sensitivity.** String replace is exact-match. Planner producing `before: "Things got done fast"` will not match `before: "Things  got done fast"` (double space) in the draft. We'll need the planner prompt to copy `before` substrings verbatim from the draft, including whitespace.
3. **Markdown-aware editing.** The draft may contain `**bold**`, links, code spans. The planner needs to preserve those exactly in `before`/`after`. Worth a callout in the planner prompt.
