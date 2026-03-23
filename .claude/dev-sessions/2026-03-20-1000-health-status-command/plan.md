# Health/Status Diagnostic Command Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `health_status` tool and `!health` command that reports agent diagnostic state across all major subsystems.

**Architecture:** A single tool module (`tools/health.py`) gathers data from MCP registry, heartbeat, tool registry, and embeddings, rendering each section independently so failures are isolated. A bundled skill (`skills/health/SKILL.md`) exposes it as a user-invokable `!health` command.

**Tech Stack:** Python, SQLite (read-only queries), existing subsystem APIs.

---

### Task 1: Make `_read_last_heartbeat` public

**Files:**
- Modify: `src/decafclaw/heartbeat.py:212` — rename function
- Modify: `src/decafclaw/heartbeat.py:254,278` — update internal callers

- [ ] **Step 1: Rename `_read_last_heartbeat` to `read_last_heartbeat`**

In `src/decafclaw/heartbeat.py`, rename the function and update all internal call sites (lines 254 and 278). Also rename `_heartbeat_timestamp_path` to `heartbeat_timestamp_path` since it's used by the renamed function.

Actually — only rename `_read_last_heartbeat`. The timestamp path helper can stay private since `read_last_heartbeat` wraps it.

- [ ] **Step 2: Run tests to verify nothing breaks**

Run: `make check && make test`
Expected: All pass — no external callers of the private function yet.

- [ ] **Step 3: Commit**

```bash
git add src/decafclaw/heartbeat.py
git commit -m "refactor: make read_last_heartbeat public for cross-module use"
```

---

### Task 2: Create the `health_status` tool — Process section

**Files:**
- Create: `src/decafclaw/tools/health.py`
- Create: `tests/test_health.py`

- [ ] **Step 1: Write the failing test for the process section**

```python
# tests/test_health.py
"""Tests for the health_status diagnostic tool."""
import time
from unittest.mock import patch

import pytest

from decafclaw.tools.health import tool_health_status


@pytest.mark.asyncio
async def test_health_process_section(ctx):
    """Process section shows uptime and memory."""
    result = await tool_health_status(ctx)
    assert "## Agent Health" in result
    assert "### Process" in result
    assert "Uptime:" in result
    assert "Memory (RSS):" in result
    assert "MB" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_health.py -v`
Expected: FAIL — module doesn't exist yet.

- [ ] **Step 3: Implement the process section**

Create `src/decafclaw/tools/health.py` with:
- Module-level `_start_time = time.monotonic()`
- `_format_uptime(seconds)` helper → "2h 14m 32s" format
- `_process_section()` → returns markdown lines for uptime + RSS memory
- `tool_health_status(ctx)` async function that calls `_process_section()` and assembles the report
- Platform-aware RSS: `sys.platform == "darwin"` → bytes, else KB

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/tools/health.py tests/test_health.py
git commit -m "feat: add health_status tool with process section"
```

---

### Task 3: Add MCP Servers section

**Files:**
- Modify: `src/decafclaw/tools/health.py`
- Modify: `tests/test_health.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_health_mcp_section_no_servers(ctx):
    """MCP section handles no registry gracefully."""
    with patch("decafclaw.tools.health.get_registry", return_value=None):
        result = await tool_health_status(ctx)
    assert "No MCP servers configured" in result


@pytest.mark.asyncio
async def test_health_mcp_section_with_servers(ctx):
    """MCP section shows server status table."""
    mock_registry = MagicMock()
    mock_state = MagicMock()
    mock_state.status = "connected"
    mock_state.tools = {"mcp__test__a": None, "mcp__test__b": None}
    mock_state.retry_count = 0
    mock_registry.servers = {"test-server": mock_state}

    with patch("decafclaw.tools.health.get_registry", return_value=mock_registry):
        result = await tool_health_status(ctx)
    assert "test-server" in result
    assert "connected" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_health.py -v`
Expected: FAIL — no MCP section yet.

- [ ] **Step 3: Implement `_mcp_section()`**

Add to `health.py`:
- Import `get_registry` from `..mcp_client`
- `_mcp_section()` → markdown table with Server/Status/Tools/Retries columns
- Handle no registry, empty servers, and per-server errors
- Wire into `tool_health_status`

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_health.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/tools/health.py tests/test_health.py
git commit -m "feat: add MCP servers section to health_status"
```

