# Code-quality and maintainability sweep

## Goal

After a stretch of feature landings (widgets phase 2, tool-result clearing, narrative summaries, scheduled-task ctx fixes, etc.), do a focused pass to clean up convention violations and clear small maintainability traps that have accumulated across the codebase.

## Scope (in)

Each item is a real, verified violation of conventions stated in `CLAUDE.md`:

1. **`getattr(obj, "field", default)` on declared dataclass fields.** CLAUDE.md flags this as a maintenance trap: "Don't `getattr(obj, "_field", fallback)` to read undeclared attributes." Verified call sites read fields that *are* declared on `Config`, `Context`, or `SkillInfo`:
   - `getattr(config, "discovered_skills", [])` — 13 sites; field is on `Config` (config.py:177)
   - `getattr(config, "relevance", None)` — memory_context.py:64; field is on `Config`
   - `getattr(ctx, "active_model", "")` — agent.py:586, health.py:395; field is on `Context`
   - `getattr(parent_ctx, "conv_id", "")`, `event_context_id`, `channel_id` — delegate.py:56–59
   - `getattr(ctx, "manager", None)`, `getattr(ctx, "conv_id", None)` — agent.py:382–383
   - `getattr(skill_info, "always_loaded", False)` — skill_tools.py:214

2. **`hasattr(ctx, "_skill_shutdown_hooks")` + setattr** — skill_tools.py:205–209 stores skill `shutdown` callbacks on a lazy-created undeclared attribute. CLAUDE.md flags the pattern, but on closer look the stored hooks are *never invoked anywhere*. Remove the dead storage block. (If skill deactivation gets wired up later, the new code can declare the field properly on `Context`.)

3. **Bare `except: pass`** — CLAUDE.md: "never acceptable; use `except Exception as exc: log.debug(...)`."
   - tools/__init__.py:261 (MCP registry probe)
   - agent.py:1374 (context sidecar write)
   - mattermost.py:117, 616, 772, 826 (best-effort post/edit)
   - mcp_client.py:579 (exit-stack cleanup on failure)
   - web/websocket.py:710 (ws send)
   - llm/__init__.py:127 (KeyError on missing model — bare on a specific exception, but the pattern is the same)

4. **Duplicated `_PRIORITY_ORDER` / `_PRIORITY_GLYPH` / `_meets_priority`** across `notification_channels/{vault_page,mattermost_dm,email}.py` (3 copies, identical body). Extract to `notification_channels/__init__.py`.

5. **Sync file I/O on the event loop** — `mail.py:86` reads attachments with blocking `path.open("rb").read()` inside async `send_mail`. Wrap with `asyncio.to_thread`.

## Scope (out — explicitly rejected)

The exploration phase surfaced larger refactor suggestions; rejecting these for this sweep because they violate "smallest reasonable changes" and risk regressions in code that has been stable:

- Splitting `run_agent_turn` (314 lines), `_handle_reflection` (117 lines), or `http_server.create_app` (1500+ lines). These are not bug-prone; restructuring them is a separate effort with its own design discussion.
- Centralizing all WebSocket message types into a constants module. Big surface, low payoff for a quality sweep.
- `archive.append_message` lacks file locking. Per-conversation files + serialized turns mean this isn't actually a bug today.
- `conversation_manager._save_conversation_state` hand-maintained field list. It's the right shape of finding, but the `state` and `ctx` shapes differ enough that a mechanical fix isn't safe — defer.
- Cosmetic fixes (`re as _re` import, missing docstrings on private helpers, etc.).

## Success criteria

- All in-scope violations removed (verifiable by grep).
- `make check` clean.
- `make test` green.
- No behavior changes (this is hygiene, not feature work).

## Risk

Low. All changes are mechanical: replace getattr with attribute access, change exception handler bodies, move three identical helpers into a shared module, wrap one read with `to_thread`. Test suite covers the affected paths.
