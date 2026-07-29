# progress_tracker Widget + Auto-Emit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a display-only `progress_tracker` widget (multi-step status list) and auto-emit it into the sticky slot from the checklist tool and the project skill, giving the user a live progress view for free.

**Architecture:** A new bundled widget (`web/static/widgets/progress_tracker/`, auto-registered by directory scan). The checklist tools (`checklist_tools.py`) become async and call `sticky.set_sticky`/`clear_sticky` after each mutation; the project skill (`skills/project/tools.py`, already async) does the same during the EXECUTING phase. Both build a `progress_tracker` data payload from their own task model and emit fail-open — a sticky failure never breaks the underlying tool. No new agent tools; no eval cases (deterministic side-effect wiring).

**Tech Stack:** Python 3.13, Lit (web components), jsonschema (widget validation), pytest / pytest-asyncio.

## Global Constraints

- Widget = `widget.json` (meta) + `widget.js` (Lit `dc-widget-<name>`) in one dir under `web/static/widgets/`; auto-registered by directory scan (no central registry edit). Element tag is `dc-widget-` + name with `_`→`-` (`progress_tracker` → `dc-widget-progress-tracker`).
- Widget renders into **light DOM** (`createRenderRoot() { return this; }`) so global `styles/widgets.css` applies. Widget CSS lives in `src/decafclaw/web/static/styles/widgets.css` (imported by `style.css`).
- Widget status enum (5 values): `pending | in_progress | done | failed | skipped`.
- `ToolResult(text="[error: ...]")` for tool errors, never bare strings/raises.
- **Auto-emit is fail-open:** wrap every `set_sticky`/`clear_sticky` call in `try/except Exception: log.warning(...)`; the checklist/project tool returns its normal result regardless.
- Skills use **absolute imports** (`from decafclaw...`). Do not import private helpers across modules — replicate the 3-line `_emit_for_ctx` locally where needed.
  - **Superseded by [#657](https://github.com/lmorchard/decafclaw/issues/657) (2026-07-29).** The "replicate locally" instruction was a deliberate scope limit for *this* session, and it left four identical copies behind. The helper is now public and shared as `emit_for_ctx` in `src/decafclaw/events.py`; import it rather than replicating it. The absolute-imports-in-skills rule still stands, and is exactly why the shared version is imported as `from decafclaw.events import emit_for_ctx` in `skills/project/tools.py`.
- `execute_tool` auto-detects sync vs async, so making the checklist tools async is runtime-safe.
- Sticky emit in non-web (terminal/Mattermost) conversations is a harmless no-op (web-only surface; sidecar write is cheap).
- Commit after each task. Run `make check` + `make test` green before the PR.

---

### Task 1: The `progress_tracker` widget

Create the widget (meta + Lit element + CSS) and prove it registers and validates a 5-status payload. This is the reusable half; it renders in inline/canvas/sticky and can be pinned manually via the existing `widget_pin_sticky` tool.

**Files:**
- Create: `src/decafclaw/web/static/widgets/progress_tracker/widget.json`
- Create: `src/decafclaw/web/static/widgets/progress_tracker/widget.js`
- Modify: `src/decafclaw/web/static/styles/widgets.css` (append)
- Test: `tests/test_widgets.py` (append)

**Interfaces:**
- Produces: a bundled widget named `progress_tracker`, modes `["inline","canvas","sticky"]`, `accepts_input:false`, whose `data_schema` requires `steps[]` of `{label, status∈{pending,in_progress,done,failed,skipped}, note?}` and allows `title?`, `summary?`. Custom element `dc-widget-progress-tracker` with a `.data` property.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_widgets.py` (uses the existing `fake_config` fixture + bundled-scan pattern, mirroring `test_bundled_markdown_document_is_registered`):
```python
def test_bundled_progress_tracker_is_registered(fake_config):
    """Fresh registry scan finds the bundled progress_tracker widget."""
    reg = load_widget_registry(fake_config,
                               admin_dir=Path("/nonexistent/admin"))
    desc = reg.get("progress_tracker")
    assert desc is not None
    assert desc.tier == "bundled"
    assert desc.accepts_input is False
    assert set(desc.modes) == {"inline", "canvas", "sticky"}


def test_progress_tracker_validates_all_statuses(fake_config):
    """The schema accepts every status value and rejects an unknown one."""
    reg = load_widget_registry(fake_config,
                               admin_dir=Path("/nonexistent/admin"))
    ok, err = reg.validate("progress_tracker", {
        "title": "Work",
        "summary": "1/5",
        "steps": [
            {"label": "a", "status": "done", "note": "n"},
            {"label": "b", "status": "in_progress"},
            {"label": "c", "status": "pending"},
            {"label": "d", "status": "failed"},
            {"label": "e", "status": "skipped"},
        ],
    })
    assert ok, err
    bad_ok, _ = reg.validate("progress_tracker", {
        "steps": [{"label": "x", "status": "bogus"}],
    })
    assert bad_ok is False
    missing_ok, _ = reg.validate("progress_tracker", {"steps": [{"label": "x"}]})
    assert missing_ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_widgets.py::test_bundled_progress_tracker_is_registered tests/test_widgets.py::test_progress_tracker_validates_all_statuses -v`
Expected: FAIL — `reg.get("progress_tracker")` is `None` (widget dir doesn't exist yet).

- [ ] **Step 3: Create `widget.json`**

Create `src/decafclaw/web/static/widgets/progress_tracker/widget.json`:
```json
{
  "name": "progress_tracker",
  "description": "A quiet multi-step status list (pending / in_progress / done / failed / skipped). Shows in-flight progress of a task at a glance. Display-only, snapshot-rendered. Auto-emitted by the checklist tool and the project skill; can also be pinned manually.",
  "modes": ["inline", "canvas", "sticky"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "required": ["steps"],
    "properties": {
      "steps": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["label", "status"],
          "properties": {
            "label": { "type": "string" },
            "status": {
              "type": "string",
              "enum": ["pending", "in_progress", "done", "failed", "skipped"]
            },
            "note": { "type": "string" }
          }
        }
      },
      "title": { "type": "string" },
      "summary": { "type": "string" }
    }
  }
}
```

- [ ] **Step 4: Create `widget.js`**

Create `src/decafclaw/web/static/widgets/progress_tracker/widget.js`:
```javascript
import { LitElement, html, nothing } from 'lit';

