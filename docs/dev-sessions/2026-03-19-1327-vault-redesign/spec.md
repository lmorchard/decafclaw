# Vault Tools Redesign — Spec

## Status: Ready

## Background

The markdown vault skill has 29 tools with complex parameters (substring matching, nested section paths, separate check/uncheck/replace/tag operations). This causes failures with content containing markdown links, silent move failures, and parameter name confusion with deferred tool loading.

The vault lives in the workspace. Most vault operations (read, edit, insert, delete, list) overlap with existing workspace tools. The redesign strips the vault skill to its unique value — section awareness, daily paths, and cross-file moves — and delegates everything else to workspace tools.

## Goals

1. Reduce from 29 tools to 5
2. Line-number-based operations instead of substring matching
3. No vault base path resolution — all tools take workspace-relative paths (consistent with workspace tools)
4. Section-aware display with line numbers so the agent always has precise coordinates

## New Tool Set

### `vault_daily_path`

The only vault-path-aware tool. Returns the workspace-relative path for a daily journal file.

**Parameters:**
- `date` (string, optional) — ISO date (YYYY-MM-DD). Default: today.
- `offset` (int, optional) — Day offset (-1 = yesterday, 1 = tomorrow). Default: 0.

**Returns:** path like `obsidian/main/journals/2026/2026-03-19.md`

**Unchanged** from current implementation except: the vault base path must be stored in memory (the agent remembers it), and the tool reads it from `ctx.skill_data["vault_base_path"]`. If not set, returns an error telling the agent to save the base path to memory and set it via the tool's config.

Actually — **simplification**: the vault base path is ONLY used by this one tool. So instead of the set_path/get_path machinery, just add `base_path` as an optional parameter:

```
vault_daily_path(date=None, offset=0, base_path="obsidian/main")
```

The agent passes the base path from memory. No state management needed.

### `md_show`

Show a markdown file's section structure or a specific section's content, with line numbers.

**Parameters:**
- `file` (string, required) — workspace-relative path
- `section` (string, optional) — section path (e.g. "today", "notes/standup"). If omitted, shows document outline.

**Returns:**

Without section — outline with line numbers:
```
5: # today
12: # tonight
16: # tomorrow
18: # this week
27: # [[someday]]
29: # notes
31: ## standup
```

With section — full content with line numbers:
```
# today (line 5)
  6: - [ ] consider renovatebot as alternative to dependabot?
  7: - [ ] check out [onecli](https://github.com/onecli/onecli) for credential vault
  8: - [x] take a look at octonous for automations
  9:
 10: Some prose note here
 11: - [ ] Cancel personal claude?
```

Line numbers are absolute (file-wide), always shown. The agent uses these numbers with workspace tools (`workspace_edit`, `workspace_insert`, `workspace_replace_lines`) for editing, and with `md_move_lines` for moving.

Section path matching: case-insensitive, wiki-link-aware (`[[someday]]` matches `someday`). Slash-separated for nested sections (`notes/standup`).

### `md_move_lines`

Move specific lines from one file to another (or within the same file), inserting them at a target section.

**Parameters:**
- `from_file` (string, required) — source file (workspace-relative)
- `to_file` (string, required) — target file (workspace-relative)
- `to_section` (string, required) — target section path (lines appended to end of section)
- `lines` (string, required) — comma-separated line numbers to move (e.g. "6,7,11")

**Behavior:**
- Lines are removed from source and appended to the target section
- Lines are moved in the order specified
- Deletion happens in reverse order (highest line number first) to preserve line numbers
- Both files are saved after the operation
- Returns a summary: "Moved 3 line(s) from source.md to target.md/today"

This replaces `vault_move_items` and `vault_move_item`. No filters (unchecked_only, etc.) — the agent reads the section with `md_show`, sees line numbers and content, and picks exactly which lines to move.

### `md_section`

Section-level operations on a markdown file.

**Parameters:**
- `file` (string, required) — workspace-relative path
- `action` (string, required) — one of: `add`, `remove`, `rename`, `move`
- `section` (string, required for remove/rename/move) — section path
- `title` (string, required for add/rename) — heading text
- `level` (int, optional, default 1) — heading level for `add`
- `after` / `before` / `parent` (string, optional) — positioning for `add` and `move`

**Unchanged** logic from current section operations, just consolidated into one tool with an action parameter.

### `md_create`

Create a new markdown file from a template.

**Parameters:**
- `file` (string, required) — workspace-relative path for the new file
- `template` (string, optional) — workspace-relative path to template file
- `content` (string, optional) — initial content (if no template)

**Behavior:**
- `{{date}}` and `{{date:FORMAT}}` in templates replaced with today's ISO date
- Won't overwrite existing files
- Creates parent directories as needed

## What Gets Removed

All of these are replaced by workspace tools + the 5 new tools:

- `vault_set_path`, `vault_get_path` — no longer needed (daily_path takes base_path param, everything else uses workspace paths)
- `vault_read` → `workspace_read`
- `vault_list` → `workspace_list`
- `vault_items`, `vault_find_items` → `md_show` (items are just lines with checkboxes)
- `vault_check`, `vault_uncheck`, `vault_bulk_check`, `vault_bulk_uncheck` → `workspace_edit` or `workspace_replace_lines` (toggle checkbox by editing the line)
- `vault_append`, `vault_prepend`, `vault_insert` → `workspace_insert` or `workspace_append`
- `vault_delete` → line deletion via `workspace_replace_lines` with empty content
- `vault_replace_item` → `workspace_replace_lines`
- `vault_move_item` → `md_move_lines` with single line
- `vault_move_items` → `md_move_lines`
- `vault_add_section`, `vault_remove_section`, `vault_move_section`, `vault_rename_section`, `vault_replace_section` → `md_section`
- `vault_tags`, `vault_tagged`, `vault_add_tag`, `vault_remove_tag` → agent edits lines directly via workspace tools
- `vault_create_file` → `md_create`

## SKILL.md Updates

The SKILL.md needs a major rewrite to:
- Document the 5 new tools
- Explain that workspace tools are used for basic file operations
- Update workflow recipes (daily migration, etc.) to use the new tool set
- Remove the old 27-tool reference section

## Library Code

The Document/Section/ChecklistItem parsing classes in tools.py are still useful for `md_show`, `md_move_lines`, and `md_section`. They stay but are only used internally — no more exposing parsed checklist items or substring matching to the LLM.

## Migration

This is a breaking change for the tool API. Since skills are lazy-loaded and per-agent, the migration is:
1. Replace tools.py with the new 5-tool implementation
2. Rewrite SKILL.md
3. Update tests
4. Old tool names stop working immediately (they're no longer registered)

## Out of Scope

- Workspace tools changes (they already support everything needed)
- Vault base path state management (agent uses memory)
- Fuzzy section matching beyond wiki-links
