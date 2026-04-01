# Tool Search / Deferred Loading — Plan

## Status: Ready

## Overview

Five phases. Each ends with lint + test passing and a commit. Phase 1 builds the tool registry and token estimation. Phase 2 implements the tool_search tool. Phase 3 rewrites `_build_tool_list` for deferred mode. Phase 4 integrates with the agent loop (system prompt injection, auto-fetch). Phase 5 handles child agents, skill activation, and docs.

---

## Phase 1: Tool registry with token estimation and always-loaded config

**Goal**: Build the data structures that know about all tools, can estimate their token cost, and distinguish always-loaded from deferrable. No behavior change yet.

**Files**: `config.py`, new `tools/tool_registry.py`

### Prompt

Read these files for context:
- `src/decafclaw/config.py` — Config dataclass
- `src/decafclaw/tools/__init__.py` — current TOOLS/TOOL_DEFINITIONS registry, execute_tool
- `src/decafclaw/agent.py` — `_build_tool_list` (lines 91-137)

Create a new module `src/decafclaw/tools/tool_registry.py` that provides a centralized view of all available tools and their deferral status.

1. **Config additions** (`config.py`):
   - `tool_context_budget_pct: float = 0.10` — proportion of compaction_max_tokens for tool definitions
   - `always_loaded_tools: str = ""` — comma-separated tool names to add to defaults
   - Add both to `load_config()` with env vars `TOOL_CONTEXT_BUDGET_PCT` and `ALWAYS_LOADED_TOOLS`
   - Add a property `tool_context_budget` that returns `int(compaction_max_tokens * tool_context_budget_pct)`

2. **`tool_registry.py`**:

   ```python
   # Hardcoded default always-loaded tools
   DEFAULT_ALWAYS_LOADED = {
       "think", "memory_save", "memory_search", "memory_recent",
       "activate_skill", "shell", "workspace_read", "workspace_write",
       "web_fetch", "current_time", "delegate_task",
   }
   ```

   Functions:
   - `estimate_tool_tokens(tool_defs: list[dict]) -> int` — sum of `len(json.dumps(td)) // 4` for each definition
   - `get_always_loaded_names(config) -> set[str]` — returns `DEFAULT_ALWAYS_LOADED | set(config.always_loaded_tools.split(","))` (filtering empty strings)
   - `classify_tools(all_tool_defs: list[dict], config, fetched_names: set[str] = set()) -> tuple[list[dict], list[dict]]` — given all available definitions, returns `(active_defs, deferred_defs)`:
     - Compute total tokens. If under budget, return `(all_tool_defs, [])` — no deferral.
     - Otherwise: active = always-loaded + fetched; deferred = everything else.
   - `build_deferred_list_text(deferred_defs: list[dict], all_tool_defs: list[dict], config) -> str` — builds the grouped name+description text block for the system prompt. Groups by source:
     - Core: tools from TOOL_DEFINITIONS
     - Skill: tools from extra_tool_definitions (group by skill name if available)
     - MCP: tools starting with `mcp__` (group by server name)
   - `get_description(tool_def: dict) -> str` — extract first sentence or first 80 chars of description

3. **Tests**: Unit tests for `estimate_tool_tokens`, `get_always_loaded_names`, `classify_tools` (under budget → no deferral, over budget → correct split), `build_deferred_list_text`.

Lint and test after.

---

## Phase 2: Implement tool_search tool

**Goal**: The `tool_search` tool itself — keyword search and exact selection. Not wired into the agent loop yet, but fully functional and tested.

**Files**: new `tools/search_tools.py`

### Prompt

Read the spec section on `tool_search` and `src/decafclaw/tools/tool_registry.py` from Phase 1.

Create `src/decafclaw/tools/search_tools.py`:

