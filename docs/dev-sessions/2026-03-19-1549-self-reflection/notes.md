# Self-Reflection / Retry — Notes

## Session Recap

Implemented the Reflexion pattern for self-evaluation of agent responses before delivery. A separate judge LLM call evaluates whether the response adequately addresses the user's request. On failure, critique is injected and the agent retries.

### What we built
- **reflection.py** — judge module: prompt assembly, LLM call, JSON verdict parsing, fail-open error handling
- **ReflectionConfig** — separate model config with `resolved()` fallback to LLM (like compaction/embedding)
- **Agent loop integration** — reflection check after final response, critique injection as user-role message, `continue` for retry within existing iteration loop
- **UI visibility** — `reflection_result` event handled in Mattermost and web UI, three modes (hidden/visible/debug)
- **22 new tests** — 16 unit tests for reflection.py, 6 integration tests for agent loop

### Key design decisions
1. **Reflexion pattern** — binary pass/fail with verbal critique, not multi-dimensional scoring
2. **Chain-of-thought before verdict** (G-Eval) — judge reasons first, then outputs JSON
3. **Fail-open** — judge errors, parse failures, and network issues all treated as pass
4. **User-role critique messages** — higher weight than system messages for most models
5. **Within iteration budget** — reflection retries consume from `max_tool_iterations`, not additional
6. **Reflection disabled in test fixtures** — tests opt in explicitly to avoid mock complexity

### Stats
- 590 tests passing, lint + pyright + tsc clean
- ~180 lines in reflection.py
- ~30 lines added to agent.py
- ~20 lines each for Mattermost/web UI event handling

### Session observations
- The test fixture issue was the trickiest part — reflection is on by default, so the existing mocked tests were hitting the real LLM as the judge. Disabling in conftest was the right call.
- Patching the deferred import (`from .reflection import evaluate_response` inside the function) required patching at the source module, not `decafclaw.agent.evaluate_response`.
- The pyright warning about `client.config` on MattermostClient caught a real issue — config isn't stored on the client, so we had to pass `reflection_visibility` as a parameter.
