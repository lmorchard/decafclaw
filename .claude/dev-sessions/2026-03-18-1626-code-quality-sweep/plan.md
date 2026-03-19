# Code Quality Sweep — Plan

## Status: Ready

## Execution Order

Each item is a commit. Run `make check && make test` between items.

1. **Item 7** — Fix max_tool_iterations default mismatch (1 line)
2. **Item 3** — Make compaction functions public (rename)
3. **Item 1** — Extract persistence.py from archive.py
4. **Item 4** — ConversationMeta.to_dict() + replace inline dicts
5. **Item 2** — Move button builders to mattermost_ui.py
6. **Item 5** — Stop mutating Config (module-level skill def cache)
7. **Item 10** — Make web_fetch async
8. **Item 8** — Deduplicate heartbeat agent turn logic
9. **Item 6** — Context getattr cleanup + add missing fields to __init__
10. **Item 12** — Move avoidable deferred imports to top-level
11. **Item 11** — Standardize tool error returns to ToolResult
12. **Item 13** — Store create_task references
13. **Item 9** — Refactor websocket_chat dispatch

---

## Item 7: Fix max_tool_iterations default (1 line)

**File:** `config.py`
- Line 108: Change `max_tool_iterations: int = 200` to `max_tool_iterations: int = 30`
- Matches the `load_config()` env var default of `"30"`

---

## Item 3: Make compaction functions public

**File:** `compaction.py`
- Rename `_flatten_messages` to `flatten_messages`
- Rename `_estimate_tokens` to `estimate_tokens`
- Update all internal references in compaction.py

**Import site:** `web/websocket.py` line 150:
- `from ..compaction import _estimate_tokens, _flatten_messages` → `from ..compaction import estimate_tokens, flatten_messages`
- Update call sites on lines 154-155

Note: `tools/core.py` has its own local `_estimate_tokens` inside `tool_context_stats` — different function, don't change.

---

## Item 1: Extract persistence.py from archive.py

**Create:** `src/decafclaw/persistence.py`

**Move from archive.py (lines 56-97):**
- `_skills_path`, `write_skills_state`, `read_skills_state`
- `_skill_data_path`, `write_skill_data`, `read_skill_data`

**Import site:** `agent.py` line 340:
- `from .archive import read_skill_data, read_skills_state, write_skill_data, write_skills_state`
- → `from .persistence import read_skill_data, read_skills_state, write_skill_data, write_skills_state`

---

## Item 4: ConversationMeta.to_dict()

**File:** `web/conversations.py` — add to ConversationMeta:
```python
def to_dict(self) -> dict:
    return {
        "conv_id": self.conv_id,
        "title": self.title,
        "created_at": self.created_at,
        "updated_at": self.updated_at,
    }
```

**Replace inline dicts in:**
- `web/websocket.py` — ~6 sites (list_convs, list_archived, unarchive x2, create, archive)
- `http_server.py` — ~4 sites (list, create, get, rename)

Pattern: `[{"conv_id": c.conv_id, "title": c.title, ...} for c in convs]` → `[c.to_dict() for c in convs]`

---

## Item 2: Move button builders to mattermost_ui.py

**Create:** `src/decafclaw/mattermost_ui.py`

**Move from http_server.py:**
- `ConfirmTokenRegistry` class (lines 17-53)
- `_token_registry` instance (line 57)
- `get_token_registry()` function (lines 59-61)
- `build_confirm_buttons` (lines 346-445)
- `build_stop_button` (lines 448-477)

**Update imports:**
- `mattermost.py` line 367: `from .http_server import build_stop_button` → `from .mattermost_ui import build_stop_button`
- `mattermost.py` line 1009: `from .http_server import build_confirm_buttons` → `from .mattermost_ui import build_confirm_buttons`
- `http_server.py`: add `from .mattermost_ui import get_token_registry`, replace `_token_registry` references

---

## Item 5: Stop mutating Config

**Problem:** `agent.py` line 112 monkey-patches `ctx.config._preloaded_skill_defs`

**Fix:** Module-level cache in `agent.py`:
```python
_skill_def_cache: dict[int, list] = {}
```
- Replace `getattr(ctx.config, "_preloaded_skill_defs", None)` with `_skill_def_cache.get(id(ctx.config))`
- Replace `ctx.config._preloaded_skill_defs = _cached` with `_skill_def_cache[id(ctx.config)] = _cached`

