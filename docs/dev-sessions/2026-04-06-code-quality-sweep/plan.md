# Code Quality Sweep — Plan

Full codebase review produced ~40 findings across 5 tiers. Grouped into phases that can be committed independently.

## Phase 1: Bug fixes (Tier 1)

1. `eval/reflect.py:37` — Fix KeyError for multi-turn test failures (`test_case["input"]` → handle `turns`)
2. `mattermost.py:575-587` — Move `finalize()` inside `finally` block (before `busy = False`)
3. `mattermost.py:673` — Replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()`
4. `interactive_terminal.py:156` — Add try/except around `run_agent_turn` to print error and continue
5. `web/conversations.py` — Add `asyncio.Lock` to `ConversationIndex` (match `ConversationFolderIndex`)
6. `claude_code/tools.py:445` — Move `try` block to start immediately after `open()`

## Phase 2: Dead code removal (Tier 2)

7. `agent.py:708-812` — Remove deprecated `_prepare_messages` (~105 lines)
8. `config.py:380-399` — Remove unused `reload_env()`
9. `context.py:170` — Remove redundant `child.tools = self.tools` assignment, fix misleading comment
10. `media.py:71` — Remove dead `format_image_url()` from base class
11. `media.py:234` — Remove redundant `format_attachment_card()` override
12. `commands.py:66` — Check if `substitute_arguments` alias is used; remove if not
13. Remove unused imports: `core.py` (Path), `compaction.py` (Path), `commands.py` (field), `eval/runner.py` (load_config), `mcp_tools.py` (init_mcp, shutdown_mcp), `workspace_tools.py:144` (redundant ToolResult)

## Phase 3: Convention fixes (Tier 3)

14. `eval/runner.py:209` — Use `dataclasses.replace()` instead of direct mutation
15. `skill_tools.py:187` — Add `_always_loaded_skill_tools` as a proper field on config
16. `skill_tools.py:218-219` — Fix `tool_refresh_skills` config mutation
17. `claude_code/tools.py:818` — Use `ToolResult(text=...)` instead of bare string
18. `vault/tools.py:348` — Same fix for `tool_vault_list`
19. `shell_tools.py:145` — Return `ToolResult` consistently

## Phase 4: Structural improvements (Tier 4)

20. Extract shared shell approval helper from `shell_tools.py` + `background_tools.py`
21. `context.py:74` — Replace string `task_mode` with `ComposerMode` enum
22. `agent.py:849-851` — Move `_task_mode_map` to module level
23. `agent.py:304-334` — Extract shared tool-list logic between `_build_tool_list` and `_compose_tools`
24. `memory_context.py:186` — Decouple from vault skill (use a resolver function passed in, or import at call time with better error handling)
25. `mcp_client.py` — Extract duplicated resource/prompt parsing into helper

Note: Large structural refactors (splitting http_server.py, agent.py, websocket.py, markdown_vault/tools.py) are tracked but deferred — they're big enough to warrant their own sessions.

## Phase 5: Minor cleanups (Tier 5)

26. `mattermost_display.py` — Add `log.debug` to bare `except Exception: pass` blocks
27. `mattermost_display.py` — Add public `get_post()` to MattermostClient, stop accessing `_http` directly
28. `mattermost_ui.py` — Add TTL cleanup for abandoned confirmation tokens
29. `llm.py:127` — Cap `_all_events` accumulation
30. `embeddings.py:137` — Remove dead 429 retry branch in except block
31. `embeddings.py` — Move `SOURCE_BOOSTS` to module level
32. `agent.py:155` — Replace defensive `getattr(ctx, "cancelled", None)` with plain `ctx.cancelled`
33. `schedules.py:158`, `http_server.py:844` — Move `import re` to module level
34. `mcp_client.py` — Move `base64`/`mimetypes` imports to module level
35. `mcp_client.py` — Parallelize `connect_all` with `asyncio.gather`

## Execution

Work phase by phase. Lint + test after each phase. Commit after each phase.
