## Architectural Patterns
- **Synchronous Context Mutation:** Use `Context.add_interceptor(TurnLifecycle.BEFORE_LLM_CALL, hook)` for deterministic context mutation before LLM calls, avoiding race conditions inherent in async EventBus mutation (introduced in #812).
- **Mid-turn Steering & Follow-up Queues:** Use `steer_event` to interrupt the agent loop safely after tool execution completes, and queue follow-up messages for subsequent turns (introduced in #145).
