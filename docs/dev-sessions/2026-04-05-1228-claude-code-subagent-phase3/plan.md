# Claude Code Subagent Phase 3 — Plan

## Overview

Two steps. This is a focused change — all logic is in the streaming loop of `tool_claude_code_send`.

---

## Step 1: Enrich the streaming loop with counts, cost, budget warnings, and error snippets

**What:** Add per-send state tracking and richer progress events to the SDK message streaming loop. Extract budget warning logic into a testable helper.

**Files:**
- `src/decafclaw/skills/claude_code/tools.py` — modify streaming loop, add budget warning helper

**Details:**

1. Add a helper function for budget warning checks:
   ```python
   _BUDGET_THRESHOLDS = [0.5, 0.75, 0.9]

   def _check_budget_warnings(cost: float, budget: float, fired: set) -> list[str]:
       """Return list of budget warning messages for newly crossed thresholds."""
       warnings = []
       for threshold in _BUDGET_THRESHOLDS:
           if threshold not in fired and budget > 0 and cost >= budget * threshold:
               pct = int(threshold * 100)
               warnings.append(
                   f"Budget warning: {pct}% used (${cost:.2f} of ${budget:.2f})"
               )
               fired.add(threshold)
       return warnings
   ```

2. Before the streaming loop (after `logger = SessionLogger(...)`, around line 444), initialize per-send state:
   ```python
   tool_call_count = 0
   tool_id_to_name: dict[str, str] = {}
   warnings_fired: set[float] = set()
   ```

3. In the `AssistantMessage` branch, for each `ToolUseBlock`:
   - Increment `tool_call_count`
   - Record `tool_id_to_name[block.id] = block.name`
   - Publish: `f"Tool call {tool_call_count}: Using {block.name}..."`

4. Add a `UserMessage` branch (currently not handled in the loop). Import `UserMessage` and `ToolResultBlock` (already imported in output.py but need to check tools.py imports). For each `ToolResultBlock` with `is_error`:
   - Look up tool name via `tool_id_to_name.get(block.tool_use_id, "unknown tool")`
   - Get error snippet: first 100 chars of content
   - Publish: `f"{tool_name} failed — {snippet}"`

5. In the `ResultMessage` branch, after updating cost:
   - Publish cost: `f"Session cost: ${session.total_cost_usd:.2f} of ${session.budget_usd:.2f} budget"`
   - Check budget warnings: `for warning in _check_budget_warnings(session.total_cost_usd, session.budget_usd, warnings_fired): await ctx.publish("tool_status", tool="claude_code", message=warning)`

6. Write tests:
   - `test_check_budget_warnings_fires_at_thresholds`: verify 50/75/90 fire at correct points
   - `test_check_budget_warnings_no_duplicates`: verify same threshold doesn't fire twice
   - `test_check_budget_warnings_skips_crossed`: verify jumping from 0% to 80% fires both 50% and 75%
   - `test_check_budget_warnings_zero_budget`: verify no warnings when budget is 0

**Prompt:**

