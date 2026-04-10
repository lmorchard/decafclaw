# Plan: Project Skill — Workflow Control

## Overview

Phases 1-2 from the original plan are done (end_turn bool, dynamic tools, eval fixes).
The remaining work upgrades `end_turn` to support `EndTurnConfirm` and updates the
project skill to use confirmation gates instead of conversational review.

## Steps

### Phase A: EndTurnConfirm dataclass and agent loop

- [ ] 1. Add `EndTurnConfirm` dataclass to `media.py`
  - [ ] 1.1. Fields: `message`, `approve_label`, `deny_label` (with defaults)
  - [ ] 1.2. Change `ToolResult.end_turn` type annotation to `bool | EndTurnConfirm = False`

- [ ] 2. Thread `EndTurnConfirm` through tool execution in `agent.py`
  - [ ] 2.1. `_execute_single_tool`: already returns `(tool_msg, end_turn)` — the `end_turn` value can now be `EndTurnConfirm`, no change needed
  - [ ] 2.2. `_execute_tool_calls`: currently aggregates `any_end_turn` as a bool. Change to also capture the first `EndTurnConfirm` object if present. Return it alongside the bool.

- [ ] 3. Handle `EndTurnConfirm` in the agent loop iteration
  - [ ] 3.1. After `_execute_tool_calls`, check if the end_turn signal is an `EndTurnConfirm`
  - [ ] 3.2. If so: publish a `tool_confirm_request` event via the event bus (same pattern as `request_confirmation`), wait for response
  - [ ] 3.3. If approved: inject a system/note message into history (e.g. "User approved the review"), continue the loop (next iteration)
  - [ ] 3.4. If denied: make final no-tools LLM call, end the turn
  - [ ] 3.5. If plain `end_turn=True`: existing behavior (final no-tools LLM call, end turn)

- [ ] 4. Write tests for EndTurnConfirm
  - [ ] 4.1. Test that `EndTurnConfirm` propagates from tool through batch
  - [ ] 4.2. Test that approval continues the loop
  - [ ] 4.3. Test that denial ends the turn

### Phase B: Update project skill

- [ ] 5. Update `project_task_done` to use `EndTurnConfirm`
  - [ ] 5.1. BRAINSTORMING → SPEC_REVIEW: return `EndTurnConfirm(message="Spec review...", approve_label="Approve", deny_label="Needs Feedback")`
  - [ ] 5.2. PLANNING → PLAN_REVIEW: same pattern
  - [ ] 5.3. On denial: revert state (SPEC_REVIEW→BRAINSTORMING, PLAN_REVIEW→PLANNING)
  - [ ] 5.4. On approval: advance state (SPEC_REVIEW→PLANNING, PLAN_REVIEW→EXECUTING)
  - [ ] 5.5. Remove `request_confirmation` import and `_request_review` helper
  - [ ] 5.6. EXECUTING → DONE: keep `end_turn=True` (simple end)
  - [ ] 5.7. Remove SPEC_REVIEW and PLAN_REVIEW branches from task_done (the confirmation handles the transition in the agent loop callback)

- [ ] 6. Update `project_update_spec` and `project_update_plan`
  - [ ] 6.1. No end_turn — return plain text instructing model to present and call task_done
  - [ ] 6.2. Include artifact content in the return for presentation

- [ ] 7. Update project skill tests
  - [ ] 7.1. Test that task_done from brainstorming returns EndTurnConfirm
  - [ ] 7.2. Test that approval advances state
  - [ ] 7.3. Test that denial reverts state
  - [ ] 7.4. Test get_tools per phase (SPEC_REVIEW/PLAN_REVIEW exclude project_next_task)

### Phase C: Evals and docs

- [ ] 8. Update evals
  - [ ] 8.1. Restore review denial test (auto_confirm: false)
  - [ ] 8.2. Adjust max_tool_calls for auto-confirm chaining
  - [ ] 8.3. Run evals on Gemini Flash, verify pass rate

- [ ] 9. Update docs
  - [ ] 9.1. `docs/skills.md`: document EndTurnConfirm alongside end_turn
  - [ ] 9.2. `CLAUDE.md`: update convention

- [ ] 10. Lint, test, commit, push
