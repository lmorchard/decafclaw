# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/145
**Frozen at:** <pending>
**Check files — read-only from Phase 1 onward:**
- `tests/test_steering.py`

## C1
CRITERION: WHEN a user sends a steering message while the agent is executing tool calls, THE SYSTEM SHALL interrupt the agent loop after the current tool call completes and ingest the steering message.
CHECK: `pytest tests/test_steering.py::test_steering_interrupts_after_tool_call` passes.
AT FREEZE: <pending>

## C2
CRITERION: WHEN a user sends a follow-up message while the agent is busy, THE SYSTEM SHALL queue the message and deliver it as a new turn after the current agent turn finishes.
CHECK: `pytest tests/test_steering.py::test_follow_up_message_queued` passes.
AT FREEZE: <pending>

## Guards
- G1: `pytest tests/test_agent_turn.py` passes and existing agent turn flow is preserved.
  Passed at freeze.

## Adjudication
- C1: <pending>
- C2: <pending>
- G1: <pending>

## Amendments
