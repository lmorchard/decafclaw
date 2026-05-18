# Session notes — 547 skill autoload

## Phase 0 survey (read-only)

- **`auto-approve: false` cases**: None found. Only `auto-approve: true` in `src/decafclaw/skills/background/SKILL.md` and `src/decafclaw/skills/mcp/SKILL.md`. No backwards-compat risk from the trust-tier bypass.
- **`always-loaded` baseline**: vault, background, mcp (all bundled). Expected.
- **`tool_search` callers that need updating**:
  - `tests/test_search_tools.py` — direct unit tests of tool_search behavior. Full rewrite needed for new skill-level semantics.
  - `tests/test_eval_reflect.py` — only counts `tool_search` invocations; should still work regardless of return shape.
  - `tests/test_tool_registry.py:307` — asserts "## Available tools (use tool_search to load)" header text. Still applies but the section content shrinks.
  - No other code callers — `tool_search` is only invoked by the LLM as a tool.
- **Existing rejection paths in `discover_skills`**: `_BUNDLED_SKILLS_DIR`-only checks at lines 294-308 already strip `auto-approve` and `always-loaded` from non-bundled skills with a warning. The new code relaxes these checks to "workspace-only rejection" (keep the warning for workspace skills).
- **Tier-path mapping**: `discover_skills` iterates a fixed-order `scan_paths` list (workspace, admin, bundled, *extra_paths). Tier annotation is straightforward — track which scan group produced each `SkillInfo`. Resolved-symlink paths don't matter for tier; user intent comes from the configured scan path.
