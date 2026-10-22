# issue-1092-checklist-completion-summary

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1092
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1092-checklist-completion-summary
- Branch: issue-1092-checklist-completion-summary
- Base: origin/main at c7786768f0b9215501e73d1efb97055b53666111
- Next action: commit and submit draft PR.

## Log

- Root cause: `checklist_step_done` returned an imperative prompt in its status string ("All N steps complete! Summarize what was accomplished.") which appeared in the tool result UI, while the final no-tools LLM call had no explicit user directive prompting the model for a summary, causing silent/empty turn endings.
- Fix:
  1. `checklist_tools.py`: Return clean declarative status `All {done} checklist steps are complete.`
  2. `agent.py`: When `end_turn_signal` triggers the final no-tools LLM call, append a synthetic user prompt to `final_messages` ("All steps are complete. Please provide a concise summary of what was accomplished and any relevant results.") so the LLM summarizes what was accomplished, without polluting conversation history or the archive.
- Tests:
  - `tests/test_checklist_tools.py`: Updated `test_checklist_step_done_all_complete` to assert the exact declarative completion text.
  - `tests/test_agent_turn.py`: Added `test_run_agent_turn_end_turn_summary_directive` verifying that the final LLM call receives the directive and produces the summary without polluting history.
- Verified: `make check` clean, `make test` (4656 passed).