/**
 * Progress tracker widget. Props:
 *   data = { steps: [{label, status, note?}], title?: string, summary?: string }
 * Display-only, snapshot-rendered — each update replaces the full step list.
 * Status ∈ pending | in_progress | done | failed | skipped.
 */
const _GLYPHS = {
  pending: '○',
  in_progress: '◐',
  done: '●',
  failed: '✗',
  skipped: '⊘',
};

export class ProgressTrackerWidget extends LitElement {
  static properties = {
    data: { type: Object },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {{steps: {label: string, status: string, note?: string}[], title?: string, summary?: string}|null} */
    this.data = null;
  }

  render() {
    const d = this.data;
    if (!d || !Array.isArray(d.steps)) {
      return html`<div class="progress-tracker progress-tracker--empty"><em>no steps</em></div>`;
    }
    return html`
      <div class="progress-tracker">
        ${d.title ? html`<div class="progress-tracker__title">${d.title}</div>` : nothing}
        <ul class="progress-tracker__list">
          ${d.steps.map((s) => {
            const status = s && typeof s.status === 'string' ? s.status : 'pending';
            const glyph = _GLYPHS[status] || _GLYPHS.pending;
            return html`
              <li class="progress-tracker__item progress-tracker__item--${status}">
                <span class="progress-tracker__glyph" aria-hidden="true">${glyph}</span>
                <span class="progress-tracker__label"
                  >${s?.label ?? ''}${s?.note
                    ? html`<span class="progress-tracker__note"> — ${s.note}</span>`
                    : nothing}</span>
              </li>
            `;
          })}
        </ul>
      </div>
    `;
  }
}