1. **No module-level state.** The deferred pool is per-context to avoid race conditions between concurrent conversations with different activated skills. The pool is passed to tool_search via `ctx`.

   Store on ctx: `ctx.deferred_tool_pool: list[dict] = []` — set by `_build_tool_list` each iteration.

2. **`tool_search(ctx, query: str, max_results: int = 10) -> str`**:
   - Reads the deferred pool from `ctx.deferred_tool_pool` (set by the agent loop).
   - If `query` starts with `"select:"`, parse comma-separated names, find exact matches in the pool, return their full schemas.
   - Otherwise, keyword search: case-insensitive substring match on tool name and first-sentence description. Return up to `max_results` matches with full schemas.
   - Add matched tool names to `ctx.skill_data["fetched_tools"]`. **Important**: store as a `list` (not `set`) since `skill_data` is JSON-serialized via the archive sidecar. Use a helper to add without duplicates.
   - Return the schemas formatted as text (JSON) so the model can see them. Include a note like "N tools loaded. These tools are now available to call."
   - If no matches: return "No tools found matching '{query}'."

3. **`SEARCH_TOOLS` and `SEARCH_TOOL_DEFINITIONS`** — the tool dict and definition list, same pattern as other tool modules.

4. **Do NOT register in `tools/__init__.py` yet** — it will be conditionally added by the agent loop in Phase 3.

5. **`fetched_tools` serialization helper**: provide `get_fetched_tools(ctx) -> set[str]` and `add_fetched_tools(ctx, names: set[str])` helpers that handle the list↔set conversion for `skill_data["fetched_tools"]`. All code reads/writes through these helpers.

6. **Tests**:
   - Keyword search matches name
   - Keyword search matches description
   - Keyword search respects max_results
   - Exact selection returns specified tools
   - Exact selection with unknown name returns partial results + message
   - Fetched tools added to ctx.skill_data
   - No matches returns helpful message

Lint and test after.

---

## Phase 3: Rewrite _build_tool_list for deferred mode

**Goal**: `_build_tool_list` now decides between normal mode and deferred mode based on token budget. When deferred, it returns only active tools + tool_search, and produces the deferred list text for system prompt injection.

**Files**: `agent.py`

### Prompt

Read these files:
- `src/decafclaw/agent.py` — current `_build_tool_list` and `run_agent_turn`
- `src/decafclaw/tools/tool_registry.py` from Phase 1
- `src/decafclaw/tools/search_tools.py` from Phase 2

Rewrite `_build_tool_list`:

1. Rename current `_build_tool_list` to `_collect_all_tool_defs(ctx) -> list[dict]` — it gathers all definitions (core + skill + MCP + extra). **Do NOT apply `allowed_tools` filter here** — we need the full set for classification. The filter was previously at the end; it moves to after classification.

2. New `_build_tool_list(ctx) -> tuple[list[dict], str | None]`:
   - Call `_collect_all_tool_defs(ctx)` to get ALL definitions (unfiltered)
   - Get fetched tools via `get_fetched_tools(ctx)` helper
   - Call `classify_tools(all_defs, ctx.config, fetched_names)`
   - Apply `allowed_tools` filter to the **active** set only (if `ctx.allowed_tools` is set). The deferred set stays unfiltered — `tool_search` filters at query time.
   - If no deferral (deferred list empty): return `(active_defs, None)`
   - If deferred mode:
     - Set `ctx.deferred_tool_pool = deferred_defs` (per-context, not module-level)
     - Add `SEARCH_TOOL_DEFINITIONS` to the active list
     - Build the deferred list text via `build_deferred_list_text`
     - Return `(active_defs, deferred_list_text)`

3. Update the call site in `run_agent_turn`:
   - `all_tools, deferred_text = _build_tool_list(ctx)`
   - **System prompt injection without duplication**: manage a `deferred_msg` slot. Before the loop, set `deferred_msg = None`. Inside the loop, if `deferred_text` is not None:
     - If `deferred_msg` is already in `messages`, replace its content
     - Otherwise, insert it as a system message right after the first system message and track the reference
   - This ensures the deferred list is updated each iteration (as tools get fetched and leave the deferred set) without duplicating.

