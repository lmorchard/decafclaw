# issue-1092-checklist-completion-summary

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1092
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1092-checklist-completion-summary
- Branch: issue-1092-checklist-completion-summary
- Base: origin/main at c7786768f0b9215501e73d1efb97055b53666111
- Next action: push review feedback fix.

## Log

- Root cause: `checklist_step_done` returned an imperative prompt in its status string ("All N steps complete! Summarize what was accomplished.") which appeared in the tool result UI, while the final no-tools LLM call had no explicit user directive prompting the model for a summary, causing silent/empty turn endings.
- Fix:
  1. `checklist_tools.py`: Return clean declarative status `All {done} checklist steps are complete.`
  2. `agent.py`: When `end_turn_signal` triggers the final no-tools LLM call, append a synthetic user prompt to `final_messages` ("Please provide a concise summary of what was accomplished, any relevant results, and any outstanding input.") unless confirmation was denied (`confirm_denied`), ensuring the LLM summarizes accomplishments without claiming full completion when input widgets exit unanswered.
- Tests:
  - `tests/test_checklist_tools.py`: Updated `test_checklist_step_done_all_complete` to assert the exact declarative completion text.
  - `tests/test_agent_turn.py`:
    - `test_run_agent_turn_end_turn_summary_directive`: Verifies directive delivery, history isolation, and archive cleanliness via `restore_history`.
    - `test_run_agent_turn_end_turn_confirm_denied_no_summary_directive`: Verifies denial instruction passed without summary directive.
    - `test_run_agent_turn_unanswered_widget_summary_directive`: Verifies neutral wording when input widget is unanswered without asserting completion.
- Verified: `make check` clean, `make test` passed.
