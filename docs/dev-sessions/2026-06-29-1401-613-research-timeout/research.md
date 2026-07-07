# Research — Per-tool timeout mechanism & `/research`'s plan-stage

Source: `Explore` subagent against the worktree, 2026-06-29.

## 1. Per-tool timeout mechanism

**Definition:** Tool dicts in `TOOL_DEFINITIONS` (and skill-bundled equivalents) carry an optional `"timeout"` key. Cited examples:

- `src/decafclaw/tools/delegate.py:546` — `"timeout": None,` (opts out; owns child-agent timeout)
- `src/decafclaw/tools/conversation_tools.py:107` — `"timeout": None,` (LLM summarization has its own model timeout)
- `src/decafclaw/skills/claude_code/tools.py:1016` — `"timeout": None,` (subprocess session bounded by its own idle/budget controls)

**Resolution at execute time** (`src/decafclaw/tools/__init__.py:113-149`, `_resolve_tool_timeout`):
- Walks `ctx.tools.extra_definitions` (skill-provided) first, then `TOOL_DEFINITIONS`, then `SEARCH_TOOL_DEFINITIONS`.
- Returns the first explicit `timeout` key encountered (including `None`).
- Falls back to `ctx.config.agent.tool_timeout_sec` (default 180s per `src/decafclaw/config_types.py:200`).
- Values `<= 0` normalize to `None` (disabled).

**Wrapping** (`src/decafclaw/tools/__init__.py:53-107`, `_run_with_cancel`):
- Creates `asyncio.sleep(timeout_sec)` timer task if `timeout_sec is not None and > 0`.
- Races the tool task against the timer (and the cancel event).
- On timer firing: cancels the tool, returns `ToolResult(text="[error: tool {name} timed out after {N}s]")`.

## 2. Skill-bundled tool registration carries `timeout` through

`activate_skill_internal` (`src/decafclaw/tools/skill_tools.py:304-358`):
- Line 323: `tools, tool_defs, module = _load_native_tools(skill_info)`.
- Line 327: `ctx.tools.extra_definitions.extend(tool_defs)` — extends the list with COMPLETE dicts; no key stripping.

`_load_native_tools` (`src/decafclaw/tools/skill_tools.py:165-181`):
- Line 180: `tool_defs = getattr(module, "TOOL_DEFINITIONS", [])` — reads the raw list as-is.

**So:** adding `"timeout": N` to a skill-bundled tool's definition is functionally identical to adding it to a built-in `TOOL_DEFINITIONS` entry. The activation path preserves the key.

## 3. `tabstack_research`'s current registration

- Definition: `src/decafclaw/skills/tabstack/tools.py:525-546`
- **NO `timeout` key present** → inherits the 180s default.
- Implementation: `tool_tabstack_research(ctx, query: str, mode: str = "balanced")` at line 378.
- Registered in `TOOLS` dict at line 426.

## 4. `/research`'s plan-stage configuration

`src/decafclaw/workflow/workflows/research.py`:

- **Schema** (lines 37-49):
  ```python
  "queries": {
      "type": "array",
      "items": {"type": "string"},
      "minItems": 3,
      "maxItems": 5,
      "description": "Search queries covering the topic.",
  }
  ```
- **System prompt** (lines 22-26):
  > "You plan focused research sweeps. Given a topic and any scope notes, generate **3-5 search queries** that together cover the topic without overlap. Each query should be specific enough to return a useful single-page result."
- **User prompt** in `_research_plan_prompt` (lines 78-79):
  > "Generate **3-5 search queries** that together cover this topic."
- Decorator: `@workflow("research", requires_skills=("tabstack",))` at line 137.

## 5. Existing tests for per-tool timeout

Dedicated suite at `tests/test_tool_timeout.py`. Relevant cases:

- `test_per_tool_short_override_wins` (lines 76-90) — `timeout: 1` overrides 300s global.
- `test_per_tool_long_override_survives` (lines 93-103) — `timeout: 5` overrides 1s global.
- `test_timeout_none_disables_wrapper` (lines 106-117) — explicit `None` opts out.
- `test_resolves_from_global_tool_definitions` (lines 174-201) — resolver finds override in global list.

Tests use `_register_extra_tool(ctx, name, fn, timeout_key=value, include_timeout_key=True)` to mock tool definitions inline.

`delegate_task`, `conversation_compact`, `claude_code_send` are NOT directly tested for their opt-out (they work the same way as generic tools given their `timeout: None` definitions).

## 6. `/research` unit-test compatibility check

`tests/test_workflow_research.py` (not directly read this session; flagged for verification during execute). The happy-path test uses mocked LLM responses; any mock `queries` list with >3 entries would fail schema validation after the cap change.

## Data flow summary (one line)

`tool_tabstack_research` dict → `_load_native_tools` → `ctx.tools.extra_definitions` → `_resolve_tool_timeout` → `_run_with_cancel(timeout_sec=600)`. Adding `"timeout": 600` to the dict at line 546 is the entire production code change.
