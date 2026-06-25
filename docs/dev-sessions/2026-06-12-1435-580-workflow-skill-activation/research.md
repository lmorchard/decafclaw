# Research — Skill activation & workflow tool registry

Documentarian sweep, 2026-06-12. Source: `Explore` subagent against the worktree.

## 1. `activate_skill` mechanics

`tool_activate_skill` at `src/decafclaw/tools/skill_tools.py:127-178`. Mutates per-turn ctx:

- `ctx.tools.extra.update(tools)` (line 203) — tool-name → callable
- `ctx.tools.extra_definitions.extend(tool_defs)` (line 204) — JSON tool specs
- `ctx.tools.dynamic_providers[name]` (line 209) + `ctx.tools.dynamic_provider_names[name]` (line 212) — for skills exporting `get_tools(ctx)`
- `ctx.skills.activated.add(name)` (line 234)
- Always-loaded skills also append tool names to `ctx.config.always_loaded_skill_tools` (lines 223-226)

The system prompt's `<loaded_skills>` section (`src/decafclaw/prompts/__init__.py:88-113`) is built from `always-loaded` skill bodies at session start — **not** from per-conversation activations. Per-conversation activations land in the next turn's tool-result message, not in the system prompt.

## 2. Skill discovery & registration

`discover_skills` at `src/decafclaw/skills/__init__.py:258-349`. Scan order:

1. `data/{agent_id}/workspace/skills/` (workspace tier)
2. `data/{agent_id}/skills/` (admin tier)
3. `src/decafclaw/skills/` (bundled tier)
4. `config.extra_skill_paths` (extra tier)

`always-loaded: true` from SKILL.md frontmatter parsed at line 124. Config can opt a trusted-tier skill in via `config.skills_always_loaded` (lines 322-330). Workspace skills cannot self-mark always-loaded (stripped at lines 311-317).

`config.discovered_skills` stores the catalog. Tool ownership index built by `build_skill_tool_owners` at `skills/__init__.py:352-386` → `config.skill_tool_owners` (used in `tools/tool_registry.py:100`).

## 3. Tool registry across TurnKinds

**TurnKind.USER (interactive):** `_setup_turn_state` (`src/decafclaw/agent.py:368-434`) auto-activates always-loaded skills at lines 395-411 — calls full `activate_skill` flow for each. Then `ContextComposer.compose` → `_compose_tools` → `classify_tools` (`tool_definitions.py:126-166`). Skill tools from activated skills sit in `ctx.tools.extra_definitions` and are never deferred (`tool_definitions.py:136-139`).

**TurnKind.HEARTBEAT_SECTION / SCHEDULED_TASK / CHILD_AGENT:** `Context.for_task()` (`src/decafclaw/context.py:105-150`) — skips `_setup_turn_state`, skips auto-activation. `collect_all_tool_defs` (`tool_definitions.py:85-123`) pre-loads tool DEFINITIONS so `tool_search` can find them, but does NOT call `ctx.tools.extra.update` — so the skill tools aren't actually callable. Skill body is NOT injected into system prompt either.

**TurnKind.WORKFLOW:** `conversation_manager.py:1405-1410` explicitly notes "WORKFLOW deliberately reuses [the USER] path for now" — full Context + `_restore_per_conv_state` at line 1419. **BUT** `run_workflow_turn` (`src/decafclaw/workflow/resume.py:30-79`) **does NOT call `_setup_turn_state`** — no always-loaded activation. AND the workflow engine bypasses `ContextComposer` entirely — `wf.llm_call` builds its own LLM request from the orchestrator's `system=`/`prompt=` args (`src/decafclaw/workflow/llm.py`). So:

- `ctx.tools.extra` is empty at workflow-turn start.
- `wf.tool_call` calls `execute_tool(ctx, name, args)`, which fails with `[error: unknown tool ...]` for any skill-bundled tool.
- The workflow's LLM never sees a `<loaded_skills>` or `<skill_catalog>` section either — its system prompt is whatever the orchestrator passes in.

## 4. `@workflow` decorator and `WorkflowSpec`

`src/decafclaw/workflow/registry.py:20-26` — the decorator.

`WorkflowSpec` dataclass at lines 10-15:

```python
@dataclass
class WorkflowSpec:
    name: str
    fn: Callable[[Any], Awaitable[Any]]
    model: str = "vertex-gemini-flash"
```

No skill/tool declaration surface today. Registry is module-level `REGISTRY: dict[str, WorkflowSpec] = {}` (line 17); `get_workflow(name)` returns the spec or None (lines 29-30).

## 5. Always-loaded skills & non-interactive turns

Three bundled always-loaded skills: `vault`, `background`, `mcp` (frontmatter in their respective SKILL.md files).

What `always-loaded: true` does has two halves:

- **(a) System prompt:** skill BODY auto-injected into `<loaded_skills>` (`prompts/__init__.py:88-113`).
- **(b) Tools:** skill TOOLS auto-activated at start of USER (and WAKE) turns via `_setup_turn_state` (`agent.py:395-411`).
- **(c) Critical priority:** always-loaded skill tools forced to `critical` priority (`tools/tool_registry.py:33-45`) — never deferred.

`_setup_turn_state` gate at `conversation_manager.py:1396-1403`: only USER and WAKE turn kinds call it. Task-mode turns (HEARTBEAT_SECTION, SCHEDULED_TASK, CHILD_AGENT) and WORKFLOW turns skip it.

For task-mode turns: system prompt section IS present (always-loaded body in `<loaded_skills>`), but tools are NOT active. Tool DEFINITIONS are pre-loaded via `collect_all_tool_defs` so `tool_search` can discover them, but they're in the deferred pool.

For workflow turns: NEITHER half of always-loaded fires — `run_workflow_turn` skips `_setup_turn_state`, and the workflow engine bypasses the system-prompt assembly entirely.

## Implications for the feature

The framing in #580 ("can't reach `tabstack_research`") is narrower than the actual gap: **workflows can't reach ANY skill tool** today, not just tabstack. That includes the always-loaded set (vault, background, mcp) which the rest of the system treats as universally available.

`@workflow` and `WorkflowSpec` are the only surface where per-workflow declarations could live; the registry has no other anchor.