**Also:** Add `invalidate_skill_cache(config)` function, called from `tool_refresh_skills` in skill_tools.py after reload.

---

## Item 10: Make web_fetch async

**File:** `tools/core.py` — change `tool_web_fetch` from sync to async:
```python
async def tool_web_fetch(ctx, url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        resp = await client.get(url)
    ...
```
No other changes needed — `execute_tool` auto-detects async.

---

## Item 8: Deduplicate heartbeat

**Extract from heartbeat.py:** `run_section_turn(config, event_bus, section, timestamp, index)` — the common "create context, build prompt, run agent turn, check result" logic.

**Update:**
- `heartbeat.py:run_heartbeat_cycle` — call `run_section_turn` in loop
- `heartbeat_tools.py:_run_heartbeat_to_channel` — call `run_section_turn`, then do Mattermost posting with result

---

## Item 6: Context getattr cleanup

**Add to Context.__init__:**
- `self._current_iteration: int = 1`
- `self.deferred_tool_pool: list = []`

**Replace getattr with direct access** for all fields defined in __init__:
- `getattr(ctx, "conv_id", "")` → `ctx.conv_id or ""`
- `getattr(ctx, "cancelled", None)` → `ctx.cancelled`
- `getattr(ctx, "extra_tools", {})` → `ctx.extra_tools`
- etc. (~57 sites across 14 files)

**Leave getattr for:** fields not on Context (e.g. `getattr(config, ...)`)

**Add comment to fork_for_tool_call:** note that __dict__.update shares mutable containers

---

## Item 12: Move deferred imports to top-level

**Can move to top-level in agent.py:**
- `from .persistence import ...` (after Item 1)
- `from .media import ToolResult, extract_workspace_media`
- `from .tools.skill_tools import restore_skills`
- `from .tools.search_tools import SEARCH_TOOL_DEFINITIONS`
- `from .tools.tool_registry import ...`
- `from .embeddings import index_entry`

**Can move in http_server.py:**
- `from .web.auth import ...`
- `from .web.conversations import ConversationIndex`

**Can move in tools/__init__.py:**
- `from .search_tools import SEARCH_TOOLS`

**Must stay deferred (circular):**
- `heartbeat.py`: `from .agent import run_agent_turn`
- `tools/delegate.py`: `from ..agent import run_agent_turn` and `from . import TOOLS`
- `tools/core.py`: `from . import TOOL_DEFINITIONS`
- `tools/tool_registry.py`: `from . import TOOL_DEFINITIONS`
- `web/websocket.py`: `from ..agent import run_agent_turn`

Add `# deferred: circular dep` comment to remaining deferred imports.

---

## Item 11: Standardize tool returns to ToolResult

**Largest change:** `workspace_tools.py` — ~46 bare string error returns. Add helper:
```python
def _error(msg: str) -> ToolResult:
    return ToolResult(text=msg)
```
Replace `return f"[error: ...]"` with `return _error(f"[error: ...]")`.

**Other files:** `core.py` (1), `mcp_tools.py` (2), `skill_tools.py` (3), `delegate.py` (1)

---

## Item 13: Store create_task references

**Pattern per file:**
```python
_background_tasks: set[asyncio.Task] = set()

task = asyncio.create_task(coro)
_background_tasks.add(task)
task.add_done_callback(_background_tasks.discard)
```

**Sites:**
- `agent.py` line 46: indexing task
- `web/websocket.py` line 383: event forwarding tasks (track in WsState after Item 9)

---

## Item 9: Refactor websocket_chat dispatch

**Extract each elif branch** into a named async handler function.

**Create dispatch dict:**
```python
_HANDLERS = {
    "list_convs": _handle_list_convs,
    "send": _handle_send,
    ...
}
```

**Bundle shared mutable state** into a dataclass:
```python
@dataclass
class _WsState:
    active_conv_ids: set[str]
    agent_tasks: set[asyncio.Task]
    cancel_events: dict[str, asyncio.Event]
```

**Main loop becomes:**
```python
handler = _HANDLERS.get(msg_type)
if handler:
    await handler(ws_send, index, username, msg, state, ...)
else:
    await ws_send({"type": "error", ...})
```