---

### Task 4: Add Heartbeat section

**Files:**
- Modify: `src/decafclaw/tools/health.py`
- Modify: `tests/test_health.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_health_heartbeat_disabled(ctx):
    """Heartbeat section shows disabled when no interval."""
    ctx.config = dataclasses.replace(ctx.config,
        heartbeat=dataclasses.replace(ctx.config.heartbeat, interval=""))
    result = await tool_health_status(ctx)
    assert "disabled" in result.lower()


@pytest.mark.asyncio
async def test_health_heartbeat_enabled(ctx, tmp_path):
    """Heartbeat section shows timing info when enabled."""
    ctx.config = dataclasses.replace(ctx.config,
        heartbeat=dataclasses.replace(ctx.config.heartbeat, interval="30m"))
    # Write a fake last-run timestamp
    ts_path = ctx.config.workspace_path / ".heartbeat_last_run"
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    ts_path.write_text(str(time.time() - 300))  # 5 min ago

    result = await tool_health_status(ctx)
    assert "30m" in result
    assert "ago" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_health.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `_heartbeat_section(config)`**

Add to `health.py`:
- Import `parse_interval` and `read_last_heartbeat` from `..heartbeat`
- `_heartbeat_section(config)` → enabled/disabled, interval, last run (relative), next due
- `_format_relative_time(seconds)` helper → "5m ago", "in 25m", "overdue by 2m"
- Wire into `tool_health_status`

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_health.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/tools/health.py tests/test_health.py
git commit -m "feat: add heartbeat section to health_status"
```

---

### Task 5: Add Tools section

**Files:**
- Modify: `src/decafclaw/tools/health.py`
- Modify: `tests/test_health.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_health_tools_section(ctx):
    """Tools section shows active/deferred counts."""
    result = await tool_health_status(ctx)
    assert "### Tools" in result
    assert "Active:" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_health.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `_tools_section(ctx)`**

Add to `health.py`:
- Import `classify_tools`, `estimate_tool_tokens` from `.tool_registry`
- Import `TOOL_DEFINITIONS` from the tools package
- `_tools_section(ctx)` → active count, deferred count, token usage, budget
- Gather all tool defs (built-in + ctx.extra_tool_definitions + MCP)
- Wire into `tool_health_status`

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_health.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/tools/health.py tests/test_health.py
git commit -m "feat: add tools section to health_status"
```

---

### Task 6: Add Embeddings section

**Files:**
- Modify: `src/decafclaw/tools/health.py`
- Modify: `tests/test_health.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_health_embeddings_no_db(ctx):
    """Embeddings section handles missing database."""
    result = await tool_health_status(ctx)
    assert "No embedding" in result or "Embeddings" in result


@pytest.mark.asyncio
async def test_health_embeddings_with_data(ctx):
    """Embeddings section shows counts when DB exists."""
    # Create a minimal embeddings DB
    import sqlite3
    db_path = ctx.config.workspace_path / "embeddings.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE memory_embeddings (
        id INTEGER PRIMARY KEY, file_path TEXT, entry_hash TEXT UNIQUE,
        entry_text TEXT, embedding BLOB, source_type TEXT DEFAULT 'memory',
        created_at TEXT)""")
    conn.execute("INSERT INTO memory_embeddings VALUES (1,'f','h1','t',X'00','memory','2024-01-01')")
    conn.execute("INSERT INTO memory_embeddings VALUES (2,'f','h2','t',X'00','conversation','2024-01-01')")
    conn.commit()
    conn.close()

    result = await tool_health_status(ctx)
    assert "Memory:" in result
    assert "Conversation:" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_health.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `_embeddings_section(config)`**

Add to `health.py`:
- `_embeddings_section(config)` → total count, by source type
- Open DB directly via `sqlite3.connect` on `config.workspace_path / "embeddings.db"` (read-only)
- Handle missing DB (file doesn't exist) and locked/corrupted DB (catch `sqlite3.Error`)
- Wire into `tool_health_status`

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_health.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/tools/health.py tests/test_health.py
git commit -m "feat: add embeddings section to health_status"
```

