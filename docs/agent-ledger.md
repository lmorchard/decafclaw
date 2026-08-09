## Architectural Patterns
- **Synchronous Context Mutation:** Use `Context.add_interceptor(TurnLifecycle.BEFORE_LLM_CALL, hook)` for deterministic context mutation before LLM calls, avoiding race conditions inherent in async EventBus mutation (introduced in #812).
