**Concept from opencode:**
While `opencode` has event emitters, its plugin architecture relies on an explicit lifecycle middleware chain (`Hooks` interface) that allows plugins to synchronously intercept and mutate the LLM request before execution.

**How `decafclaw` could implement this:**
`decafclaw` heavily relies on an async EventBus (`events.py`), which is great for "fire-and-forget" but prone to race conditions when used for mutation.

**Proposed Implementation:**
- Formalize a middleware/hook chain (e.g., `Context.add_interceptor(TurnLifecycle.BEFORE_LLM_CALL, my_hook)`).
- Allow external skills to safely and synchronously alter prompt context, tool execution parameters, or LLM routing right before a request goes out.

## Verifiable acceptance criteria

- CRITERION: WHEN an interceptor is registered via `Context.add_interceptor(TurnLifecycle.BEFORE_LLM_CALL, hook)`, the system SHALL synchronously invoke the hook with `(ctx, messages, tools)` before sending the LLM request, allowing it to modify the arguments in-place.
  CHECK: `pytest tests/test_interceptor_hooks.py::test_before_llm_call_hook_mutates_messages` (asserts that a test hook appending a system message successfully alters the outgoing LLM call) passes.
  VERIFIED DISCRIMINATING: No such test or `add_interceptor` method exists today.

- CRITERION: WHEN multiple interceptors are registered for the same phase, the system SHALL execute them synchronously in the order they were added.
  CHECK: `pytest tests/test_interceptor_hooks.py::test_interceptors_execute_in_order` (asserts that hooks appending to a shared list execute in registration order) passes.
  VERIFIED DISCRIMINATING: No such test or mechanism exists today.

## Regression guards

- GUARD: `make test` — The existing EventBus behavior and `run_agent_turn` loop remain functionally equivalent when no hooks are registered. Passes today.

## Tier: auto-ok

The criteria reduce the hook mechanism to concrete testable behavior. Assuming we start with just the `BEFORE_LLM_CALL` phase, no subjective human judgment is required to verify the implementation.

## Design decisions

- **Decision:** Only support the `BEFORE_LLM_CALL` phase in this initial PR.
  - **Why:** To keep the initial implementation scoped and focused as requested in the issue, and confirmed by user.
  - **Rejected:** Supporting multiple lifecycle phases at once.