---

### Task 7: Register the tool and create the `!health` command skill

**Files:**
- Modify: `src/decafclaw/tools/__init__.py` — import and merge HEALTH_TOOLS/HEALTH_TOOL_DEFINITIONS
- Create: `src/decafclaw/skills/health/SKILL.md`

- [ ] **Step 1: Add HEALTH_TOOLS to the tool registry**

In `src/decafclaw/tools/__init__.py`:
- Add import: `from .health import HEALTH_TOOL_DEFINITIONS, HEALTH_TOOLS`
- Merge into `TOOLS` dict: `**HEALTH_TOOLS`
- Append to `TOOL_DEFINITIONS`: `+ HEALTH_TOOL_DEFINITIONS`

- [ ] **Step 2: Define HEALTH_TOOLS and HEALTH_TOOL_DEFINITIONS in health.py**

At the bottom of `src/decafclaw/tools/health.py`, add:

```python
HEALTH_TOOLS = {
    "health_status": tool_health_status,
}

HEALTH_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "health_status",
            "description": (
                "Show agent health and diagnostic status. "
                "Reports process uptime, memory usage, MCP server connections, "
                "heartbeat timing, tool deferral stats, and embedding index size. "
                "Use when asked about agent status, health, diagnostics, or uptime."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
```

- [ ] **Step 3: Create the `!health` skill**

Create `src/decafclaw/skills/health/SKILL.md`:

```markdown
---
name: health
description: Show agent diagnostic status — process, MCP, heartbeat, tools, embeddings
user-invocable: true
context: inline
allowed-tools: health_status
---

Call the `health_status` tool and share the full output with the user. Do not summarize or omit any sections.
```

- [ ] **Step 4: Run full check**

Run: `make check && make test`
Expected: All pass. The health tool is now registered and the skill is discoverable.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/tools/__init__.py src/decafclaw/tools/health.py src/decafclaw/skills/health/SKILL.md
git commit -m "feat: register health_status tool and add !health command skill"
```

---

### Task 8: Integration test — full report

**Files:**
- Modify: `tests/test_health.py`

- [ ] **Step 1: Write an integration test that checks the full report**

```python
@pytest.mark.asyncio
async def test_health_full_report_all_sections(ctx):
    """Full report includes all five section headers."""
    result = await tool_health_status(ctx)
    assert "## Agent Health" in result
    assert "### Process" in result
    assert "### MCP Servers" in result
    assert "### Heartbeat" in result
    assert "### Tools" in result
    assert "### Embeddings" in result
```

- [ ] **Step 2: Run the full test suite**

Run: `make check && make test`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_health.py
git commit -m "test: add integration test for full health report"
```

---

### Task 9: Docs update

**Files:**
- Modify: `CLAUDE.md` — add `health.py` to key files, note the `!health` command
- Modify: `README.md` — add `health_status` to tool table if one exists

- [ ] **Step 1: Update CLAUDE.md key files list**

Add under key files:
- `src/decafclaw/tools/health.py` — Health/diagnostic status tool
- `src/decafclaw/skills/health/` — `!health` user command

- [ ] **Step 2: Update README.md tool table**

Add `health_status` row with description.

- [ ] **Step 3: Run lint**

Run: `make check`
Expected: Pass.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: add health_status tool and !health command to docs"
```
