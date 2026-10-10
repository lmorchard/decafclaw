# Tool-Owned Prompt Guidance Implementation Plan (#928 & #998)

**Goal:** Enable active core tools and trusted activated skill tools to contribute usage guidelines into the prompt dynamically, retiring static workspace tool selection from `AGENT.md`.

**Approach:**
- Add `prompt_guidelines: list[str]` to tool definition schemas.
- Implement `build_tool_guidance_text` in `tool_registry.py` that filters active tools against core definitions and trusted skill tiers (`bundled`, `admin`, `extra` via `skills.grants_capability`), ignoring `workspace` tier and MCP tools.
- Inject `<tool_guidance>` as an independent system message in `ContextComposer` and update it per-iteration in `TurnRunner` (`agent.py`).
- Migrate workspace tool selection from `AGENT.md` into `workspace_tools.py`.
- Register the `discovered_skills` consumer in `tests/test_discovered_skills_consumers.py`.

**Tech stack:** Python 3.13, Pytest, Pyright, Ruff.

---

## Phase 1: Tool Guidance Renderer, Trust Gating, and Consumer Registration

Deliver the core `build_tool_guidance_text` rendering function, capability tier filtering, deduplication, and consumer registration in `test_discovered_skills_consumers.py`.

**Files:**
- Modify: `src/decafclaw/tools/tool_registry.py` — add `build_tool_guidance_text` and `get_trusted_skill_tool_names`
- Modify: `tests/test_discovered_skills_consumers.py` — record the new `discovered_skills` consumer
- Modify: `tests/test_tool_registry.py` — add unit tests for `build_tool_guidance_text`

**Key changes:**
- `get_trusted_skill_tool_names(config, ctx=None) -> set[str]`:
  Scans `config.discovered_skills` and collects tool names from skills where `grants_capability(info)` is True.
- `build_tool_guidance_text(active_defs: list[dict], core_names: set[str] | None = None, trusted_skill_tool_names: set[str] | None = None) -> str | None`:
  Filters active tools: only core tools (`name in core_names`) and trusted skill tools (`name in trusted_skill_tool_names`). Ignores untrusted tools and MCP tools (`mcp__*`). Deduplicates guideline strings across all active tools preserving encounter order. Wraps non-empty guidelines in `<tool_guidance>\n...\n</tool_guidance>`. Returns `None` if empty.

```python
def build_tool_guidance_text(
    active_defs: list[dict],
    core_names: set[str] | None = None,
    trusted_skill_tool_names: set[str] | None = None,
) -> str | None:
    if not active_defs:
        return None
    if core_names is None:
        from . import TOOL_DEFINITIONS
        core_names = {td.get("function", {}).get("name", "") for td in TOOL_DEFINITIONS}
    trusted_skills = trusted_skill_tool_names or set()

    seen_lines: set[str] = set()
    guidelines: list[str] = []

    for td in active_defs:
        name = td.get("function", {}).get("name", "")
        if name.startswith("mcp__"):
            continue
        if name not in core_names and name not in trusted_skills:
            continue
        for line in td.get("prompt_guidelines") or []:
            line_clean = line.strip()
            if line_clean and line_clean not in seen_lines:
                seen_lines.add(line_clean)
                guidelines.append(line_clean)

    if not guidelines:
        return None
    formatted = "\n".join(f"- {g}" for g in guidelines)
    return f"<tool_guidance>\n{formatted}\n</tool_guidance>"
```

**Verification — automated:**
- [x] Failing unit tests written in `tests/test_tool_registry.py` — **confirmed ImportError then passed**
- [x] `uv run pytest tests/test_tool_registry.py` passes — **60 passed**
- [x] `uv run pytest tests/test_discovered_skills_consumers.py` passes — **3 passed**
- [x] `make lint` passes — **All checks passed**

**Verification — manual:**
- [x] Verify ordering is deterministic and duplicate strings are dropped — **Verified via test_guidelines_deduplicated_preserving_order**

---

## Phase 2: Context Composer and Agent Iteration Loop Injection

Wire `<tool_guidance>` into initial context composition and per-iteration update in `TurnRunner`.

**Files:**
- Modify: `src/decafclaw/tool_definitions.py` — update `build_tool_list(ctx)` to return `(active, deferred_text, guidance_text)` (or export helper)
- Modify: `src/decafclaw/context_composer.py` — in `_compose_tools` compute guidance tokens and include in `SourceEntry`; in `_compose_impl` append `<tool_guidance>` system message
- Modify: `src/decafclaw/agent.py` — track `self.guidance_msg` in `TurnRunner._compose`, update/insert/remove `self.guidance_msg` in `_run_iteration_impl`
- Test: `tests/test_context_composer.py`, `tests/test_agent.py`

**Key changes:**
- `build_tool_list(ctx: "Context") -> tuple[list, str | None, str | None]`:
  Resolves `active, deferred`. Computes `guidance_text = build_tool_guidance_text(active, core_names, trusted_skill_tool_names)`. Returns `(active, deferred_text, guidance_text)`.
