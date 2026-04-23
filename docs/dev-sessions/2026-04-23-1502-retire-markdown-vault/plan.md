# Retire `markdown_vault` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold section-aware tools from `markdown_vault` into `vault`, retire `markdown_vault`, unify vault root around the user's Obsidian tree, and deliver a migration script. Closes #264.

**Architecture:** Port the `Section`/`Document` parser into `skills/vault/_sections.py` (internal helpers), register three new tools in `skills/vault/tools.py` taking vault-relative paths with the existing agent-folder write guardrail, delete `skills/markdown_vault/` and `contrib/skills/daily-todo-migration/`, and ship `scripts/migrate_vault_root.py` as a standalone migration helper.

**Tech Stack:** Python 3 stdlib, pytest, existing decafclaw infrastructure (`ToolResult`, `_vault_root`, `_safe_write_path`, `_is_in_agent_dir`, `resolve_page`).

---

## File Structure

**New files:**
- `src/decafclaw/skills/vault/_sections.py` — Port of `Section`, `Document`, and tree helpers (`_build_tree`, `_walk_path`, `_flatten_sections`, `_section_path`, `_find_first_list_item`, `_insert_into_doc`, pattern constants) from `markdown_vault/tools.py`. No tool functions. No dependencies on `markdown_vault`.
- `tests/test_vault_section_tools.py` — Tests for the three new tools, migrated/rewritten from `src/decafclaw/skills/markdown_vault/tests/test_vault.py`.
- `scripts/migrate_vault_root.py` — Migration helper.
- `tests/test_migrate_vault_root.py` — Tests for the migration script.

**Modified files:**
- `src/decafclaw/skills/vault/tools.py` — Add three new tool functions (`tool_vault_show_sections`, `tool_vault_move_lines`, `tool_vault_section`) + register in `TOOLS` + extend `TOOL_DEFINITIONS`.
- `tests/test_commands.py` — Swap `"markdown_vault"` fixture skill name → `"tabstack"`.
- `tests/test_skills.py` — Same.
- `tests/test_context.py` — Same.
- `docs/vault.md` — Document the three new tools + vault-root-is-Obsidian-root story.
- `docs/commands.md` — Remove stale `vault_set_path, vault_daily_path, vault_move_items` example.
- `CLAUDE.md` — Remove the `markdown_vault` bullet from the Skills section of the key files list; add a line noting section-aware tools live in vault.

**Deleted:**
- `src/decafclaw/skills/markdown_vault/` (entire dir: SKILL.md, tools.py, tests/).
- `contrib/skills/daily-todo-migration/` (entire dir).

---

## Task 1: Port `Section`/`Document` parser into `vault/_sections.py`

**Files:**
- Create: `src/decafclaw/skills/vault/_sections.py`
- Source: `src/decafclaw/skills/markdown_vault/tools.py:1-600` (specifically: regex constants, `extract_tags`, `normalize_title`, `_ensure_newlines`, `class Section`, `class Document`, `_build_tree`, `_walk_path`, `_flatten_sections`, `_section_path`, `move_item_across_files`, `bulk_move_items`, `_find_first_list_item`, `_insert_into_doc`)
- Test: `tests/test_vault_section_tools.py` (will be authored in later tasks; this task just smoke-tests the port)

- [ ] **Step 1: Write a minimal smoke test**

Create `tests/test_vault_sections_helpers.py`:

```python
from decafclaw.skills.vault._sections import Document, Section


def test_document_round_trip():
    text = "# Title\n\nBody line.\n\n## Sub\n\n- item\n"
    doc = Document.from_text(text)
    assert doc.to_text() == text


def test_section_walk_by_path():
    text = "# Top\n\n## Child\n\ncontent\n"
    doc = Document.from_text(text)
    sec = doc.find_section("top/child")
    assert sec is not None
    assert sec.normalized_title == "child"
```