customElements.define('dc-widget-progress-tracker', ProgressTrackerWidget);
```

- [ ] **Step 5: Append CSS**

Append to `src/decafclaw/web/static/styles/widgets.css`:
```css
/* progress_tracker — quiet multi-step status list */
.progress-tracker { font-size: 0.85rem; }
.progress-tracker__title {
  font-weight: 600;
  font-size: 0.9rem;
  margin: 0 0 0.35rem;
}
.progress-tracker__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.progress-tracker__item {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}
.progress-tracker__glyph { flex: 0 0 1rem; text-align: center; }
.progress-tracker__label { flex: 1; min-width: 0; }
.progress-tracker__note { color: var(--pico-muted-color); font-size: 0.8rem; }
.progress-tracker__item--pending { color: var(--pico-muted-color); }
.progress-tracker__item--pending .progress-tracker__glyph { color: var(--pico-muted-color); }
.progress-tracker__item--in_progress .progress-tracker__glyph { color: var(--pico-primary); }
.progress-tracker__item--done .progress-tracker__glyph { color: var(--pico-ins-color, #2e7d32); }
.progress-tracker__item--failed .progress-tracker__glyph { color: var(--pico-del-color, #c62828); }
.progress-tracker__item--skipped { color: var(--pico-muted-color); }
.progress-tracker__item--skipped .progress-tracker__label { text-decoration: line-through; }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_widgets.py -v -k progress_tracker`
Expected: PASS (2 tests).

- [ ] **Step 7: Verify JS typechecks**

Run: `make check-js`
Expected: PASS (tsc --checkJs clean). No new vendored imports, so no `make vendor` needed.

- [ ] **Step 8: Commit**

```bash
git add src/decafclaw/web/static/widgets/progress_tracker/ src/decafclaw/web/static/styles/widgets.css tests/test_widgets.py
git commit -m "feat(414): progress_tracker widget (meta + Lit element + styles)"
```

---

### Task 2: Checklist auto-emit

Make the three checklist tools async and emit a mapped `progress_tracker` into the sticky slot on create/step_done, clearing it when all steps are done or the checklist is aborted. Fail-open throughout.

**Files:**
- Modify: `src/decafclaw/tools/checklist_tools.py`
- Test: `tests/test_checklist_tools.py` (rewrite calls to async)

**Interfaces:**
- Consumes: `decafclaw.sticky.set_sticky(config, conv_id, widget_type, data, emit=None)` / `clear_sticky(config, conv_id, emit=None)`; `checklist.checklist_status(config, conv_id) -> list[{text,done,note}]`.
- Produces: `tool_checklist_create` / `tool_checklist_step_done` / `tool_checklist_abort` are now **async**; `_progress_data_from_checklist(items: list[dict]) -> dict` (progress_tracker payload).

- [ ] **Step 1: Verify no other caller invokes these tools synchronously**

Run: `grep -rn "tool_checklist_create\|tool_checklist_step_done\|tool_checklist_abort" src/ tests/`
Expected: only `checklist_tools.py` (definition + registry) and `tests/test_checklist_tools.py`. `test_tool_registry.py` / `test_search_tools.py` reference the string name `"checklist_create"` only (synthetic defs), not the function — confirm they do NOT appear. If any other real caller exists, add it to this task's scope (await it).

- [ ] **Step 2: Write the failing tests**

Rewrite `tests/test_checklist_tools.py`. Add `import pytest` and the mapping/wiring tests; convert every tool call to `await` and mark tests `async`. Full file:
```python
"""Tests for checklist tools."""

from unittest.mock import AsyncMock

import pytest

from decafclaw.media import ToolResult
from decafclaw.tools.checklist_tools import (
    _progress_data_from_checklist,
    tool_checklist_abort,
    tool_checklist_create,
    tool_checklist_status,
    tool_checklist_step_done,
)


# --- pure mapping ---------------------------------------------------------

def test_progress_data_maps_first_unchecked_to_in_progress():
    items = [
        {"text": "A", "done": True, "note": "did a"},
        {"text": "B", "done": False, "note": ""},
        {"text": "C", "done": False, "note": ""},
    ]
    data = _progress_data_from_checklist(items)
    assert [s["status"] for s in data["steps"]] == ["done", "in_progress", "pending"]
    assert data["steps"][0]["note"] == "did a"
    assert data["title"] == "Checklist"
    assert data["summary"] == "1/3 · B"


def test_progress_data_all_done_summary_has_no_current():
    items = [{"text": "A", "done": True, "note": ""}]
    data = _progress_data_from_checklist(items)
    assert data["steps"][0]["status"] == "done"
    assert data["summary"] == "1/1"


# --- existing behavior (now async) ---------------------------------------

@pytest.mark.asyncio
async def test_checklist_create(ctx):
    result = await tool_checklist_create(ctx, steps=["Step A", "Step B", "Step C"])
    assert isinstance(result, ToolResult)
    assert "3 steps" in result.text
    assert "Step A" in result.text


@pytest.mark.asyncio
async def test_checklist_create_empty(ctx):
    result = await tool_checklist_create(ctx, steps=[])
    assert "error" in result.text


@pytest.mark.asyncio
async def test_checklist_step_done_advances(ctx):
    await tool_checklist_create(ctx, steps=["First", "Second", "Third"])
    result = await tool_checklist_step_done(ctx, note="done with first")
    assert result.end_turn is False
    assert "Second" in result.text


@pytest.mark.asyncio
async def test_checklist_step_done_all_complete(ctx):
    await tool_checklist_create(ctx, steps=["Only step"])
    result = await tool_checklist_step_done(ctx)
    assert result.end_turn is True
    assert "complete" in result.text.lower()


@pytest.mark.asyncio
async def test_checklist_step_done_no_checklist(ctx):
    result = await tool_checklist_step_done(ctx)
    assert "error" in result.text.lower() or "no active" in result.text.lower()


@pytest.mark.asyncio
async def test_checklist_abort(ctx):
    await tool_checklist_create(ctx, steps=["Step 1", "Step 2"])
    result = await tool_checklist_abort(ctx, reason="changed my mind")
    assert "aborted" in result.text.lower()
    assert "changed my mind" in result.text
    status = await tool_checklist_status(ctx)
    assert "No active" in status.text


@pytest.mark.asyncio
async def test_checklist_abort_empty(ctx):
    result = await tool_checklist_abort(ctx)
    assert "No active" in result.text


@pytest.mark.asyncio
async def test_checklist_status(ctx):
    await tool_checklist_create(ctx, steps=["A", "B", "C"])
    await tool_checklist_step_done(ctx)
    status = await tool_checklist_status(ctx)
    assert "[x]" in status.text
    assert "[ ]" in status.text
    assert "current" in status.text
    assert "1/3 complete" in status.text


@pytest.mark.asyncio
async def test_checklist_status_empty(ctx):
    result = await tool_checklist_status(ctx)
    assert "No active" in result.text


# --- sticky auto-emit wiring (monkeypatch sticky funcs) -------------------

@pytest.mark.asyncio
async def test_create_emits_set_sticky(ctx, monkeypatch):
    set_mock = AsyncMock()
    clear_mock = AsyncMock()
    monkeypatch.setattr("decafclaw.sticky.set_sticky", set_mock)
    monkeypatch.setattr("decafclaw.sticky.clear_sticky", clear_mock)
    await tool_checklist_create(ctx, steps=["A", "B"])
    assert set_mock.await_count == 1
    args, kwargs = set_mock.await_args
    # (config, conv_id, widget_type, data)
    assert args[2] == "progress_tracker"
    assert args[3]["steps"][0]["status"] == "in_progress"
    clear_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_step_done_clears_sticky(ctx, monkeypatch):
    clear_mock = AsyncMock()
    monkeypatch.setattr("decafclaw.sticky.set_sticky", AsyncMock())
    monkeypatch.setattr("decafclaw.sticky.clear_sticky", clear_mock)
    await tool_checklist_create(ctx, steps=["only"])
    await tool_checklist_step_done(ctx)
    assert clear_mock.await_count >= 1


@pytest.mark.asyncio
async def test_abort_clears_sticky(ctx, monkeypatch):
    clear_mock = AsyncMock()
    monkeypatch.setattr("decafclaw.sticky.set_sticky", AsyncMock())
    monkeypatch.setattr("decafclaw.sticky.clear_sticky", clear_mock)
    await tool_checklist_create(ctx, steps=["A", "B"])
    await tool_checklist_abort(ctx)
    assert clear_mock.await_count >= 1


@pytest.mark.asyncio
async def test_sticky_failure_is_fail_open(ctx, monkeypatch):
    monkeypatch.setattr("decafclaw.sticky.set_sticky",
                        AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr("decafclaw.sticky.clear_sticky",
                        AsyncMock(side_effect=RuntimeError("boom")))
    # Must not raise; checklist still works.
    result = await tool_checklist_create(ctx, steps=["A"])
    assert "1 step" in result.text or "1 steps" in result.text
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_checklist_tools.py -v`
Expected: FAIL — `_progress_data_from_checklist` import error / tools not awaitable.

- [ ] **Step 4: Implement**

Rewrite `src/decafclaw/tools/checklist_tools.py`:
```python
"""Checklist tools — mechanical step-by-step execution loop.

Always-loaded tools that drive the agent through a checklist one step
at a time. The agent iterates within a single turn: do step → call
step_done → get next step → do next step. ``end_turn=True`` is only
set when all steps are complete (agent summarizes and stops).

As a side effect, each mutation mirrors the checklist into the sticky
slot as a ``progress_tracker`` widget so the user sees live progress.
The mirror is fail-open — a sticky failure never breaks the checklist.
"""

import logging

from .. import checklist
from .. import sticky as sticky_mod
from ..media import ToolResult

log = logging.getLogger(__name__)


def _emit_for_ctx(ctx):
    manager = getattr(ctx, "manager", None)
    if manager is None:
        return None
    return manager.emit


def _progress_data_from_checklist(items: list[dict]) -> dict:
    """Map checklist items {text,done,note} → progress_tracker data.

    Completed items → 'done'; the first not-done item → 'in_progress';
    remaining not-done items → 'pending'.
    """
    steps = []
    first_pending = True
    done_count = 0
    current_label = ""
    for item in items:
        if item["done"]:
            status = "done"
            done_count += 1
        elif first_pending:
            status = "in_progress"
            first_pending = False
            current_label = item["text"]
        else:
            status = "pending"
        step = {"label": item["text"], "status": status}
        if item.get("note"):
            step["note"] = item["note"]
        steps.append(step)
    total = len(items)
    summary = f"{done_count}/{total} · {current_label}" if current_label \
        else f"{done_count}/{total}"
    return {"steps": steps, "title": "Checklist", "summary": summary}


async def _mirror_to_sticky(ctx, conv_id: str) -> None:
    """Sync the sticky slot with current checklist state. Fail-open.

    Clears the slot when there is no active checklist or every step is
    done; otherwise pins a progress_tracker snapshot.
    """
    try:
        items = checklist.checklist_status(ctx.config, conv_id)
        if not items or all(i["done"] for i in items):
            await sticky_mod.clear_sticky(
                ctx.config, conv_id, emit=_emit_for_ctx(ctx))
            return
        data = _progress_data_from_checklist(items)
        await sticky_mod.set_sticky(
            ctx.config, conv_id, "progress_tracker", data,
            emit=_emit_for_ctx(ctx))
    except Exception:
        log.warning("checklist sticky mirror failed for %s", conv_id,
                    exc_info=True)


async def tool_checklist_create(ctx, steps: list[str]) -> ToolResult:
    """Create a checklist and return the first step."""
    conv_id = ctx.conv_id or "default"
    if not steps:
        return ToolResult(text="[error: steps list is empty]")
    items = checklist.checklist_create(ctx.config, conv_id, steps)
    await _mirror_to_sticky(ctx, conv_id)
    first = items[0]["text"]
    return ToolResult(
        text=f"Checklist created ({len(items)} steps). "
             f"Do step 1 now: {first}\n\n"
             f"When done, call checklist_step_done.",
    )


async def tool_checklist_step_done(ctx, note: str = "") -> ToolResult:
    """Mark current step done and advance. end_turn=True only when all complete."""
    conv_id = ctx.conv_id or "default"
    next_item = checklist.checklist_complete_current(ctx.config, conv_id, note)
    await _mirror_to_sticky(ctx, conv_id)
    if next_item is None:
        items = checklist.checklist_status(ctx.config, conv_id)
        if not items:
            return ToolResult(text="[error: no active checklist]")
        done = sum(1 for i in items if i["done"])
        return ToolResult(
            text=f"All {done} steps complete! Summarize what was accomplished.",
            end_turn=True,
        )
    return ToolResult(
        text=f"Step {next_item['index'] - 1}/{next_item['total']} done. "
             f"Do step {next_item['index']} now: {next_item['text']}\n\n"
             f"When done, call checklist_step_done.",
    )


async def tool_checklist_abort(ctx, reason: str = "") -> ToolResult:
    """Abandon the current checklist."""
    conv_id = ctx.conv_id or "default"
    items = checklist.checklist_status(ctx.config, conv_id)
    if not items:
        return ToolResult(text="No active checklist to abort.")
    done = sum(1 for i in items if i["done"])
    checklist.checklist_abort(ctx.config, conv_id)
    await _mirror_to_sticky(ctx, conv_id)
    msg = f"Checklist aborted ({done}/{len(items)} steps were complete)."
    if reason:
        msg += f" Reason: {reason}"
    return ToolResult(text=msg)


def tool_checklist_status(ctx) -> ToolResult:
    """Show current checklist progress."""
    conv_id = ctx.conv_id or "default"
    items = checklist.checklist_status(ctx.config, conv_id)
    if not items:
        return ToolResult(text="No active checklist.")
    lines = []
    current_found = False
    for i, item in enumerate(items, 1):
        if item["done"]:
            note_suffix = f" — {item['note']}" if item.get("note") else ""
            lines.append(f"  {i}. [x] {item['text']}{note_suffix}")
        else:
            marker = " ← current" if not current_found else ""
            lines.append(f"  {i}. [ ] {item['text']}{marker}")
            if not current_found:
                current_found = True
    done = sum(1 for i in items if i["done"])
    lines.append(f"\n{done}/{len(items)} complete")
    return ToolResult(text="\n".join(lines))


CHECKLIST_TOOLS = {
    "checklist_create": tool_checklist_create,
    "checklist_step_done": tool_checklist_step_done,
    "checklist_abort": tool_checklist_abort,
    "checklist_status": tool_checklist_status,
}
```
(Keep the existing `CHECKLIST_TOOL_DEFINITIONS` list below unchanged — descriptions are not modified.) `tool_checklist_status` stays sync (it doesn't mutate; `execute_tool` handles the mix).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_checklist_tools.py -v`
Expected: PASS (all).

- [ ] **Step 6: Confirm no wider breakage**

Run: `uv run pytest tests/test_tool_registry.py tests/test_search_tools.py -q`
Expected: PASS (they reference the tool name only).

- [ ] **Step 7: Commit**

```bash
git add src/decafclaw/tools/checklist_tools.py tests/test_checklist_tools.py
git commit -m "feat(414): checklist auto-emits progress_tracker to sticky slot"
```

---

### Task 3: Project-skill auto-emit (EXECUTING phase)

Emit a `progress_tracker` to the sticky slot while a project is EXECUTING, on `project_next_task` / `project_update_step` / `project_add_steps`, and clear on transition to DONE. Fail-open.

**Files:**
- Modify: `src/decafclaw/skills/project/tools.py`
- Test: `tests/test_project_tools.py` (add `conv_id` to fixture; add emit tests)

**Interfaces:**
- Consumes: `decafclaw.sticky.set_sticky`/`clear_sticky`; `plan_parser.parse_plan`, `plan_progress`, `next_actionable`; `Step` fields `number/description/status/note/children`.
- Produces: `_progress_data_from_plan(info, steps) -> dict`; internal `_emit_project_progress(ctx, info)` / `_clear_project_progress(ctx)` helpers, wired into the four tools above.

- [ ] **Step 1: Write the failing tests**

First, extend the `ctx` fixture in `tests/test_project_tools.py` to carry `conv_id` and (implicitly) no `manager`:
```python
@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(workspace_path=workspace)
    tools = SimpleNamespace(
        preapproved={"project_next_task", "project_advance"},
        current_call_id=None,
    )
    skills = SimpleNamespace(data={})
    return SimpleNamespace(config=config, tools=tools, skills=skills,
                           conv_id="proj-conv", manager=None)
```

Then append a new test class (uses the existing `_advance_to_executing` helper + `AsyncMock` monkeypatch, so no widget registry / real sidecar needed):
```python
class TestProgressTrackerEmit:
    @pytest.mark.asyncio
    async def test_update_step_emits_during_executing(self, ctx, monkeypatch):
        from unittest.mock import AsyncMock
        set_mock = AsyncMock()
        monkeypatch.setattr("decafclaw.sticky.set_sticky", set_mock)
        monkeypatch.setattr("decafclaw.sticky.clear_sticky", AsyncMock())
        await _advance_to_executing(ctx, slug="pt-step")
        set_mock.reset_mock()
        await tool_project_update_step(ctx, step="1.1", status="done", note="ok")
        assert set_mock.await_count >= 1
        args, _ = set_mock.await_args
        assert args[2] == "progress_tracker"
        labels = [s["label"] for s in args[3]["steps"]]
        assert any(lbl.startswith("1.1.") for lbl in labels)

    @pytest.mark.asyncio
    async def test_done_clears_sticky(self, ctx, monkeypatch):
        from unittest.mock import AsyncMock
        clear_mock = AsyncMock()
        monkeypatch.setattr("decafclaw.sticky.set_sticky", AsyncMock())
        monkeypatch.setattr("decafclaw.sticky.clear_sticky", clear_mock)
        plan = "# Plan\n\n## Steps\n\n- [ ] 1. Only step\n"
        await _advance_to_executing(ctx, slug="pt-done", plan=plan)
        await tool_project_update_step(ctx, step="1", status="done")
        result = await tool_project_task_done(ctx)
        assert _text(result) == "Project complete!" or "complete" in _text(result).lower()
        assert clear_mock.await_count >= 1

    @pytest.mark.asyncio
    async def test_no_emit_outside_executing(self, ctx, monkeypatch):
        from unittest.mock import AsyncMock
        set_mock = AsyncMock()
        monkeypatch.setattr("decafclaw.sticky.set_sticky", set_mock)
        monkeypatch.setattr("decafclaw.sticky.clear_sticky", AsyncMock())
        await tool_project_create(ctx, description="planning phase", slug="pt-plan")
        # BRAINSTORMING phase: next_task must not pin a tracker.
        await tool_project_next_task(ctx)
        set_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_emit_failure_is_fail_open(self, ctx, monkeypatch):
        from unittest.mock import AsyncMock
        monkeypatch.setattr("decafclaw.sticky.set_sticky",
                            AsyncMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr("decafclaw.sticky.clear_sticky",
                            AsyncMock(side_effect=RuntimeError("boom")))
        await _advance_to_executing(ctx, slug="pt-failopen")
        # Must not raise.
        result = await tool_project_update_step(ctx, step="1.1", status="done")
        assert _text(result)
```
(`_advance_to_executing` uses the module `SAMPLE_PLAN`, whose first leaf step is `1.1`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit -v`
Expected: FAIL — no emit happens (helpers not implemented).

- [ ] **Step 3: Implement**

In `src/decafclaw/skills/project/tools.py`:

Add imports near the top (after the existing imports):
```python
import logging

from decafclaw import sticky as sticky_mod
from decafclaw.skills.project.plan_parser import Step

log = logging.getLogger(__name__)
```
(`parse_plan`, `plan_progress`, `next_actionable` are already imported.)

Add helpers in the "Helpers" section:
```python
def _emit_for_ctx(ctx):
    manager = getattr(ctx, "manager", None)
    if manager is None:
        return None
    return manager.emit


def _flatten_leaf_steps(steps: list[Step]) -> list[Step]:
    """Depth-first list of leaf steps (those without children)."""
    out: list[Step] = []
    for s in steps:
        if s.children:
            out.extend(_flatten_leaf_steps(s.children))
        else:
            out.append(s)
    return out


def _progress_data_from_plan(info: ProjectInfo, steps: list[Step]) -> dict:
    """Build a progress_tracker payload from parsed plan steps."""
    widget_steps = []
    for s in _flatten_leaf_steps(steps):
        step = {"label": f"{s.number}. {s.description}", "status": s.status}
        if s.note:
            step["note"] = s.note
        widget_steps.append(step)
    done, total = plan_progress(steps)
    nxt = next_actionable(steps)
    summary = f"{done}/{total} · {nxt.number}. {nxt.description}" if nxt \
        else f"{done}/{total}"
    return {"steps": widget_steps, "title": info.description, "summary": summary}


async def _emit_project_progress(ctx, info: ProjectInfo) -> None:
    """Mirror an EXECUTING project's plan into the sticky slot. Fail-open."""
    if info.status != ProjectState.EXECUTING:
        return
    try:
        content = info.plan_path.read_text() if info.plan_path.exists() else ""
        if not content.strip():
            return
        _, steps, _ = parse_plan(content)
        if not steps:
            return
        data = _progress_data_from_plan(info, steps)
        await sticky_mod.set_sticky(
            ctx.config, ctx.conv_id, "progress_tracker", data,
            emit=_emit_for_ctx(ctx))
    except Exception:
        log.warning("project sticky emit failed", exc_info=True)


async def _clear_project_progress(ctx) -> None:
    """Clear the sticky slot for a project. Fail-open."""
    try:
        await sticky_mod.clear_sticky(
            ctx.config, ctx.conv_id, emit=_emit_for_ctx(ctx))
    except Exception:
        log.warning("project sticky clear failed", exc_info=True)
```

Wire the emit calls:

In `tool_project_next_task`, in the `EXECUTING` branch, emit before returning:
```python
    elif info.status == ProjectState.EXECUTING:
        await _emit_project_progress(ctx, info)
        return _next_execution_step(info)
```

In `tool_project_update_step`, after `save_project(info)` and before building `msg`:
```python
    info.plan_path.write_text(render_plan(overview, steps, tail))
    save_project(info)
    await _emit_project_progress(ctx, info)
```

In `tool_project_add_steps`, after `save_project(info)`:
```python
    info.plan_path.write_text(render_plan(overview, plan_steps, tail))
    save_project(info)
    await _emit_project_progress(ctx, info)
```

In `tool_project_task_done`, in the `EXECUTING` branch, after setting DONE:
```python
        info.status = ProjectState.DONE
        save_project(info)
        await _clear_project_progress(ctx)
        return ToolResult(text="Project complete!", end_turn=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_project_tools.py -v`
Expected: PASS (existing + new `TestProgressTrackerEmit`). Existing tests are unaffected — with no widget registry the real `set_sticky` would return ok=False, but the emit is fail-open and these tests either monkeypatch it or don't assert on it.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/skills/project/tools.py tests/test_project_tools.py
git commit -m "feat(414): project skill auto-emits progress_tracker during executing"
```

---

### Task 4: Docs, CLAUDE.md, full check, manual QA

**Files:**
- Modify: `docs/widgets.md` (progress_tracker entry) and/or the widget-surface doc that documents the sticky slot (grep for the #419 "Sticky slot" section).
- Modify: `docs/tools.md` / project + checklist docs — note the auto-emit side effect (grep for where checklist/project are documented).
- Modify: `CLAUDE.md` — add the widget to the widgets list; note the checklist/project auto-emit side effect.
- Modify: session `notes.md` (final summary + QA results).

- [ ] **Step 1: Locate the docs to update**

Run: `grep -rln "sticky slot\|Sticky slot\|progress_tracker\|checklist" docs/ | sort -u`
Update: the widget catalog / sticky-surface doc with a `progress_tracker` entry (5 statuses, display-only, snapshot, modes inline/canvas/sticky); the checklist doc and project-skill doc with a one-paragraph "auto-emits a progress_tracker to the sticky slot" note.

- [ ] **Step 2: Update `CLAUDE.md`**

In the bundled-widgets / key-files area, add `web/static/widgets/progress_tracker/`, and add a one-line note under the checklist and project entries that they auto-emit a `progress_tracker` into the sticky slot (fail-open, EXECUTING-only for project).

- [ ] **Step 3: Full check + test**

Run: `make check`
Expected: PASS (lint + typecheck + check-js + check-message-types — no message-type changes this session, so the drift check is a no-op pass).
Run: `make test`
Expected: PASS (full suite).

- [ ] **Step 4: Manual web QA**

Per the `reference_ws_smoke_local_run` memory, start a web-only server on the worktree's `HTTP_PORT` (`MATTERMOST_ENABLED=false`), mint a login token (`uv run decafclaw-token create <user>`), open the UI, and:
- Pin manually: `widget_pin_sticky(widget_type="progress_tracker", data={"title":"Demo","summary":"1/3 · B","steps":[{"label":"A","status":"done"},{"label":"B","status":"in_progress"},{"label":"C","status":"pending"},{"label":"D","status":"failed"},{"label":"E","status":"skipped"}]})` → quiet list renders with distinct glyphs; collapsed line shows the summary.
- Run a checklist (`checklist_create` with 3+ steps, then `checklist_step_done` a few times) → the sticky slot tracks live; the last `checklist_step_done` clears it; `checklist_abort` clears it.
- Reload mid-checklist → slot persists (sticky REST recovery from #419).
- Drive a project into EXECUTING and `project_update_step` → slot tracks; complete → clears.
- Tune the widget look live with Les (glyphs, spacing, color). Record results + revoke the token.

- [ ] **Step 5: Write `notes.md` and commit**

```bash
git add docs/ CLAUDE.md docs/dev-sessions/
git commit -m "docs(414): document progress_tracker widget + auto-emit; QA notes"
```

---

## Self-Review

**Spec coverage** — every spec section maps to a task:
- Widget (`widget.json`+`widget.js`, 5 statuses, 3 modes, display-only, snapshot) → Task 1. Widget CSS → Task 1.
- Checklist auto-emit (async tools, mapping, set/clear on create/step_done/abort, fail-open) → Task 2.
- Project auto-emit (EXECUTING-only, `project_next_task`/update_step/add_steps set, DONE clear, skipped passthrough, fail-open) → Task 3.
- No new tools / no evals → honored (no tool-def edits; no `evals/` changes).
- Tests (widget registration+validation, checklist mapping+wiring+fail-open, project emit+clear+outside-executing+fail-open) → Tasks 1–3. Docs + CLAUDE.md + `make check`/`make test` + manual QA → Task 4.
- Out-of-scope (delegate/scheduled occupants, canvas promotion, patching, config toggle) correctly absent.

**Placeholder scan** — no TBD/TODO. The two "grep for the doc/caller" steps (Task 2 Step 1, Task 4 Step 1) are verification/location steps with explicit expected outputs, not code gaps.

**Type consistency** — `_progress_data_from_checklist(items: list[dict]) -> dict` and `_progress_data_from_plan(info, steps) -> dict` both return `{steps:[{label,status,note?}], title, summary}` matching the widget `data_schema` from Task 1. `set_sticky(config, conv_id, "progress_tracker", data, emit=...)` argument order is consistent across Tasks 2–3 and matches the existing `sticky.set_sticky` signature (verified in `sticky_tools.py`). Monkeypatch target `decafclaw.sticky.set_sticky` matches the `from .. import sticky as sticky_mod` / `from decafclaw import sticky as sticky_mod` binding in both callers. `Step` field access (`number`, `description`, `status`, `note`, `children`) matches `plan_parser.Step`.