4. Update existing tests that call `_build_tool_list` directly — they now get a tuple back. Unpack or update assertions.

**Note on search + use latency**: tool_search returns schemas as text in a tool result. The model sees them but can't call the tools until the next iteration when `_build_tool_list` includes them in the active set. This means search + use takes 2 LLM rounds. This is expected and matches Claude Code's client-side behavior (as opposed to their API-level server-side search which is single-round).

4. **Tests**:
   - Under budget: returns all tools, no deferred text
   - Over budget: returns only always-loaded + fetched + tool_search, deferred text is populated
   - Fetched tools appear in active set

Lint and test after.

---

## Phase 4: Auto-fetch and execute_tool integration

**Goal**: When the model calls a deferred tool without searching first, auto-fetch it. Wire everything together end-to-end.

**Files**: `tools/__init__.py` (execute_tool), `agent.py`

### Prompt

Read:
- `src/decafclaw/tools/__init__.py` — `execute_tool` function
- `src/decafclaw/tools/search_tools.py` from Phase 2
- `src/decafclaw/agent.py` — the updated `_build_tool_list` and `run_agent_turn`

1. **Auto-fetch in `execute_tool`**: After the existing `fn is None` check (line 98), before returning the "unknown tool" error, check if the tool name exists in `ctx.deferred_tool_pool`:
   ```python
   deferred_pool = getattr(ctx, "deferred_tool_pool", [])
   deferred_names = {td.get("function", {}).get("name") for td in deferred_pool}
   if name in deferred_names:
       log.debug(f"Auto-fetching deferred tool: {name}")
       add_fetched_tools(ctx, {name})
       # The callable is already in TOOLS or extra_tools — fn lookup
       # failed because of allowed_tools filtering, not because it
       # doesn't exist. Re-lookup without the filter.
       fn = extra_tools.get(name) or TOOLS.get(name)
       # Execute as normal...
   ```
   The key insight: the callable is registered (in TOOLS or extra_tools), but `allowed_tools` filtering may have blocked it. Auto-fetch adds it to the fetched set so the next `_build_tool_list` includes it. For this turn, we bypass the filter and execute directly.

2. **Register search_tools** in `tools/__init__.py`: import `SEARCH_TOOLS` and `SEARCH_TOOL_DEFINITIONS` but do NOT add them to the global TOOLS/TOOL_DEFINITIONS. They're conditionally added by `_build_tool_list`. But `execute_tool` needs to find `tool_search` — add a check:
   ```python
   from .search_tools import SEARCH_TOOLS
   fn = extra_tools.get(name) or TOOLS.get(name) or SEARCH_TOOLS.get(name)
   ```

3. **Tests**:
   - Model calls a deferred tool → auto-fetched and executed
   - Auto-fetched tool added to fetched set
   - tool_search callable via execute_tool when deferred mode active

Lint and test after.

---

## Phase 5: Child agents, skill activation, and docs

**Goal**: Children inherit fetched tools without search. Skill activation defers tool schemas in deferred mode. Update docs.

**Files**: `tools/delegate.py`, `tools/skill_tools.py`, `CLAUDE.md`, `docs/`

### Prompt

Read:
- `src/decafclaw/tools/delegate.py` — `_run_child_turn`
- `src/decafclaw/tools/skill_tools.py` — `tool_activate_skill`, `restore_skills`
- `src/decafclaw/agent.py` — `_build_tool_list`

### Part 5a: Child agents

1. In `_run_child_turn`, children already inherit `skill_data` from the parent. Since fetched tools are stored in `skill_data["fetched_tools"]`, children automatically get the parent's fetched set.

2. Children should NOT get the `tool_search` tool. They already don't get `activate_skill` or `refresh_skills`. Add `"tool_search"` to the `excluded` set in `_run_child_turn`.

