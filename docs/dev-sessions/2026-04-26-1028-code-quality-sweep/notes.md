# Notes — code-quality sweep

## Findings deferred to later sessions

These surfaced during exploration but are out of scope for this sweep:

- **`run_agent_turn` (~314 lines) and `_handle_reflection` (~117 lines)** in `agent.py` are large enough to be friction during review, but the structure isn't actively bug-prone. A targeted refactor is a separate effort.
- **`http_server.create_app`** is a single closure with 40+ route handlers. Same story — large but stable. If we add more routes, splitting into a Router class becomes worthwhile.
- **WebSocket message-type strings** (~43 occurrences) are not centralized. A shared `web/message_types.py` module would help, but it's a coordinated edit across the JS client too.
- **`archive.append_message` has no file locking**. Today this is fine (per-conv files + serialized turns), but if we ever allow concurrent turns per conv, revisit.
- **`conversation_manager._save_conversation_state` is a hand-maintained field list.** Same pattern CLAUDE.md flags, but the `state` and `ctx` shapes differ enough that a mechanical fix isn't obvious. Worth a focused mini-session.
- **Skill `init(config, skill_config)` stores config as a module global.** Re-activating in another conversation overwrites; today only one config is in play, but it's a footgun if we ever support multi-tenant skills.
- **`embeddings.py:62` `except sqlite3.OperationalError: pass`** for the column-already-exists migration could log at debug. Tiny tweak but not critical.

## Session log

- Phase 1: replaced `getattr(obj, "field", default)` with direct attribute access at every verified call site. 13 `discovered_skills` sites (interactive_terminal, context_composer, commands ×3, agent ×2, schedules, eval/runner, tool_registry, delegate, skill_tools ×2). 1 `relevance` site (memory_context). 1 `active_model` site (health). Plus `manager` / `conv_id` / `cancelled` / `request_confirmation` / `event_context_id` / `channel_id` / `always_loaded` sites in agent.py, delegate.py, skill_tools.py, widget_input.py.
- Phase 2: removed dead `_skill_shutdown_hooks` storage (registered in `skill_tools._call_init` but never read anywhere — orphan code).
- Phase 3: replaced 9 `except: pass` (or `except SpecificType: pass`) sites with `except ... as exc: log.debug(...)`. Sites: `tools/__init__.py`, `agent.py`, `mattermost.py` ×4, `mcp_client.py`, `web/websocket.py`, `llm/__init__.py`.
- Phase 4: extracted `PRIORITY_ORDER` / `PRIORITY_GLYPH` / `meets_priority` to `notification_channels/__init__.py`. Three channels (email, mattermost_dm, vault_page) now import from there.
- Phase 5: wrapped `mail.py` attachment read with `asyncio.to_thread(path.read_bytes)`.
- Verification: `make lint` (ruff) clean, `make typecheck` (pyright) clean, `make test` 2027/2027 passing in ~21s.

## Outcome

Hygiene-only changes; no behavior changed. Five categories of convention violations and small smells closed across ~15 files.
