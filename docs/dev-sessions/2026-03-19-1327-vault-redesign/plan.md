# Vault Tools Redesign — Plan

## Status: Ready

## Overview

Three phases. Phase 1 rewrites tools.py with the 5 new tools, keeping the Document/Section parsing library intact. Phase 2 rewrites SKILL.md with updated workflows. Phase 3 updates tests and docs. Each phase ends with lint + test passing and a commit.

This is a rewrite, not incremental changes — we replace the tool functions and definitions in one go since the old and new APIs are incompatible.

---

## Phase 1: Rewrite tools.py — 5 new tools

**Goal**: Replace 29 tool functions and definitions with 5. Keep the Document/Section/ChecklistItem library code at the top of the file (still used internally by md_show, md_move_lines, md_section).

**Files**: `src/decafclaw/skills/markdown_vault/tools.py`

### Prompt

Read `src/decafclaw/skills/markdown_vault/tools.py` — the file has two parts:
1. Lines 1-640: Document model (patterns, Section, ChecklistItem, Document class, tree helpers, cross-file move helpers). **Keep all of this.**
2. Lines 640+: Tool functions, TOOLS dict, TOOL_DEFINITIONS list. **Replace all of this.**

Also read the spec at `.claude/dev-sessions/2026-03-19-1327-vault-redesign/spec.md`.

Replace everything after the library code with:

### `vault_daily_path(ctx, date=None, offset=0, base_path="")`

Keep the existing `daily_path()` helper function. The tool wraps it and prepends `base_path`:

```python
def tool_vault_daily_path(ctx, base_path: str = "", date: str | None = None, offset: int = 0) -> ToolResult:
    path = daily_path(date=date, offset=offset)
    if base_path:
        path = f"{base_path}/{path}"
    return ToolResult(text=path)
```

If `base_path` is empty, just return the bare journal path — the agent handles prefixing.

### `md_show(ctx, file, section=None)`

Uses the existing Document class to parse the file. Two modes:

**Outline mode** (no section): show all headings with line numbers.
```
  5: # today
 12: # tonight
 16: # tomorrow
```

**Section mode**: show the section heading + all content lines with absolute line numbers.
```
# today (line 5)
  6: - [ ] consider renovatebot
  7: - [ ] check out [onecli](https://github.com/...)
  8: - [x] take a look at octonous
```

The file path is workspace-relative (resolved via `config.workspace_path`). No vault base path resolution.

### `md_move_lines(ctx, from_file, to_file, to_section, lines)`

- Parse `lines` as comma-separated integers
- Read both files as Document objects
- Find the target section in to_file
- Collect the specified lines from from_file (by absolute line number)
- Remove them from source (reverse order to preserve indices)
- Append to target section
- Save both files
- Return summary

### `md_section(ctx, file, action, section=None, title=None, level=1, after=None, before=None, parent=None)`

Dispatch on `action`:
- `add`: use Document.add_section(title, level, after/before/parent)
- `remove`: use Document.remove_section(section)
- `rename`: use Document.rename_section(section, title)
- `move`: use Document.move_section(section, after/before)

### `md_create(ctx, file, template=None, content="")`

Same logic as current vault_create_file but resolves paths from workspace root (no vault base path). Template `{{date}}` and `{{date:FORMAT}}` substitution.

### Registry

```python
TOOLS = {
    "vault_daily_path": tool_vault_daily_path,
    "md_show": tool_md_show,
    "md_move_lines": tool_md_move_lines,
    "md_section": tool_md_section,
    "md_create": tool_md_create,
}
```

TOOL_DEFINITIONS with 5 entries. Keep parameter names simple and predictable — `file` (not `path`), `section`, `action`, `lines`, `template`.

### What to delete

- `init()` function and `_workspace_path` global — no longer needed (tools resolve from ctx.config.workspace_path)
- `_resolve()` function — replace with a simpler `_resolve_workspace(config, path)` that resolves from workspace root
- All 29 `tool_vault_*` functions
- All 29 TOOL_DEFINITIONS entries
- `_get_vault_base`, `_set_vault_base` helpers