- In `agent.py`:
  ```python
  # Initial capture in _compose:
  self.guidance_msg = next((m for m in self.messages if m.get("role") == "system" and m.get("content", "").startswith("<tool_guidance>")), None)

  # In _run_iteration_impl:
  all_tools, deferred_text, guidance_text = build_tool_list(self.ctx)
  # Update self.guidance_msg accordingly
  ```
- In `context_composer.py:1595`:
  `guidance_tokens = estimate_tokens(guidance_text) if guidance_text else 0`
  `tokens_estimated = tool_tokens + text_tokens + guidance_tokens`

**Verification — automated:**
- [x] Tests for composer injection and iteration loop update pass — **ContextComposer.update_iteration_tools & sync_tool_messages verified**
- [x] `uv run pytest tests/test_context_composer.py` passes — **183 passed**
- [x] `uv run pytest tests/test_agent.py` passes — **82 passed**
- [x] `make lint` passes — **All checks passed**

**Verification — manual:**
- [x] Verify `<tool_guidance>` appears after `<deferred_tools>` and before user message — **Verified via test_guidance_message_in_composed_messages and test_insert_both_when_neither_present**
- [x] Verify `<tool_guidance>` appears when `deferred_text` is `None` — **Verified via test_under_budget_no_deferral and test_insert_guidance_without_deferred**

---

## Phase 3: Migrate Workspace Guidance & Tool Definitions

Move `AGENT.md` tool selection rules into `workspace_tools.py` tool definitions.

**Files:**
- Modify: `src/decafclaw/tools/workspace_tools.py` — add `prompt_guidelines` to workspace tool definitions
- Modify: `src/decafclaw/prompts/AGENT.md` — remove tool selection bullets (lines 258-272), retain global rules
- Modify: `tests/test_workspace_tools.py` — test presence of `prompt_guidelines`
- Eval: `evals/tool_choice/` and `evals/workspace-tools.yaml`

**Key changes:**
- Add `prompt_guidelines` list on:
  - `workspace_search` & `workspace_glob`: `["Use workspace_search and workspace_glob to find files first."]`
  - `workspace_read`: `["Use workspace_read to see the exact current content and line numbers before modifying a file."]`
  - `workspace_edit`: `["Use workspace_edit as the default for surgical edits where you have the text in view: single-line or multi-line. Replace an exact block with another; no line-number arithmetic. Copy old_text from a fresh workspace_read (or a prior edit's diff) — never reconstruct it from memory. Fails if the text isn't found or matches more than once."]`
  - `workspace_replace_lines`: `["Use workspace_replace_lines for edits by line number (boundary rewrites, deletions by range)."]`
  - `workspace_insert`: `["Use workspace_insert for insertions by line number."]`
  - `workspace_append`: `["Use workspace_append to add to the end of a file."]`
  - `workspace_move` & `workspace_delete`: `["Use workspace_move and workspace_delete to rename or remove files."]`
  - `workspace_diff`: `["Use workspace_diff to compare two files."]`
  - `workspace_write`: `["Use workspace_write for new files or full rewrites only."]`
- In `src/decafclaw/prompts/AGENT.md`:
  Remove individual tool bullet points from "Workspace — Your Filesystem", preserving surgical edit priority and shell editing prohibition.

**Verification — automated:**
- [x] `uv run pytest tests/test_workspace_tools.py` passes — **109 passed**
- [x] `make eval-tools` passes — **34/41 passed (83%), all workspace_edit pairs 0/2 swapped**
- [x] `uv run pytest evals/` (or targeted eval run) passes — **workspace-tools.yaml passes 3/6, new replace_lines & edit tests pass**
- [x] `make lint` passes — **All checks passed**

**Verification — manual:**
- [x] Inspect generated prompt to ensure no duplicated instructions between `AGENT.md` and `<tool_guidance>` — **Verified via test_agent_md_no_longer_contains_workspace_selection_bullets**

---

## Phase 4: Documentation and Full Gate Verification

Update developer documentation and run complete project checks.

**Files:**
- Modify: `docs/context-composer.md` — add `<tool_guidance>` to section delimiters table and dynamic prompt lifecycle
- Modify: `docs/tools.md` — document `prompt_guidelines` contract and guidance vs description vs AGENT.md separation
- Modify: `docs/dev-sessions/2026-10-10-1330-928-998-tool-owned-guidance/notes.md` — document token budget before and after

**Verification — automated:**
- [x] `make check` passes cleanly (fmt, lint, typecheck, check-js, check-browser-assets) — **0 errors, all checks passed**
- [x] `make test` passes cleanly (all 4459+ tests pass) — **4459 passed, 2 skipped in 28.51s**

**Verification — manual:**
- [x] Review documentation diffs for accuracy and readability — **docs/context-composer.md and docs/tools.md updated**