3. Verify: `_build_tool_list` for children (who have `allowed_tools` set) should include the parent's fetched tools in the active set. Since the child inherits `skill_data` with `fetched_tools`, and `classify_tools` reads `fetched_names`, this should work. Verify with a test.

### Part 5b: Skill activation in deferred mode

1. In `tool_activate_skill` (skill_tools.py), when a skill is activated:
   - The SKILL.md body is returned to the model (unchanged)
   - Native tools are registered in `ctx.extra_tools` and `ctx.extra_tool_definitions` (unchanged)
   - **New**: the next call to `_build_tool_list` will see these extra tool definitions and classify them. In deferred mode, they'll go into the deferred pool. In normal mode, they load immediately. No code change needed in skill_tools.py — the existing flow works because `_build_tool_list` already reads `extra_tool_definitions`.

2. Verify with a test: activate a skill in deferred mode → its tools appear in the deferred list, not the active list. Searching for them fetches them.

### Part 5c: Docs

1. Update `CLAUDE.md`:
   - Add convention: "Tool definitions are deferred behind tool_search when they would exceed `tool_context_budget_pct` (default 10%) of `compaction_max_tokens`. Always-loaded tools are configured via `ALWAYS_LOADED_TOOLS`."
   - Add `tools/tool_registry.py` and `tools/search_tools.py` to key files
   - Note that skills' tool schemas load on demand in deferred mode

2. Update `docs/`:
   - Create or update a doc about tool management
   - Document `TOOL_CONTEXT_BUDGET_PCT`, `ALWAYS_LOADED_TOOLS` env vars

3. Update `README.md` if there's a config table

4. Close issue #35

Lint, test, commit.

---

## Dependency Graph

```
Phase 1 (registry + token estimation + config)
  ↓
Phase 2 (tool_search tool implementation)
  ↓
Phase 3 (rewrite _build_tool_list for deferred mode)
  ↓
Phase 4 (auto-fetch + execute_tool integration)
  ↓
Phase 5 (children + skill activation + docs)
```

Each phase builds directly on the previous. No orphaned code.

## Testing Strategy

- **Phase 1**: Unit tests for token estimation, always-loaded set, tool classification, deferred list formatting
- **Phase 2**: Unit tests for keyword search, exact selection, fetch persistence, edge cases
- **Phase 3**: Integration tests for _build_tool_list: under/over budget, fetched tools in active set, deferred text generation, system prompt injection
- **Phase 4**: Auto-fetch on deferred tool call, execute_tool routing to search_tools
- **Phase 5**: Child agent inherits fetched set, skill activation in deferred mode, manual QA in Mattermost/web

## Risk Notes

- **Gemini tool list stability**: Gemini can produce empty responses when the tool list changes between iterations. Currently `_build_tool_list` pre-loads skill definitions to keep the list stable. With deferred mode, the tool list grows as tools are fetched. This is similar to the existing skill activation flow (tools appear mid-conversation) which already works. But monitor for Gemini regressions.
- **Token estimation accuracy**: JSON length / 4 is a rough approximation. May need tuning if the threshold triggers too aggressively or not enough. The configurable `tool_context_budget_pct` gives a knob to adjust.
- **Search + use is 2 LLM rounds**: The model calls tool_search, gets schemas as text, then on the next iteration the tools are callable. This is an inherent cost of client-side search (vs Anthropic's server-side tool_search_tool which is single-round). Acceptable for now.
- **Pre-loaded skill definitions**: The current `_build_tool_list` pre-loads ALL skill tool definitions to keep the tool list stable for Gemini. In deferred mode, we should NOT pre-load deferred skills (defeats the purpose). The `_collect_all_tool_defs` function should still gather them (for classification), but only the active set goes to the LLM. The tool list will grow as tools are fetched — same pattern as existing skill activation.