### What to keep

- All library code: patterns (HEADING_RE, CHECKBOX_RE, TAG_RE, WIKILINK_RE), helper functions (extract_tags, daily_path, normalize_title, _ensure_newlines), dataclasses (Section, ChecklistItem), Document class, tree helpers (_build_tree, _walk_path, _flatten_sections, _section_path), cross-file helpers (move_item_across_files, bulk_move_items)

Lint and test after — existing tests will break (expected, fixed in Phase 3).

---

## Phase 2: Rewrite SKILL.md

**Goal**: Update the skill documentation with new tool set, workflows, and guidance on using workspace tools for basic operations.

**File**: `src/decafclaw/skills/markdown_vault/SKILL.md`

### Prompt

Rewrite SKILL.md with:

1. **Concepts**: explain that the vault lives in the workspace, basic file operations use workspace tools, this skill adds section awareness and daily path helpers.

2. **Getting Started**: check memory for vault base path, use it with vault_daily_path.

3. **Available Tools**: document the 5 tools with parameters and examples.

4. **Using Workspace Tools**: explain that workspace_read, workspace_edit, workspace_insert, workspace_replace_lines, workspace_list are the primary tools for reading and editing vault files. The md_* tools add section navigation and cross-file moves.

5. **Common Workflows** — updated for new tools:
   - "Check off X" → md_show to find line number → workspace_replace_lines to toggle checkbox
   - "Add a task to today" → md_show to find section end → workspace_insert
   - "Move unchecked items" → md_show both files → md_move_lines with specific line numbers
   - "Create today's note" → vault_daily_path → md_create with template
   - "What's in my today section?" → vault_daily_path → md_show with section="today"

6. **Safety Protocols**: keep existing (append-only by default, confirm before deletion).

---

## Phase 3: Tests and docs

**Goal**: Rewrite tests for the new tool set. Update CLAUDE.md and README.

**Files**: `src/decafclaw/skills/markdown_vault/tests/test_vault.py`, `CLAUDE.md`

### Prompt

Read `src/decafclaw/skills/markdown_vault/tests/test_vault.py` — currently 1000+ lines testing the Document model and old tool functions.

**Keep**: all Document model tests (parsing, sections, checklist items, tags, round-trip). These test the library code which is unchanged.

**Replace**: any tests that call old tool functions (tool_vault_read, tool_vault_check, etc.) with tests for the 5 new tools.

**New tests for each tool:**

`md_show`:
- Outline mode shows all headings with line numbers
- Section mode shows content with line numbers
- Section not found returns error
- File not found returns error

`md_move_lines`:
- Move specific lines between files
- Lines removed from source, appended to target section
- Invalid line numbers handled gracefully
- Same-file move works

`md_section`:
- Add section at end / after / before / as child
- Remove section
- Rename section
- Move section

`md_create`:
- Create from template with date substitution
- Create with content
- Won't overwrite existing file

`vault_daily_path`:
- With base_path
- Without base_path
- With offset

**Update CLAUDE.md**: update key files description for markdown_vault skill.

Lint and test after.

---

## Dependency Graph

```
Phase 1 (rewrite tools.py — 5 new tools)
  ↓
Phase 2 (rewrite SKILL.md)
  ↓
Phase 3 (tests + docs)
```

Phases 1 and 2 can be done in either order (independent files), but Phase 1 first lets us verify the code compiles. Phase 3 must come last.

## Risk Notes

- **Breaking change**: all 29 old tool names stop working immediately. Any conversation that has them in history will see "unknown tool" errors on retry. This is acceptable — skills are per-conversation and activate fresh each time.
- **Two-step workflows**: "check off an item" is now md_show → workspace_replace_lines (two calls) instead of vault_check (one call). This is more round-trips but more reliable — the agent sees exact content before editing.
- **Document model kept but partially unused**: some Document methods (bulk_check, find_items, etc.) are no longer called by tools. They're still tested and could be useful for future tools. Not worth removing — dead code in a library is less harmful than premature deletion.
