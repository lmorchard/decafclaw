# Session Notes

## Summary

Built a project skill for structured multi-step workflows. The core
infrastructure (state machine, plan parser, eval system improvements) is
solid. However, getting Gemini to follow the workflow reliably proved
extremely difficult — the model chains tool calls regardless of prompt
instructions or mechanical enforcement.

**Decision: pause the project skill and investigate #128 (direct Gemini API
support) to determine if LiteLLM's translation layer is the root cause.**

## What works

- State machine with 6 phases and enforced transitions
- Plan parser (checkboxes, sub-steps, auto-numbering, trailing content)
- Review gates via request_confirmation
- Concurrent eval runner with auto-confirm
- max_tool_errors assertion for catching kwargs hallucination
- Tool deferral with max_active_tools threshold
- Skill tools sorted first in tool list
- Custom confirmation button labels (Approve/Needs Feedback)
- Conversation history capture in eval results

## What doesn't work (Gemini-specific)

- **Runaway tool chaining:** Gemini calls tools in a loop until max_iterations,
  ignoring STOP instructions in tool returns, SKILL.md guidance, and even
  mechanical rate limiters. Tried: prompt engineering, tool separation
  (next_task/task_done), minimal return messages, rate limiting. None worked.
- **Kwargs hallucination:** Gemini invents parameter names (title, spec,
  project_id, project_slug) despite correct schemas in the tool definitions.
  Persists across all naming strategies we tried.
- **Phase skipping:** Gemini tries to skip review states and compress
  multi-phase workflows into single turns.

## Architecture evolution

1. **v1:** SKILL.md-driven workflow with behavioral guidance
2. **v2:** Mechanical review gates (request_confirmation)  
3. **v3:** Express mode removed, stateful current project
4. **v4:** project_next_task as workflow driver (tools drive workflow, not SKILL.md)
5. **v5:** project_next_task + project_task_done split (informational vs advancement)

Each iteration improved eval scores but couldn't fully prevent runaway chaining.

## Key insight

**Tool descriptions are a control surface, but tool call chaining behavior
is model-level.** Prompt engineering can influence what the model does, but
cannot prevent a model from continuing to call tools. This is likely either
a Gemini model behavior or a LiteLLM translation artifact (#128).

## Issues filed during session

- #233 — Think tool evaluation
- #234 — Todo tools redundancy with project skill
- #235 — Skill activation approval in wrong conversation
- #236 — Shell output size cap
- #237 — Build bundled gh-cli skill
- #238 — LLM hallucinated kwargs
- #239 — Concurrent eval execution (implemented)
- #240 — Eval coverage audit
- #241 — Background process completion notifications
- #242 — GitHub issue-backed dev sessions
- #243 — Flexible multi-choice confirmation UI
- #244 — Tool deferral when skills active (partially implemented)
- #245 — set_effort needs user confirmation (P0)
- #246 — Tool priority system
- #247 — Move rarely-used core tools to skills
- #248 — Expand effort system for arbitrary model selection

## Eval system improvements (shipped in this PR)

- `setup.skills` for pre-activating skills in evals
- `setup.auto_confirm` for controlling review gate behavior
- `max_tool_errors` assertion
- Error detail reporting in failures
- `_collect_tool_errors` for extracting error messages
- Concurrent execution with `--concurrency` flag
- Auto-confirm/deny via EventBus subscriber
- Full conversation history capture on failures
- Progress output during concurrent runs

## Infrastructure improvements (shipped in this PR)

- `max_active_tools` config (default 30, triggers deferral)
- Skill tools sorted first in tool list
- Custom button labels in confirmation UI (approve_label, deny_label)