> In `src/decafclaw/skills/claude_code/tools.py`:
>
> 1. Add imports if not present: `UserMessage` and `ToolResultBlock` from `claude_code_sdk`.
>
> 2. Add a module-level constant and helper after `_assemble_prompt`:
>    ```python
>    _BUDGET_THRESHOLDS = [0.5, 0.75, 0.9]
>
>    def _check_budget_warnings(cost: float, budget: float, fired: set) -> list[str]:
>        """Return list of budget warning messages for newly crossed thresholds."""
>        warnings = []
>        for threshold in _BUDGET_THRESHOLDS:
>            if threshold not in fired and budget > 0 and cost >= budget * threshold:
>                pct = int(threshold * 100)
>                warnings.append(
>                    f"Budget warning: {pct}% used (${cost:.2f} of ${budget:.2f})"
>                )
>                fired.add(threshold)
>        return warnings
>    ```
>
> 3. Before the streaming try block (after `logger = SessionLogger(...)`, around line 444), add:
>    ```python
>    tool_call_count = 0
>    tool_id_to_name: dict[str, str] = {}
>    warnings_fired: set[float] = set()
>    ```
>
> 4. Replace the current `AssistantMessage` ToolUseBlock handling:
>    ```python
>    # Old:
>    elif isinstance(block, ToolUseBlock):
>        await ctx.publish("tool_status", tool="claude_code",
>                          message=f"Using {block.name}...")
>
>    # New:
>    elif isinstance(block, ToolUseBlock):
>        tool_call_count += 1
>        tool_id_to_name[block.id] = block.name
>        await ctx.publish(
>            "tool_status", tool="claude_code",
>            message=f"Tool call {tool_call_count}: Using {block.name}..."
>        )
>    ```
>
> 5. Add a `UserMessage` branch after the `AssistantMessage` block (before `elif isinstance(message, ResultMessage)`):
>    ```python
>    elif isinstance(message, UserMessage):
>        for block in getattr(message, "content", []):
>            if isinstance(block, ToolResultBlock) and block.is_error:
>                tool_name = tool_id_to_name.get(block.tool_use_id, "unknown tool")
>                snippet = (block.content if isinstance(block.content, str)
>                           else str(block.content))[:100]
>                await ctx.publish(
>                    "tool_status", tool="claude_code",
>                    message=f"{tool_name} failed — {snippet}"
>                )
>    ```
>
> 6. In the `ResultMessage` branch, after the cost update (`session.total_cost_usd = message.total_cost_usd`), add:
>    ```python
>    # Publish cost progress
>    await ctx.publish(
>        "tool_status", tool="claude_code",
>        message=f"Session cost: ${session.total_cost_usd:.2f} of ${session.budget_usd:.2f} budget"
>    )
>    # Check budget warnings
>    for warning in _check_budget_warnings(
>        session.total_cost_usd, session.budget_usd, warnings_fired
>    ):
>        await ctx.publish("tool_status", tool="claude_code", message=warning)
>    ```
>
> 7. Write tests in `tests/test_claude_code_progress.py`:
>    ```python
>    from decafclaw.skills.claude_code.tools import _check_budget_warnings
>
>    def test_fires_at_thresholds():
>        fired = set()
>        # At 50%
>        warnings = _check_budget_warnings(1.0, 2.0, fired)
>        assert len(warnings) == 1
>        assert "50%" in warnings[0]
>        # At 75%
>        warnings = _check_budget_warnings(1.5, 2.0, fired)
>        assert len(warnings) == 1
>        assert "75%" in warnings[0]
>        # At 90%
>        warnings = _check_budget_warnings(1.8, 2.0, fired)
>        assert len(warnings) == 1
>        assert "90%" in warnings[0]
>
>    def test_no_duplicates():
>        fired = set()
>        _check_budget_warnings(1.0, 2.0, fired)
>        warnings = _check_budget_warnings(1.0, 2.0, fired)
>        assert len(warnings) == 0
>
>    def test_jumps_fire_multiple():
>        fired = set()
>        warnings = _check_budget_warnings(1.6, 2.0, fired)
>        assert len(warnings) == 2  # 50% and 75%
>        assert "50%" in warnings[0]
>        assert "75%" in warnings[1]
>
>    def test_zero_budget():
>        fired = set()
>        warnings = _check_budget_warnings(1.0, 0, fired)
>        assert len(warnings) == 0
>    ```
>
> Run `make lint` and `make test`.

---

## Step 2: Update SKILL.md + docs

**What:** Document the richer progress events in SKILL.md.

**Files:**
- `src/decafclaw/skills/claude_code/SKILL.md`

**Prompt:**

> Update `src/decafclaw/skills/claude_code/SKILL.md`:
>
> 1. Add a "Progress Reporting" section (after "Permission Model") documenting:
>    - Tool call count: each tool use is numbered ("Tool call 5: Using Edit...")
>    - Error snippets: tool failures are reported with the first 100 chars of error text
>    - Running cost: session cost updates published on each ResultMessage
>    - Budget warnings: published when cost crosses 50%, 75%, 90% of session budget
>    - All events use the `tool_status` event type
>
> 2. Note that cost is session-total (cumulative across sends), not per-send.
>
> Run `make check` one final time.
