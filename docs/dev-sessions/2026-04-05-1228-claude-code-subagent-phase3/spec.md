# Claude Code Subagent Phase 3 — Spec

## Goal

Enrich progress reporting during `claude_code_send` so the parent agent and Mattermost display have better situational awareness. Currently progress events are coarse ("Using Edit..."). This adds tool call counts, running cost, budget warnings, and error snippets.

Covers issue: #212 (richer progress reporting). Part of umbrella #213.

Note: #210 (structured error classification) was closed as not planned — the parent LLM can classify errors from raw messages better than any rule-based system.

## 1. Enriched progress events (#212)

### Tool call count

Track a per-send tool call counter (resets each `claude_code_send`). Include it in progress messages:

- On tool use: `"Tool call 5: Using Edit..."`
- On tool error: `"Edit failed — ModuleNotFoundError: No module named 'foo'"` (no count prefix — the tool name identifies which tool failed)

Counter increments for each `ToolUseBlock` seen in `AssistantMessage`.

### Tool result snippets

When a `UserMessage` contains `ToolResultBlock` entries with `is_error=True`, publish a progress event with the error snippet (first 100 chars). This gives the parent agent signal that things are going wrong without waiting for the send to complete.

### Running cost

When a `ResultMessage` arrives with cost data, publish a progress event:

- `"Session cost: $0.45 of $2.00 budget"`

### Budget warnings

When cost crosses threshold percentages of the session budget, publish a warning:

- 50%: `"Budget warning: 50% used ($1.00 of $2.00)"`
- 75%: `"Budget warning: 75% used ($1.50 of $2.00)"`
- 90%: `"Budget warning: 90% used ($1.80 of $2.00)"`

Track which thresholds have been crossed per-send to avoid duplicate warnings (e.g., if cost jumps from 40% to 80%, fire both 50% and 75% warnings).

### Event type

All progress events use the existing `tool_status` event type with richer message text. No new event types needed.

## 2. Edge cases and constraints

### Tool name lookup for error snippets

`ToolResultBlock` carries `tool_use_id` but not the tool name. To show `"Tool call 5: Edit failed — ..."`, we need a mapping from tool_use_id → tool_name built during the loop as we see `ToolUseBlock` entries. If the id isn't found (shouldn't happen, but defensive), fall back to `"unknown tool"`.

### Multiple ResultMessages

The SDK may yield multiple `ResultMessage`s with updated cost. The budget threshold tracking (`warnings_fired` set) handles this correctly — thresholds already fired won't fire again.

### Cost is session-total, not per-send

`ResultMessage.total_cost_usd` is the cumulative session cost. Progress messages should say `"Session cost: $0.45 of $2.00 budget"` (not just "Cost:") to avoid confusion with per-send cost.

## 3. Implementation approach

All changes are in the SDK message streaming loop inside `tool_claude_code_send` in `tools.py`. The loop already iterates over `AssistantMessage`, `UserMessage`, and `ResultMessage` — we just enrich what's published.

State needed during the loop:
- `tool_call_count: int = 0` — increments per ToolUseBlock
- `tool_id_to_name: dict[str, str]` — maps ToolUseBlock.id → name for error snippet lookup
- `warnings_fired: set` — tracks which budget thresholds (0.5, 0.75, 0.9) have been published

## 4. Files changed

- `src/decafclaw/skills/claude_code/tools.py` — enrich the streaming loop in `tool_claude_code_send`
- `src/decafclaw/skills/claude_code/SKILL.md` — document richer progress events
- Tests for budget warning threshold logic

## 5. Out of scope

- Phase detection (reading/writing/testing/stuck) — too heuristic, parent LLM can infer
- Structured error classification (#210) — closed as not planned
- New event types — reuse tool_status
- File staging (#211) — next phase
