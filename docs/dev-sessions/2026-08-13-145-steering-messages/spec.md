## Summary

Allow users to send messages while the agent is mid-turn, with two modes: **steering** (interrupt after current tool call) and **follow-up** (queue for after the agent finishes).

## Motivation

Inspired by Pi/OpenClaw's steering and follow-up queues. Currently, DecafClaw's `busy` flag blocks new input during a turn. Users can't say "actually stop, try a different approach" or queue up additional context while the agent is working.

## Design Notes

- Steering messages interrupt the agent loop after the current tool call completes (not mid-tool)
- Follow-up messages queue and are delivered after the current turn ends
- Could use the existing EventBus to signal steering interrupts
- Web UI and Mattermost would both need to support sending messages while busy
- Need to consider how this interacts with the reflection judge (skip reflection on steered turns?)

## Prior Art

- Pi agent core implements steering and follow-up queues with "one-at-a-time" or "all" delivery modes
- OpenClaw inherits this from the Pi SDK

## Verifiable acceptance criteria

- CRITERION: WHEN a user sends a steering message while the agent is executing tool calls, THE SYSTEM SHALL interrupt the agent loop after the current tool call completes and ingest the steering message.
  - CHECK: `pytest tests/test_steering.py::test_steering_interrupts_after_tool_call` passes.

- CRITERION: WHEN a user sends a follow-up message while the agent is busy, THE SYSTEM SHALL queue the message and deliver it as a new turn after the current agent turn finishes.
  - CHECK: `pytest tests/test_steering.py::test_follow_up_message_queued` passes.

## Regression guards

- GUARD: `pytest tests/test_agent.py` passes and existing agent turn flow is preserved.

## Tier: auto-ok

**Reason:** Approved by human review.

