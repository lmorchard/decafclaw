# Session Notes: Project Skill — Workflow Control

## Recap

Resumed PR #232 (project skill) after confirming that the runaway tool-chaining
problem was model behavior, not a LiteLLM artifact. Built three mechanical workflow
control mechanisms for the agent loop:

1. **`end_turn=True` on ToolResult** — ends the agent turn with a final no-tools LLM call
2. **`EndTurnConfirm` on ToolResult** — shows Approve/Needs Feedback buttons via event bus;
   approval continues the loop, denial ends the turn. Carries `on_approve`/`on_deny`
   callbacks for state transitions.
3. **Dynamic skill tools via `get_tools(ctx)`** — skills supply different tools per turn
   based on state, refreshed each iteration via `_refresh_dynamic_tools()`

Updated the project skill to use all three: spec/plan updates trigger `EndTurnConfirm`
directly (one tool call = write artifact + show review buttons), execution tools chain
freely, dynamic tools gate phase-inappropriate tools.

Also fixed: eval runner per-turn tool counting (was cumulative), streaming buffer flush
before confirmation events (web UI), accumulated text re-dump in end_turn responses.

Filed #255 (first-class workflow abstraction), #258 (confirmation widget lost on reload),
commented on #256 (canvas panel for artifact presentation).

## Divergences from plan

The original plan had 6 phases. We ended up doing significantly more iteration:

- **Review gates went through 4 designs:** (1) conversational review (model interprets
  "looks good" → too fragile), (2) `request_confirmation` restored (model still
  misinterpreted questions as approval), (3) `EndTurnConfirm` as end-of-turn action
  (clean separation), (4) `EndTurnConfirm` on artifact update tools directly (eliminates
  the stall where model doesn't call `task_done` after writing spec)

- **`end_turn` on spec/plan updates was added then removed then replaced** with
  `EndTurnConfirm`. The discovery: `end_turn=True` stalls the conversation (user
  doesn't know what to do), but removing it causes runaway chaining in evals.
  `EndTurnConfirm` solved both: buttons provide clear UX, approval continues the loop.

- **Presentation LLM call before confirmation** wasn't in the original plan. Discovered
  during manual testing that the confirmation buttons appeared before the model had a
  chance to present the artifact. Added a no-tools LLM call before the confirmation event.

- **Streaming buffer flush** wasn't planned — discovered the web UI was truncating
  artifact presentation because the streaming buffer wasn't finalized before the
  confirmation event fired.

## Insights

### The core lesson: control flow must be mechanical, not verbal

This was the session's thesis, drawn from agent framework literature (LangGraph,
Anthropic's "Building Effective Agents", CoALA). We proved it empirically:

- "STOP" in tool results: ignored by Gemini Flash (62 tool calls in one turn)
- "Present the spec, then call task_done": model often just presents and stops
- "The user's response is your review signal": model interprets questions as approval

Every attempt to control flow via prompt text failed on at least one model or one
prompt variation. Mechanical controls (end_turn, EndTurnConfirm, dynamic tools)
worked 100% of the time.

### EndTurnConfirm is a workflow primitive, not just a project skill feature

The `EndTurnConfirm` with `on_approve`/`on_deny` callbacks is a general-purpose
mechanism. Any skill that needs a human-in-the-loop gate can use it. The agent loop
handles the confirmation mechanics; the skill just declares what it needs.

### Dynamic tool loading needs careful seeding

Bug found: when a skill with `get_tools()` is first activated, the static `TOOLS`
dict is loaded into `ctx.tools.extra`. The first `_refresh_dynamic_tools()` call
didn't know about these names, so stale tools persisted. Fix: seed
`dynamic_provider_names` at activation time with the full static tool set.

### Artifact update tools should own the review gate

Relying on the model to call `task_done` as a second step after `update_spec` was
unreliable. The model often produced text without a tool call, ending the turn
without triggering the review buttons. Fix: `update_spec` and `update_plan` now
advance state and return `EndTurnConfirm` directly — one tool call does everything.

### Eval auto-confirm changes the pacing but not the correctness

With auto-confirm, the model chains through the entire lifecycle in one turn. This
is correct behavior — the confirmation gates fire and resolve instantly. Eval
assertions need to accommodate the higher tool counts. The evals validate workflow
correctness (right tools called, right state transitions), not pacing (which is
controlled by UI in production).

## Efficiency

- The early phases (end_turn bool, dynamic tools) went smoothly — clean implementations,
  tests passed first try
- Most time was spent iterating on review gate design (4 iterations) — each one
  required manual testing to reveal the failure mode
- The streaming buffer flush was a quick fix once diagnosed but would have been
  hard to catch without manual testing in the web UI
- Evals were useful for catching regressions but insufficient for UX issues —
  manual testing was essential for the review gate flow

## Process improvements

- **Manual test earlier in the cycle.** We ran evals first and they passed, but manual
  testing revealed fundamental UX issues (stalls, missing presentations, truncation).
  For UX-sensitive features, manual test after each design change.

- **Design the confirmation flow before implementing.** We iterated through 4 review
  gate designs. If we'd sketched the EndTurnConfirm design (with presentation call +
  callbacks + streaming flush) upfront, we could have saved 2-3 iterations.

- **Evals need a "pacing mode."** Auto-confirm is great for testing correctness but
  hides pacing issues. A mode that adds realistic delays to confirmations would catch
  stall bugs that auto-confirm masks.

## Conversation turns

~35 back-and-forth exchanges over the session, roughly:
- 5 turns: research and diagnosis
- 5 turns: brainstorming the spec
- 3 turns: planning
- 22+ turns: implementation iterations with manual testing feedback

## Other highlights

- Gemini Flash eval results went from 4/8 (755K tokens, 62 max tool calls) to
  8/8 (331K tokens, reasonable tool counts) — a qualitative transformation
- The `EndTurnConfirm` pattern maps cleanly to LangGraph's interrupt/resume model,
  validating the architectural direction for #255
- Les's instinct to bring back mechanical confirmation was correct — conversational
  review was a dead end regardless of how we phrased the prompts
- The distinction between "spec = WHAT/WHY" and "plan = HOW" in SKILL.md helped
  the model produce better artifacts once it stopped conflating the two
