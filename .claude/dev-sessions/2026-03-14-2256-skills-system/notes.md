# Skills System — Session Notes

## What we built

Agent Skills standard-compatible skills system for DecafClaw:

- **SKILL.md parser** with YAML frontmatter, lenient parsing
- **3-tier discovery**: workspace > agent-level > bundled, with `requires.env` gating
- **Skill catalog** injected into system prompt after AGENT.md
- **`activate_skill` tool** with confirmation (yes/no/always), lazy Python module loading
- **`refresh_skills` tool** for mid-session re-discovery without restart
- **Per-conversation tool registration** via `ctx.extra_tools` / `ctx.extra_tool_definitions`
- **Permissions file** at `data/{agent_id}/skill_permissions.json` (outside workspace sandbox)
- **"Always" confirmation option** in both interactive terminal and Mattermost
- **Tabstack migrated** from hardcoded tool to bundled skill
- **Tabstack SKILL.md improved** with trigger phrases, cost guidance, and error recovery from openclaw version
- **debug_context upgraded** to write full JSON (messages + tool definitions) to workspace

## Bugs found and fixed during testing

- **LLM skipping activation**: agent called tabstack tools directly without activating first. Fixed with stronger "MUST activate first" language in catalog and tool descriptions.
- **`web_fetch` referencing tabstack**: tool description pointed to `tabstack_extract_markdown` before activation. Fixed to be conditional.
- **Skill state not persisting across Mattermost turns**: `req_ctx` forked fresh each turn, losing `extra_tools`. Fixed with `conv_skill_state` dict alongside `histories`.
- **`skills.py` vs `skills/` package collision**: standalone module shadowed by package directory. Fixed by merging into `skills/__init__.py`.

## Validated with real community skills

- ClawHub weather skill (shell-based, `curl` only) — full chain: discovery → activation confirmation → shell confirmation → result
- Also tested: gh-issues, github, tmux, coding-agent, skill-creator from OpenClaw bundled skills
- Skills requiring external binaries work but need manual installation

## Key design decisions

- **Hybrid model**: native Python skills get structured tool calling, shell-based Agent Skills standard skills use `shell` tool guided by SKILL.md instructions
- **Lazy everything**: catalog in prompt at startup, but tools only load on activation, init() only runs on first use
- **Config holds discovered_skills**: not ctx, because Mattermost forks ctx per turn but shares config
- **Permissions outside workspace**: agent can't grant itself permission to activate skills

## 91 tests, 13 commits on skills-system branch
