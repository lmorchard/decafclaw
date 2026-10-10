# Tool-Owned Prompt Guidance Spec (#928 & #998)

**Goal:** Allow active core tools and trusted activated skill tools to contribute usage guidelines dynamically into the system prompt at runtime, eliminating prompt bloat and instruction drift from deferred or unavailable tools.

**Source:** Issues #928 and #998.

## Current state

- Static tool guidance lives in `src/decafclaw/prompts/AGENT.md:252-272` (the "Workspace — Your Filesystem" selection guide for 11 `workspace_*` tools) and repeats in tool descriptions (`src/decafclaw/tools/workspace_tools.py:813, 959, 1005`).
- Most `workspace_*` tools are `priority: normal` or `low` and are deferred when tool count/token budget limits are reached (`classify_tools` in `src/decafclaw/tools/tool_registry.py:83-205`). As a result, `AGENT.md` routinely instructs the model to call deferred tools that are not callable without a `tool_search` fetch first.
- `ContextComposer._compose_impl` (`src/decafclaw/context_composer.py:662-670`) injects system prompt, vault guide, deferred tools catalog (`<deferred_tools>`), and suggested skills into the messages list.
- `TurnRunner._run_iteration_impl` (`src/decafclaw/agent.py:776-790`) dynamically updates or removes the deferred tools system message (`self.deferred_msg`) across iterations as tools are activated or fetched.
- Skills define tool definitions that are registered during activation in `ctx.tools.extra_definitions` (`src/decafclaw/tools/skill_tools.py:804`). Skill trust is governed by `skills.grants_capability(info)` (`src/decafclaw/skills/__init__.py:421`), which trusts `bundled`, `admin`, and `extra` tiers while rejecting agent-writable `workspace` tier.
- `tests/test_discovered_skills_consumers.py` scans and enforces recorded trust decisions for all consumers reading `discovered_skills`.

## Desired end state

1. **Schema support (`prompt_guidelines`):**
   - Tool definition dicts can optionally define `prompt_guidelines: list[str]` alongside `priority`.
   - Providers ignore or strip top-level extra keys without error.
2. **Guidance Rendering (`build_tool_guidance_text` in `tool_registry.py`):**
   - Takes active tool definitions and trust context.
   - Collects `prompt_guidelines` from active tools that are either:
     a) Core tools (name present in `TOOL_DEFINITIONS`).
     b) Trusted skill tools (skill passes `skills.grants_capability(info)`).
   - Ignores guidelines from `workspace`-tier skill tools, MCP tools (`mcp__*`), and deferred tools.
   - Deduplicates guideline strings preserving stable order (core tools first in `TOOL_DEFINITIONS` order, then trusted skill tools).
   - Wraps output in `<tool_guidance>\n...\n</tool_guidance>`. Returns `None` if no active tool defines guidelines.
3. **Turn & Iteration Injection:**
   - In `ContextComposer._compose_impl`, injects `tool_guidance_text` as a dedicated system message (`role: "system"`), positioned immediately after `<deferred_tools>` (or after vault guide / system prompt if deferred tools are absent) before user history.
   - In `TurnRunner` (`agent.py`), tracks `self.guidance_msg` alongside `self.deferred_msg`. On each iteration in `_run_iteration_impl`, re-renders the guidance block from `all_tools` and replaces, inserts, or removes `self.guidance_msg`.
   - Operates independently of deferred tools: appears when tools fit entirely in budget (`deferred_text is None`), and updates dynamically if a tool is fetched via `tool_search` or dynamic skill provider refresh.
4. **Token Diagnostics:**
   - `ContextComposer._compose_tools` accounts for guidance text tokens in `tokens_estimated` for the `"tools"` source entry.
5. **Guidance Migration:**
   - Migrate per-tool lines from `AGENT.md` (lines 258-272) into `prompt_guidelines` in `src/decafclaw/tools/workspace_tools.py`.
   - `AGENT.md` retains only general rules: prefer surgical edits over rewrites, never edit files using shell `sed`/`python`/heredocs, diff verification.
6. **Trust Consumer Tracking:**
   - Register the new `discovered_skills` read in `tests/test_discovered_skills_consumers.py` with its `grants_capability` rationale.
7. **Documentation:**
   - Update `docs/context-composer.md` (add `<tool_guidance>` to Section Delimiters table) and `docs/tools.md` explaining the boundary between tool descriptions, `prompt_guidelines`, `AGENT.md`, and skill bodies.

## Design decisions

- **Decision:** Dedicated system message for `<tool_guidance>` tracked via `self.guidance_msg` in `TurnRunner`, rather than merging with `<deferred_tools>`.
  - **Why:** Separates active execution rules (`<tool_guidance>`) from the discovery catalog (`<deferred_tools>`). Allows guidance to appear when all tools fit in budget and no tools are deferred.
  - **Rejected:** Merging into the deferred tools message (fails to surface guidance when `deferred_text is None`).
  - **Rejected:** Appending to individual tool descriptions (wastes tokens when guidance is shared across related tools like `search`/`glob` and creates verbosity in schema definitions).

- **Decision:** Trust gate uses `skills.grants_capability(info)` (`bundled`, `admin`, `extra` tiers), excluding `workspace` tier.
  - **Why:** Complies with repository security invariant: `workspace/skills/` is agent-writable and must never gain trusted prompt injection capabilities.
  - **Rejected:** Core-only gate (would require a second refactor for #998).
  - **Rejected:** Excluding `extra` tier (inconsistent with `grants_capability` and would break contrib skills configured by the user).

- **Decision:** Deduplication across all guideline strings preserving encounter order.
  - **Why:** Allows related tools (e.g., `workspace_search` and `workspace_glob`, or `workspace_move` and `workspace_delete`) to declare the same guideline string without duplicate lines in `<tool_guidance>`.

## Patterns to follow

- Delimiter wrapping pattern: `docs/context-composer.md:19-35` and `prompts/__init__.py:wrap_xml`.
- Deferred message iteration lifecycle: `agent.py:752-755` and `agent.py:779-790`.
- Tool definition framework properties: `priority` in `tools/tool_registry.py:16-28` and `timeout` in `tools/shell_tools.py:1131`.
- Capability gating: `skills/__init__.py:421` (`grants_capability`) and `tool_definitions.py:107`.
- Consumer registration: `tests/test_discovered_skills_consumers.py:57-120`.

## What we're NOT doing

- Not changing tool priority definitions or default budget limits (already resolved in #1003 / #1041).
- Not allowing MCP tools to contribute prompt guidance.
- Not implementing tool retirement / deactivation (#30). When deactivation is added in the future, guidance will naturally drop because `build_tool_guidance_text` evaluates only currently active tools on each iteration.
- Not adding conversation modes or UI selectors (scoped to #959).
- Not modifying global behavior rules in `AGENT.md` (e.g. reflexivity guards, edit verification, shell prohibitions).

## Open questions

None. (Trust tier for `extra` confirmed as permitted via `skills.grants_capability`; dedicated system message lifecycle confirmed).
