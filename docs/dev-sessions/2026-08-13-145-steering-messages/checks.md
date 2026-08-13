# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/145
**Frozen at:** 337f273d
**Check files — read-only from Phase 1 onward:**
- `tests/test_steering.py`

## C1
CRITERION: WHEN a user sends a steering message while the agent is executing tool calls, THE SYSTEM SHALL interrupt the agent loop after the current tool call completes and ingest the steering message.
CHECK: `pytest tests/test_steering.py::test_steering_interrupts_after_tool_call` passes.
AT FREEZE: fails - feature not implementedd

## C2
CRITERION: WHEN a user sends a follow-up message while the agent is busy, THE SYSTEM SHALL queue the message and deliver it as a new turn after the current agent turn finishes.
CHECK: `pytest tests/test_steering.py::test_follow_up_message_queued` passes.
AT FREEZE: fails - feature not implementedd

## Guards
- G1: `pytest tests/test_agent_turn.py` passes and existing agent turn flow is preserved.
  Passed at freeze.

## Adjudication
- C1: accepted - The check accurately validates steering.d
- C2: accepted - The check accurately validates follow-up queuing.d
- G1: accepted - No changes required for regression guard.d

## Amendments