- [ ] **Step 2: Run it to confirm it fails (module doesn't exist yet)**

```
pytest tests/test_vault_sections_helpers.py -v
```
Expected: `ModuleNotFoundError: No module named 'decafclaw.skills.vault._sections'`.

- [ ] **Step 3: Port the parser**

Copy the following from `src/decafclaw/skills/markdown_vault/tools.py` into `src/decafclaw/skills/vault/_sections.py`:

- File-level imports (just `datetime`, `re`, `dataclasses`, `Path`)
- Constants: `HEADING_RE`, `CHECKBOX_RE`, `TAG_RE`, `WIKILINK_RE`
- Helpers: `extract_tags`, `normalize_title`, `_ensure_newlines`
- `class Section` (full definition)
- `class Document` (full definition — including `from_text`, `to_text`, `find_section`, `append`, etc.)
- `_build_tree`, `_walk_path`, `_flatten_sections`, `_section_path`
- `move_item_across_files`, `bulk_move_items`
- `_find_first_list_item`, `_insert_into_doc`

Do **not** copy `daily_path` (dropped), `_resolve_workspace` (not needed — vault tools use `_vault_root` / `_safe_write_path` / `resolve_page` from vault/tools.py), or any `tool_*` functions.

Add a module docstring at the top:

```python
"""Section-aware markdown parser for vault pages.

Internal helpers for vault_show_sections, vault_move_lines, vault_section.
Ported from the retired markdown_vault skill — see #264.
"""
```

- [ ] **Step 4: Run the smoke test**

```
pytest tests/test_vault_sections_helpers.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Lint**

```
make lint
```
Expected: clean exit.

- [ ] **Step 6: Commit**

```
git add src/decafclaw/skills/vault/_sections.py tests/test_vault_sections_helpers.py
git commit -m "refactor(vault): port section/document parser into vault/_sections.py"
```

---

## Task 2: Add `tool_vault_show_sections`

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py` (add function, register in `TOOLS`, extend `TOOL_DEFINITIONS`)
- Test: `tests/test_vault_section_tools.py` (new file)

Behavior summary:
- Args: `page: str`, `section: str | None = None`
- Without `section`: returns document outline — every heading line with absolute line number (e.g. `1: # Title`, `3: ## Sub`).
- With `section`: returns that section's content with absolute line numbers on every line.
- Path resolution: `resolve_page(ctx.config, page)` — bare name or partial path, `.md` optional. Returns `ToolResult(text="[error: ...]")` if not found.
- Read-only; no agent-folder guardrail needed.

- [ ] **Step 1: Write failing tests**

Create `tests/test_vault_section_tools.py`:

```python
import pytest

from decafclaw.skills.vault.tools import (
    tool_vault_show_sections,
)


@pytest.mark.asyncio
async def test_show_sections_outline(vault_ctx):
    # vault_ctx fixture: creates tmp vault with a test page (see below)
    (vault_ctx.config.vault_root / "agent" / "pages" / "note.md").parent.mkdir(
        parents=True, exist_ok=True
    )
    (vault_ctx.config.vault_root / "agent" / "pages" / "note.md").write_text(
        "# Top\n\n## Sub A\n\ncontent a\n\n## Sub B\n\ncontent b\n"
    )
    result = await tool_vault_show_sections(vault_ctx, page="agent/pages/note")
    assert "# Top" in result.text
    assert "## Sub A" in result.text
    assert "## Sub B" in result.text
    # Line numbers present
    assert "1:" in result.text or "1 " in result.text


@pytest.mark.asyncio
async def test_show_sections_specific(vault_ctx):
    (vault_ctx.config.vault_root / "agent" / "pages" / "note.md").parent.mkdir(
        parents=True, exist_ok=True
    )
    (vault_ctx.config.vault_root / "agent" / "pages" / "note.md").write_text(
        "# Top\n\n## Sub A\n\ncontent a\n\n## Sub B\n\ncontent b\n"
    )
    result = await tool_vault_show_sections(
        vault_ctx, page="agent/pages/note", section="top/sub a"
    )
    assert "content a" in result.text
    assert "content b" not in result.text


@pytest.mark.asyncio
async def test_show_sections_missing_page(vault_ctx):
    result = await tool_vault_show_sections(vault_ctx, page="agent/pages/missing")
    assert "[error" in result.text.lower() or "not found" in result.text.lower()
```

A shared `vault_ctx` fixture should be added (or reused) at `tests/conftest.py` or in the test file's own conftest. If a similar vault-ctx fixture already exists in `tests/test_vault_tools.py` / conftest.py, reuse it. Check there first:

```
grep -rn "vault_ctx\|vault_root.*tmp_path" tests/conftest.py tests/test_vault_tools.py 2>/dev/null
```

If not present, add this fixture to the new test file:

```python
from dataclasses import replace
from pathlib import Path

import pytest

from decafclaw.config import Config
from decafclaw.context import Context


@pytest.fixture
def vault_ctx(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    config = Config()
    config = replace(
        config,
        vault_root=vault,
        vault_agent_dir=vault / "agent",
        workspace_path=tmp_path,
    )
    ctx = Context(config=config)
    return ctx
```

(Use whatever fixture shape matches the existing test suite — inspect `tests/test_vault_tools.py` before authoring.)

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_vault_section_tools.py -v
```
Expected: ImportError (`tool_vault_show_sections` doesn't exist).

- [ ] **Step 3: Implement `tool_vault_show_sections`**

In `src/decafclaw/skills/vault/tools.py`, after the existing `tool_vault_backlinks` function, add:

```python
async def tool_vault_show_sections(
    ctx, page: str, section: str | None = None
) -> ToolResult:
    """Show a vault page's section outline or a specific section with line numbers."""
    log.info(f"[tool:vault_show_sections] page={page!r} section={section!r}")
    path = resolve_page(ctx.config, page)
    if path is None or not path.exists():
        return ToolResult(text=f"[error: page not found: {page}]")
    from decafclaw.skills.vault._sections import Document
    text = path.read_text(encoding="utf-8")
    doc = Document.from_text(text)
    if section is None:
        # Outline: every heading, line-numbered
        lines = []
        for sec in doc.sections_flat():
            line_no = sec.heading_line + 1  # 1-based
            hashes = "#" * sec.level
            lines.append(f"{line_no}: {hashes} {sec.title}")
        return ToolResult(text="\n".join(lines) if lines else "(no sections)")
    # Specific section: body with line numbers
    sec = doc.find_section(section)
    if sec is None:
        return ToolResult(text=f"[error: section not found: {section}]")
    start = sec.heading_line
    end = sec.end_line  # exclusive
    numbered = [
        f"{i + 1}: {doc.lines[i].rstrip()}" for i in range(start, end)
    ]
    return ToolResult(text="\n".join(numbered))
```

**Note:** the exact `Document`/`Section` API (attribute names like `heading_line`, `end_line`, `sections_flat`) is the ported parser's API from markdown_vault/tools.py. Verify attribute names match what you ported in Task 1 before writing — adjust if different.

Register:

```python
# In TOOLS dict
"vault_show_sections": tool_vault_show_sections,
```

Extend `TOOL_DEFINITIONS` with a new entry (follow the existing style). Tool description:

```
"Show a vault page's section structure (headings with absolute line numbers) "
"or a specific section's content with line numbers. Use this to see what's "
"in a page before editing with vault_write or vault_move_lines."
```

Parameters: `page` (required, string, vault-relative path or bare name), `section` (optional, string, slash-separated section path).

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_vault_section_tools.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Lint + full suite**

```
make lint && make test
```
Expected: clean.

- [ ] **Step 6: Commit**

```
git add src/decafclaw/skills/vault/tools.py tests/test_vault_section_tools.py
git commit -m "feat(vault): add vault_show_sections tool"
```

---

## Task 3: Add `tool_vault_move_lines`

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py`
- Test: `tests/test_vault_section_tools.py`

Behavior summary:
- Args: `from_page: str`, `to_page: str`, `lines: str` (comma-separated line numbers), `to_section: str | None = None`, `position: str = "append"` (or `"prepend"`).
- Both `from_page` and `to_page` must resolve into `agent/` for writes — writes to user or admin pages refused.
- Source: strip lines from `from_page`, target: append/prepend into `to_section` (or whole file if `to_section` is None) of `to_page`.
- Returns a short success summary or `ToolResult(text="[error: ...]")`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vault_section_tools.py`:

```python
@pytest.mark.asyncio
async def test_move_lines_basic(vault_ctx):
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    (agent_pages / "src.md").write_text(
        "# Top\n\n- [ ] task1\n- [ ] task2\n- [ ] task3\n"
    )
    (agent_pages / "dst.md").write_text("# Today\n\n## inbox\n")
    result = await tool_vault_move_lines(
        vault_ctx,
        from_page="agent/pages/src",
        to_page="agent/pages/dst",
        lines="3,4",
        to_section="today/inbox",
    )
    assert "[error" not in result.text.lower()
    src_after = (agent_pages / "src.md").read_text()
    dst_after = (agent_pages / "dst.md").read_text()
    assert "task1" not in src_after
    assert "task2" not in src_after
    assert "task3" in src_after
    assert "task1" in dst_after
    assert "task2" in dst_after


@pytest.mark.asyncio
async def test_move_lines_refuses_write_outside_agent(vault_ctx):
    vault = vault_ctx.config.vault_root
    (vault / "agent" / "pages").mkdir(parents=True)
    (vault / "user_notes").mkdir()
    (vault / "agent" / "pages" / "src.md").write_text(
        "# Top\n\n- [ ] x\n"
    )
    (vault / "user_notes" / "dst.md").write_text("# User\n")
    # Writing into a user page must be refused
    result = await tool_vault_move_lines(
        vault_ctx,
        from_page="agent/pages/src",
        to_page="user_notes/dst",
        lines="3",
    )
    assert "[error" in result.text.lower()
    # user_notes/dst.md must be unchanged
    assert (vault / "user_notes" / "dst.md").read_text() == "# User\n"
```

Add `tool_vault_move_lines` to the top-level import.

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_vault_section_tools.py -v
```
Expected: ImportError or AttributeError.

- [ ] **Step 3: Implement**

In `src/decafclaw/skills/vault/tools.py`, after `tool_vault_show_sections`:

```python
async def tool_vault_move_lines(
    ctx,
    from_page: str,
    to_page: str,
    lines: str,
    to_section: str | None = None,
    position: str = "append",
) -> ToolResult:
    """Move specific lines (by line number) from one vault page to another."""
    log.info(
        f"[tool:vault_move_lines] from={from_page!r} to={to_page!r} "
        f"lines={lines!r} section={to_section!r} position={position!r}"
    )
    # Source must be resolvable and writable (we're removing lines from it)
    from_path = resolve_page(ctx.config, from_page)
    if from_path is None or not from_path.exists():
        return ToolResult(text=f"[error: source page not found: {from_page}]")
    if not _is_in_agent_dir(ctx.config, from_path):
        return ToolResult(
            text=f"[error: cannot modify page outside agent folder: {from_page}]"
        )
    # Target must be writable — use _safe_write_path for validation
    to_path = resolve_page(ctx.config, to_page)
    if to_path is None or not to_path.exists():
        return ToolResult(text=f"[error: target page not found: {to_page}]")
    if not _is_in_agent_dir(ctx.config, to_path):
        return ToolResult(
            text=f"[error: cannot write to page outside agent folder: {to_page}]"
        )
    # Parse line numbers
    try:
        line_nums = sorted({int(s.strip()) for s in lines.split(",") if s.strip()})
    except ValueError:
        return ToolResult(text=f"[error: invalid lines argument: {lines!r}]")
    if not line_nums:
        return ToolResult(text="[error: no line numbers provided]")
    from decafclaw.skills.vault._sections import Document, _insert_into_doc
    from_doc = Document.from_text(from_path.read_text(encoding="utf-8"))
    to_doc = Document.from_text(to_path.read_text(encoding="utf-8"))
    # Collect line text in original order, then delete in reverse
    moved: list[str] = []
    for n in line_nums:
        idx = n - 1
        if idx < 0 or idx >= len(from_doc.lines):
            return ToolResult(text=f"[error: line {n} out of range in {from_page}]")
        moved.append(from_doc.lines[idx].rstrip("\n"))
    for n in sorted(line_nums, reverse=True):
        from_doc._delete_lines(n - 1, 1)
    # Insert into target
    # Signature: _insert_into_doc(doc, lines_to_insert, to_section, position)
    _insert_into_doc(to_doc, moved, to_section, position)
    from_path.write_text(from_doc.to_text(), encoding="utf-8")
    to_path.write_text(to_doc.to_text(), encoding="utf-8")
    return ToolResult(
        text=f"Moved {len(moved)} line(s) from {from_page} to {to_page}"
        + (f" section '{to_section}'" if to_section else "")
    )
```

Register in `TOOLS` and extend `TOOL_DEFINITIONS` with a `vault_move_lines` entry. Description:

```
"Move specific lines (by absolute line number) from one vault page to "
"another. Use vault_show_sections first to see line numbers. Good for "
"migrating to-do items between daily notes. Both pages must be under the "
"agent folder. When to_section is omitted, moves into the whole target file."
```

Parameters: `from_page`, `to_page`, `lines` (required); `to_section`, `position` (optional).

**Note:** If the existing `Document` class uses a different helper name than `_delete_lines` (e.g., `delete_lines`), adjust. Re-check the ported parser.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_vault_section_tools.py -v
```
Expected: 5 passed (2 show_sections + 1 missing + 2 move_lines).

- [ ] **Step 5: Lint + full suite**

```
make lint && make test
```

- [ ] **Step 6: Commit**

```
git add src/decafclaw/skills/vault/tools.py tests/test_vault_section_tools.py
git commit -m "feat(vault): add vault_move_lines tool"
```

---

## Task 4: Add `tool_vault_section`

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py`
- Test: `tests/test_vault_section_tools.py`

Behavior summary:
- Args: `page: str`, `action: str` (one of `add`, `remove`, `rename`, `move`), plus action-specific args: `section`, `title`, `level`, `after`, `before`, `parent`.
- Page must be in `agent/` (it's a write).
- Dispatches to the ported `Document` methods (or inline logic, whichever matches how `markdown_vault/tools.py:tool_md_section` was implemented — copy its dispatch logic).

- [ ] **Step 1: Write failing tests**

Append to `tests/test_vault_section_tools.py`:

```python
@pytest.mark.asyncio
async def test_section_add(vault_ctx):
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    (agent_pages / "note.md").write_text("# Top\n\n## First\n")
    result = await tool_vault_section(
        vault_ctx,
        page="agent/pages/note",
        action="add",
        title="Second",
        level=2,
        after="top/first",
    )
    assert "[error" not in result.text.lower()
    content = (agent_pages / "note.md").read_text()
    assert "## Second" in content


@pytest.mark.asyncio
async def test_section_rename(vault_ctx):
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    (agent_pages / "note.md").write_text("# Top\n\n## Old\n")
    result = await tool_vault_section(
        vault_ctx,
        page="agent/pages/note",
        action="rename",
        section="top/old",
        title="New",
    )
    assert "[error" not in result.text.lower()
    content = (agent_pages / "note.md").read_text()
    assert "## New" in content
    assert "## Old" not in content


@pytest.mark.asyncio
async def test_section_refuses_write_outside_agent(vault_ctx):
    vault = vault_ctx.config.vault_root
    (vault / "user_notes").mkdir()
    (vault / "user_notes" / "x.md").write_text("# U\n")
    result = await tool_vault_section(
        vault_ctx,
        page="user_notes/x",
        action="add",
        title="New",
        level=2,
    )
    assert "[error" in result.text.lower()
    # Unchanged
    assert (vault / "user_notes" / "x.md").read_text() == "# U\n"
```

Add `tool_vault_section` to the top-level import.

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_vault_section_tools.py -v
```
Expected: failures for the three new tests.

- [ ] **Step 3: Implement**

Port the dispatch logic from `markdown_vault/tools.py:tool_md_section` into `vault/tools.py`. The shape:

```python
async def tool_vault_section(
    ctx,
    page: str,
    action: str,
    section: str | None = None,
    title: str | None = None,
    level: int = 1,
    after: str | None = None,
    before: str | None = None,
    parent: str | None = None,
) -> ToolResult:
    """Section operations on a vault page: add, remove, rename, or move."""
    log.info(
        f"[tool:vault_section] page={page!r} action={action!r} "
        f"section={section!r} title={title!r}"
    )
    path = resolve_page(ctx.config, page)
    if path is None or not path.exists():
        return ToolResult(text=f"[error: page not found: {page}]")
    if not _is_in_agent_dir(ctx.config, path):
        return ToolResult(
            text=f"[error: cannot modify page outside agent folder: {page}]"
        )
    from decafclaw.skills.vault._sections import Document
    doc = Document.from_text(path.read_text(encoding="utf-8"))
    # Dispatch (copy logic from markdown_vault/tools.py:tool_md_section)
    # ...
    path.write_text(doc.to_text(), encoding="utf-8")
    return ToolResult(text=f"Section {action} on {page} complete.")
```

Copy the exact dispatch switch (add/remove/rename/move) from markdown_vault/tools.py verbatim; only the path resolution and guardrail checks are new. Error returns should use `ToolResult(text="[error: ...]")`.

Register in `TOOLS` and extend `TOOL_DEFINITIONS`. Description:

```
"Section operations on a vault page: add, remove, rename, or move a "
"section. Actions: 'add', 'remove', 'rename', 'move'. Page must be under "
"the agent folder."
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_vault_section_tools.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Lint + full suite**

```
make lint && make test
```

- [ ] **Step 6: Commit**

```
git add src/decafclaw/skills/vault/tools.py tests/test_vault_section_tools.py
git commit -m "feat(vault): add vault_section tool"
```

---

## Task 5: Delete `markdown_vault` skill

**Files:**
- Delete: `src/decafclaw/skills/markdown_vault/` (entire directory)

- [ ] **Step 1: Verify new tools fully cover the old ones**

```
grep -rn "md_show\|md_move_lines\|md_section\|md_create\|vault_daily_path" \
    src/ tests/ contrib/ docs/ CLAUDE.md README.md 2>/dev/null \
    | grep -v "skills/markdown_vault/\|docs/dev-sessions/"
```

Every hit outside the markdown_vault skill and old dev-session docs should either (a) be in test fixtures that we'll update in Task 7, or (b) be in `contrib/skills/daily-todo-migration/` which we'll delete in Task 6, or (c) be in `docs/commands.md:14` which we'll update in Task 9. If something else pops up, surface it and pause.

- [ ] **Step 2: Delete the skill directory**

```
rm -rf src/decafclaw/skills/markdown_vault/
```

- [ ] **Step 3: Run full test suite**

```
make test
```

Tests referencing `"markdown_vault"` as a string fixture will still pass here — the test fixtures just name a non-existent skill; discovery won't trip unless a test activates it. The real cleanup is in Task 7.

If any test actually fails (not just warnings), investigate — the skill may be imported somewhere we missed.

- [ ] **Step 4: Lint**

```
make lint
```

- [ ] **Step 5: Commit**

```
git add -A src/decafclaw/skills/markdown_vault/
git commit -m "refactor: delete markdown_vault skill (tools folded into vault)"
```

---

## Task 6: Delete `contrib/skills/daily-todo-migration/`

**Files:**
- Delete: `contrib/skills/daily-todo-migration/`

- [ ] **Step 1: Delete**

```
rm -rf contrib/skills/daily-todo-migration/
```

- [ ] **Step 2: Verify tests still pass**

```
make test
```

- [ ] **Step 3: Commit**

```
git add -A contrib/skills/daily-todo-migration/
git commit -m "chore: delete daily-todo-migration contrib skill (pending rework, see follow-up issue)"
```

---

## Task 7: Update test fixtures that reference `"markdown_vault"`

**Files:**
- Modify: `tests/test_commands.py:199,207,222,225,230`
- Modify: `tests/test_skills.py:175,179`
- Modify: `tests/test_context.py:121`

Strategy: replace the string `"markdown_vault"` with `"tabstack"` in fixtures. `tabstack` is an existing bundled skill, so discovery will find it. Behavior of these tests is about the framework (required-skills activation, etc.), not about any specific skill semantics.

- [ ] **Step 1: Check each callsite**

```
grep -n '"markdown_vault"' tests/test_commands.py tests/test_skills.py tests/test_context.py
```

- [ ] **Step 2: Replace in `tests/test_commands.py`**

Use Edit: change every `"markdown_vault"` literal in that file to `"tabstack"`, including the `SkillInfo(name=..., description="Vault", ...)` lines (change description to `"Tabstack"` to keep semantics readable).

- [ ] **Step 3: Replace in `tests/test_skills.py`**

Same swap.

- [ ] **Step 4: Replace in `tests/test_context.py`**

Same swap.

- [ ] **Step 5: Run the three files to confirm green**

```
pytest tests/test_commands.py tests/test_skills.py tests/test_context.py -v
```
Expected: all pass.

- [ ] **Step 6: Run the full suite**

```
make test
```

- [ ] **Step 7: Commit**

```
git add tests/test_commands.py tests/test_skills.py tests/test_context.py
git commit -m "test: swap markdown_vault fixture references to tabstack"
```

---

## Task 8: `scripts/migrate_vault_root.py`

**Files:**
- Create: `scripts/migrate_vault_root.py`
- Test: `tests/test_migrate_vault_root.py`

Behavior summary:
- CLI: `python scripts/migrate_vault_root.py --from <old_root> --to <new_root> [--config <path>] [--apply]`
- Dry-run by default. Prints what it would do. `--apply` executes.
- Refuses (nonzero exit, error message) if `<new_root>/agent/` already exists.
- Refuses if `<old_root>/agent/` does not exist.
- On `--apply`:
  1. `shutil.move(<old_root>/agent, <new_root>/agent)` (atomic on same filesystem).
  2. Read `<config>` (default `data/decafclaw/config.json`), set `"vault_path": str(<new_root>)`, write back.
  3. Print "Migration complete. Run `make reindex` to rebuild the embedding index."

- [ ] **Step 1: Write failing tests**

Create `tests/test_migrate_vault_root.py`:

```python
import json
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parent.parent / "scripts" / "migrate_vault_root.py"


def _setup(tmp_path):
    old = tmp_path / "old_vault"
    new = tmp_path / "new_vault"
    (old / "agent" / "pages").mkdir(parents=True)
    (old / "agent" / "pages" / "note.md").write_text("content\n")
    new.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"vault_path": str(old)}))
    return old, new, config


def test_dry_run_reports_without_moving(tmp_path):
    old, new, config = _setup(tmp_path)
    r = subprocess.run(
        [
            "python", str(SCRIPT),
            "--from", str(old),
            "--to", str(new),
            "--config", str(config),
        ],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    # Old content still there
    assert (old / "agent" / "pages" / "note.md").exists()
    # New dir still empty of agent/
    assert not (new / "agent").exists()


def test_apply_moves_and_updates_config(tmp_path):
    old, new, config = _setup(tmp_path)
    r = subprocess.run(
        [
            "python", str(SCRIPT),
            "--from", str(old),
            "--to", str(new),
            "--config", str(config),
            "--apply",
        ],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (new / "agent" / "pages" / "note.md").exists()
    assert not (old / "agent").exists()
    updated = json.loads(config.read_text())
    assert updated["vault_path"] == str(new)


def test_apply_refuses_if_target_agent_exists(tmp_path):
    old, new, config = _setup(tmp_path)
    (new / "agent").mkdir()
    r = subprocess.run(
        [
            "python", str(SCRIPT),
            "--from", str(old),
            "--to", str(new),
            "--config", str(config),
            "--apply",
        ],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "already exists" in (r.stderr + r.stdout).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_migrate_vault_root.py -v
```
Expected: `FileNotFoundError` or similar — script doesn't exist.

- [ ] **Step 3: Implement**

Create `scripts/migrate_vault_root.py`:

```python
#!/usr/bin/env python3
"""Move the decafclaw agent content from one vault root to another.

Typical use: unifying the agent's vault with the user's Obsidian vault.

Dry-run by default. Pass --apply to execute.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="src", required=True, type=Path,
                        help="Current vault root (contains agent/)")
    parser.add_argument("--to", dest="dst", required=True, type=Path,
                        help="New vault root")
    parser.add_argument("--config", type=Path,
                        default=Path("data/decafclaw/config.json"),
                        help="Path to config.json to update (default: %(default)s)")
    parser.add_argument("--apply", action="store_true",
                        help="Actually perform the migration (default: dry-run)")
    args = parser.parse_args(argv)

    src = args.src.resolve()
    dst = args.dst.resolve()
    config_path = args.config

    src_agent = src / "agent"
    dst_agent = dst / "agent"

    if not src_agent.exists():
        print(f"ERROR: source agent folder not found: {src_agent}", file=sys.stderr)
        return 1
    if dst_agent.exists():
        print(f"ERROR: target agent folder already exists: {dst_agent}",
              file=sys.stderr)
        return 1
    if not dst.exists():
        print(f"ERROR: target vault root does not exist: {dst}", file=sys.stderr)
        return 1
    if not config_path.exists():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 1

    if not args.apply:
        print("DRY RUN (pass --apply to execute):")
        print(f"  move {src_agent} -> {dst_agent}")
        print(f"  patch {config_path}: vault_path = {dst}")
        return 0

    print(f"Moving {src_agent} -> {dst_agent}")
    shutil.move(str(src_agent), str(dst_agent))

    print(f"Updating {config_path}: vault_path = {dst}")
    config = json.loads(config_path.read_text())
    config["vault_path"] = str(dst)
    config_path.write_text(json.dumps(config, indent=2) + "\n")

    print()
    print("Migration complete. Run `make reindex` to rebuild the embedding index.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_migrate_vault_root.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Lint + full suite**

```
make lint && make test
```

- [ ] **Step 6: Commit**

```
git add scripts/migrate_vault_root.py tests/test_migrate_vault_root.py
git commit -m "feat(scripts): add migrate_vault_root.py for unifying vault roots"
```

---

## Task 9: Docs

**Files:**
- Modify: `docs/vault.md` — add the three new tools to the tool list; add a section "Configuring the vault root" describing how to point at your Obsidian vault via `vault_path` in `config.json`.
- Modify: `docs/commands.md:14` — remove the stale `allowed-tools: vault_set_path, vault_daily_path, vault_move_items` example; replace with a realistic one like `allowed-tools: vault_read, vault_write, vault_search`.
- Modify: `CLAUDE.md` — in the **Skills** section of the key files list, delete any `markdown_vault` reference and note that section-aware tools (`vault_show_sections`, `vault_move_lines`, `vault_section`) are part of the `vault` skill.

- [ ] **Step 1: Read current state**

```
head -80 docs/vault.md
sed -n '1,30p' docs/commands.md
grep -n "markdown_vault\|vault skill" CLAUDE.md
```

- [ ] **Step 2: Update `docs/vault.md`**

Add the three tools to the "Tools" table (or equivalent section). Signatures:

- `vault_show_sections(page, section=None)` — outline or section content with line numbers.
- `vault_move_lines(from_page, to_page, lines, to_section=None, position="append")` — move lines between pages. Both must be under `agent/`.
- `vault_section(page, action, section=None, title=None, level=1, after=None, before=None, parent=None)` — add/remove/rename/move sections.

Add a short "Configuring the vault root" paragraph explaining: default is `workspace/vault/`, set `"vault_path": "<abs-path>"` in `data/<agent-id>/config.json` to point at a different tree (e.g. your Obsidian vault). Agent content lives at `agent/` under whatever the root is.

- [ ] **Step 3: Update `docs/commands.md:14`**

Use Edit:

```
old: allowed-tools: vault_set_path, vault_daily_path, vault_move_items
new: allowed-tools: vault_read, vault_write, vault_search
```

- [ ] **Step 4: Update `CLAUDE.md`**

Find the `markdown_vault` line in the Skills subsection of "Key files" and delete it. In the `vault` skill entry nearby, append: "Section-aware tools (`vault_show_sections`, `vault_move_lines`, `vault_section`) live here too."

- [ ] **Step 5: Verify docs build (spot-check)**

No doc build system — just re-read the modified files and confirm they parse as intended markdown.

- [ ] **Step 6: Commit**

```
git add docs/vault.md docs/commands.md CLAUDE.md
git commit -m "docs: document folded vault section tools, drop markdown_vault refs"
```

---

## Task 10: Final verification

**Files:** none (just commands)

- [ ] **Step 1: Full lint**

```
make lint
```

- [ ] **Step 2: Full test suite (parallel, check for new slow tests)**

```
make test -- --durations=25
```

If any of the new tests land in the top 25, investigate per CLAUDE.md test-speed discipline.

- [ ] **Step 3: Search for any remaining references**

```
grep -rn "markdown_vault\|md_show\|md_move_lines\|md_section\|md_create\|vault_daily_path" \
    src/ tests/ contrib/ docs/ CLAUDE.md README.md scripts/ 2>/dev/null \
    | grep -v "docs/dev-sessions/"
```

Expected: nothing except maybe migration notes in the current session's docs dir (which is fine).

- [ ] **Step 3b: Dead-code sweep in `vault/_sections.py`**

Check whether `move_item_across_files` and `bulk_move_items` are imported/used anywhere in the tree:

```
grep -rn "move_item_across_files\|bulk_move_items" src/ tests/ scripts/ 2>/dev/null
```

If the only hits are their definitions in `vault/_sections.py`, delete them (and any tests or imports they reference). These were ported defensively for Tasks 2-4 but Task 3's `tool_vault_move_lines` uses `Document._delete_lines` + `_insert_into_doc` directly. Carrying dead code just because it came along for the port is a maintenance trap.

After any deletion, re-run `make lint && make test`. Commit as a separate small change: `chore(vault): drop unused cross-file move helpers from _sections.py`.

- [ ] **Step 4: Open the PR**

```
git push -u origin retire-markdown-vault
gh pr create --title "Retire markdown_vault, fold section tools into vault (#264)" \
    --body "$(cat <<'EOF'
## Summary

- Port `Section`/`Document` parser into `vault/_sections.py` (internal).
- Add three new tools to the always-loaded `vault` skill: `vault_show_sections`, `vault_move_lines`, `vault_section` — all taking vault-relative paths, enforcing the `agent/` write guardrail.
- Retire `src/decafclaw/skills/markdown_vault/` entirely.
- Retire `contrib/skills/daily-todo-migration/` (follow-up issue will recreate on unified vault).
- Drop `md_create` and `vault_daily_path` — callers can do template-read + date-math inline.
- Swap `"markdown_vault"` test fixtures to `"tabstack"`.
- Add `scripts/migrate_vault_root.py` for moving `workspace/vault/agent/` to a new vault root (e.g. your Obsidian vault).
- Update `docs/vault.md`, `docs/commands.md`, `CLAUDE.md`.

Closes #264.

## Test plan

- [ ] `make lint && make test` clean on CI.
- [ ] Manual: point `vault_path` at your Obsidian vault via `scripts/migrate_vault_root.py --apply`, run `make reindex`, confirm `vault_search`/`vault_read` find your existing pages.
- [ ] Manual: exercise the three new tools — `vault_show_sections` on an agent page, `vault_move_lines` between two agent pages, `vault_section` add/rename.
EOF
)"
```

- [ ] **Step 5: File follow-up issue for daily-todo-migration rework**

```
gh issue create --repo lmorchard/decafclaw \
    --title "Recreate daily-todo-migration skill on unified vault" \
    --body "$(cat <<'EOF'
## Background

The old `contrib/skills/daily-todo-migration/` skill was deleted in #264 because it depended on `md_create` and `vault_daily_path` (also dropped in #264) and used workspace-relative paths (`obsidian/main/journals/...`) under the pre-unification vault model.

## Goal

Recreate the skill on top of the unified vault: it should use `vault_read`/`vault_write` for the template-based file creation, `vault_show_sections` + `vault_move_lines` for the actual migration, and compute daily paths inline (or rely on a config/convention for where user daily notes live in the vault).

## Tasks

- Decide where daily notes live in the new vault (presumably `journals/YYYY/YYYY-MM-DD.md` relative to vault root, matching Obsidian Daily Notes plugin convention).
- Rewrite the skill to use only tools that exist today.
- Add a test scenario or at least a documented manual procedure.

## Scope-out

- Reintroducing `vault_daily_path` unless we decide we need it. Inline date math is probably fine.
EOF
)"
```

Then add the issue to the project board at whatever priority fits (probably P2, size S).

---

## Self-review (plan → spec coverage)

- [x] Unify vault root story → Task 8 (migration script) + Task 9 (docs).
- [x] Three folded tools → Tasks 2, 3, 4 (TDD each).
- [x] `Section`/`Document` parser relocation → Task 1.
- [x] `md_create`/`vault_daily_path` dropped → enforced by not porting them in Task 1.
- [x] Delete markdown_vault skill → Task 5.
- [x] Delete daily-todo-migration → Task 6.
- [x] Test fixtures swap → Task 7.
- [x] Migration helper → Task 8.
- [x] Docs → Task 9.
- [x] Follow-up issue for daily-todo-migration → Task 10 Step 5.
- [x] Final verification → Task 10.
