# Research: Tool-Owned Prompt Guidance (#928 & #998)

## 1. System Message Assembly & Per-Iteration Injection (`agent.py`, `context_composer.py`)

- **Initial Turn Assembly (`context_composer.py:661-670`):**
  Messages assembled in order:
  1. System prompt: `{"role": "system", "content": system_text}` (`context_composer.py:662`)
  2. Optional vault guide: `{"role": "system", "content": vault_guide_text}` (`context_composer.py:663-664`)
  3. Optional deferred tools catalog: `{"role": "system", "content": deferred_text}` (`context_composer.py:665-666`)
  4. Optional pre-empt skill matches: `{"role": "system", "content": preempt_skill_text}` (`context_composer.py:667-668`)
  5. `llm_history`: prior messages + memory context + user message (`context_composer.py:669`)
  6. Optional context status appended at tail (`context_composer.py:687`)

- **Turn Initialization (`agent.py:752-755`):**
  `self.deferred_msg` is captured:
  ```python
  if len(self.messages) > 1 and self.messages[1].get("role") == "system" and self.composed.deferred_tools:
      self.deferred_msg = self.messages[1]
  else:
      self.deferred_msg = None
  ```

- **Per-Iteration Update Loop (`agent.py:776-790`):**
  At each iteration in `TurnRunner._run_iteration_impl`:
  - `refresh_dynamic_tools(self.ctx)` is called (`agent.py:776`).
  - `all_tools, deferred_text = build_tool_list(self.ctx)` (`agent.py:777`).
  - If `deferred_text`:
    - Replaces `self.deferred_msg` in `self.messages` or inserts at index 1 (`agent.py:780-786`).
  - Else if `self.deferred_msg` exists in `self.messages`, removes it (`agent.py:787-789`).

## 2. Tool Definitions & Classification (`tool_definitions.py`, `tool_registry.py`)

- **Tool Schemas:**
  Tool definitions are dicts matching OpenAI function spec (`type: "function"`, `function: {"name": ..., "description": ..., "parameters": ...}`) plus framework-level keys: `"priority"`, `"timeout"`, `"_source_skill"` (`tool_registry.py:16-28`).
  Providers tolerate extra top-level fields or strip them (e.g. `openai_responses.py:149-152`).

- **Tool Collection (`tool_definitions.py:86-130`):**
  Order:
  1. `ctx.tools.extra_definitions` (activated skills) (`tool_definitions.py:94`)
  2. `TOOL_DEFINITIONS` (core tools) (`tool_definitions.py:94`)
  3. Preloaded definitions from `ctx.config.discovered_skills` for skills where `has_native_tools and grants_capability(skill_info)` (`tool_definitions.py:98-123`)
  4. MCP server tools (`tool_definitions.py:126-129`)
  Deduplicated by name (`_dedupe_by_name`).

- **Classification (`tool_registry.py:83-205`):**
  - Resolves priority: `force_critical` (config critical, fetched, activated skills) > declared `priority` > default `"normal"`.
  - Hidden skill tools (unactivated skills) bypass active set and go to deferred pool (`tool_registry.py:133-134, 176`).
  - Allowed / disallowed filters applied in `build_tool_list(ctx)` (`tool_definitions.py:198-207`).
  - `deferred_text` built via `build_deferred_list_text(deferred, core_names)` (`tool_registry.py:259-323`).

## 3. Token Estimation & Diagnostics (`context_composer.py:1565-1604`)

- `ContextComposer._compose_tools`:
  - `tool_tokens = estimate_tool_tokens(active)`: sums `estimate_tokens(json.dumps(td))` (`tool_registry.py:33-35`).
  - `text_tokens = estimate_tokens(deferred_text) if deferred_text else 0` (`context_composer.py:1596`).
  - Returns `SourceEntry(source="tools", tokens_estimated=tool_tokens + text_tokens, ...)`.

## 4. Skill Trust Model & Capability Gating (`skills/__init__.py`, `tests/test_discovered_skills_consumers.py`)

- **Trust Tiers (`skills/__init__.py:404`):**
  - `SKILL_CAPABILITY_TIERS = frozenset({"admin", "bundled", "extra"})`.
  - `workspace` tier is excluded: `workspace/skills/` is agent-writable.
  - `skills.grants_capability(info)` checks `info.trust_tier in SKILL_CAPABILITY_TIERS`.
- **Consumer Enforcement:**
  - `tests/test_discovered_skills_consumers.py` AST-scans all reads of `discovered_skills`.
  - Any new consumer reading `discovered_skills` must be registered in `REVIEWED_CONSUMERS` with a recorded rationale.
- **Skill Tool Association:**
  - `ctx.tools.skill_tool_names[skill_name] = set(tool_names)` (`skill_tools.py:811`).
  - `ctx.tools.skill_contributions[skill_name] = (tools_dict, tool_defs_list)` (`skill_tools.py:815`).
  - `config.skill_tool_owners: dict[str, str]` maps `tool_name -> skill_name` (`skills/__init__.py:693-740`).

## 5. Current Workspace Guidance in `AGENT.md` vs Tool Definitions

- `src/decafclaw/prompts/AGENT.md` lines 252-272 contains static tool selection guide:
  - `workspace_search` / `workspace_glob`: "find files first"
  - `workspace_read`: "see the exact current content and line numbers"
  - `workspace_edit`: surgical single-line/multi-line edit
  - `workspace_replace_lines` / `workspace_insert`: edits by line number
  - `workspace_append`: add to end of file
  - `workspace_move` / `workspace_delete`: rename or remove
  - `workspace_diff`: compare two files
  - `workspace_write`: new files or full rewrites only
- Currently all of these except `workspace_read`, `workspace_write`, and `workspace_edit` are `priority: "normal"` or `"low"`.
- When deferred, the model sees guidance in AGENT.md for tools that are not callable.
