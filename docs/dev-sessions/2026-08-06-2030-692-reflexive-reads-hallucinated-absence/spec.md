# Specification — 692-reflexive-reads-hallucinated-absence

## Goal
Address issue #692 where the agent reflexively fires read-only tools (`notes_read`, `vault_search`, `conversation_search`) before answering, and subsequently hallucinates absence ("I don't have that information") if those queries return empty, instead of falling back to the visible context window or general knowledge.

## Requirements
1. Prevent reflexive read-only tool calls (`notes_read`, `vault_search`, `conversation_search`) on simple general knowledge questions or queries that are answerable from the visible conversation history.
2. If a search/read tool is legitimately called and returns empty, ensure the agent falls back to visible context and general knowledge instead of confidently asserting that the information is missing.

## Design Decisions
- **`AGENT.md` Prompts:** Enhance the `AGENT.md` system prompt with highly targeted directives under `### Tool usage` and `## Vault` to explicitly guide the agent's behavior.
- **Verification Evals:**
  - Update `over_ceremony.yaml` to ensure the plain "What's the capital of France?" runs with exactly `max_tool_calls: 0` and `expect_no_tool` set for all read tools.
  - Create `empty_search_fallback.yaml` to verify the fallback-to-context behavior when a search returns empty.

## What we are NOT doing
- We are not changing the python code of the tool runners themselves; this is a pure prompt-engineering and behavior stabilization fix.
