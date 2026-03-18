---
name: markdown_vault
description: "Read and edit markdown notes in the workspace. Handles section navigation, checklist management (check/uncheck/add/delete items), content manipulation (append/prepend), and #tag-based organization. Use for any task involving the user's notes, to-do lists, daily pages, or tagged items. Triggers on: 'check off,' 'add to my list,' 'what's on my list,' 'mark done,' 'add a task,' 'show my notes,' 'what's in my today section,' 'find tagged,' 'tag this,' or references to note files."
---

# Markdown Vault — Note Editing Tools

Tools for reading and surgically editing markdown notes organized by headings, checklists, and tags.

## Concepts

- **Vault base path**: The vault may live in a subdirectory of the workspace (e.g. `obsidian/main`). Use `vault_set_path` at the start of a conversation to set this. Once set, all file paths in other vault tools resolve relative to the vault base — you don't need to include the base in every path. Use `vault_get_path` to check the current setting.
- **File**: A markdown file within the vault, addressed by relative path (e.g. `journals/2026/2026-03-17.md`). Paths are relative to the vault base path if set, otherwise relative to the workspace root.
- **Section path**: Slash-separated heading path within a file (e.g. `today`, `notes/standup`). Case-insensitive, wiki-link-aware (`[[someday]]` matches `someday`)
- **Item selection**: Checklist items within a section are selected by `match` (substring) or `index` (0-based position, negatives ok). You must provide one or the other.
- **Checklist format**: Items are `- [ ] text` (unchecked) or `- [x] text` (checked). When creating new items with `vault_append`, `vault_prepend`, or `vault_insert`, always use this format — e.g. `- [ ] buy groceries`. Plain text without the checkbox prefix will be inserted as prose, not a checklist item.
- **Tags**: `#hashtags` anywhere in a line — `#word`, `#CamelCase`, or `#multi-word-tag`. Tags can appear on checklist items or plain prose lines. They're used for cross-section categorization (e.g. `#storyidea`, `#research`, `#urgent`).
- **Prose lines**: Not all content in a section is checklist items. Sections can contain plain prose paragraphs, indented sub-items, and other markdown. Tags work on both checklist items and prose lines — use `match` to select a prose line by substring, or `index` to select a checklist item by position.

## Getting Started — REQUIRED before any other vault tool

**You MUST set the vault base path before using any other vault tools.** Follow these steps in order — do NOT skip to asking the user:

1. Call `vault_get_path` — the path may already be set from earlier in this conversation. If it returns a path, you're done.
2. If not set, call `memory_search("markdown vault base path")` to check if the user has told you the path before. **You MUST call memory_search — do not skip this step.**
3. If memory returns a vault path, call `vault_set_path` with that path.
4. **Only if memory_search returns nothing relevant**, ask the user where their vault is located.

**NEVER ask the user for the vault path without searching memory first.**

## Available Tools

### Vault path

#### `vault_set_path` — Set the vault base path
Set the subdirectory within the workspace where the vault lives (e.g. `obsidian/main`). **Call this before using other vault tools** if the vault isn't at the workspace root. All file paths in subsequent calls resolve relative to this base.

#### `vault_get_path` — Get the current vault base path
Returns the current vault base path, or indicates none is set (using workspace root).

### File operations

#### `vault_read` — Read an entire file
Read a markdown file's full content as text. Use when you need the whole file, not just a section.

#### `vault_create_file` — Create a new file
Create a new markdown file, optionally from a template. `{{date}}` in templates is replaced with today's date (ISO format). Will not overwrite existing files. Creates parent directories as needed.

#### `vault_daily_path` — Get a daily journal path
Returns the relative path for a daily journal file (e.g. `journals/2026/2026-03-17.md`). Accepts an optional date and offset (-1 for yesterday, 1 for tomorrow). **Always use this instead of constructing date paths manually.**

#### `vault_list` — List files in the vault
List markdown files and directories at a given path. Use to discover what notes exist.

### Navigation

#### `vault_show` — Show a section or document outline
With a section path: shows the heading and its content. Without: shows the document's heading outline (useful for navigating an unfamiliar file).

#### `vault_items` — List checklist items in a section
Returns indexed checklist items with their checked/unchecked state. Use the indices with other item tools.

#### `vault_find_items` — Search for items across sections
Search all sections in a file for checklist items matching a substring. Returns section name and item for each match. Useful when you know *what* you're looking for but not *where* it is.

### Checklist item operations

#### `vault_check` / `vault_uncheck` — Toggle a single item
Mark an item done or not done. Select by substring match or index.

#### `vault_bulk_check` / `vault_bulk_uncheck` — Toggle all items in a section
Mark all checklist items in a section done or not done.

#### `vault_append` / `vault_prepend` — Add content to a section
Append adds to the end of a section (before the next heading). Prepend adds right after the heading. Items are placed contiguously with existing items — no extra blank lines.

#### `vault_insert` — Insert at a specific position
Insert text at a specific item index within a section. Index 0 = before first item.

#### `vault_replace_item` — Edit a checklist item's text
Replace the text of a checklist item while preserving its checked/unchecked state.

#### `vault_move_item` — Move an item between sections
Move a checklist item from one section to another (same file). The item is appended to the target section.

#### `vault_move_items` — Bulk move items across files
Move multiple checklist items between files and/or sections in a single operation. Supports filters: `unchecked_only`, `checked_only`, or specific `indices`. **This is the primary tool for daily task migration** — e.g. moving unchecked items from yesterday's "today" section to today's "today" section.

#### `vault_delete` — Remove a checklist item
Delete by substring match or index.

### Tag operations

#### `vault_tags` — List all tags in a file
Discover all unique `#tags` in a document, sorted alphabetically.

#### `vault_tagged` — Find all lines with a given tag
Search the entire file for lines containing a specific `#tag`. Returns section name and line text. Works on both checklist items and plain prose lines.

#### `vault_add_tag` — Add a tag to a checklist item
Append a `#tag` to an item. Idempotent — won't duplicate if already present.

#### `vault_remove_tag` — Remove a tag from a checklist item
Remove a `#tag` from an item. Case-insensitive matching.

### Section operations

#### `vault_add_section` — Add a new section
Create a new heading (with optional initial content). Position with `after`, `before`, or `parent` (insert as child). Omit all three to append at end of document.

#### `vault_rename_section` — Rename a section heading
Change a heading's text without affecting level, content, or position.

#### `vault_replace_section` — Replace a section's content
Overwrite a section's body while preserving the heading and any child sections.

#### `vault_remove_section` — Remove an entire section
Remove a heading and all its content, including any child sections. **Always confirm with the user first.**

#### `vault_move_section` — Move a section to a new position
Relocate a section (heading + content + children) within the document. Position with `after` or `before`. Omit both to move to end.

## Safety Protocols

- **Append-only by default.** Creating new files and appending content is fine. Checking/unchecking items is fine. But **do not delete items, sections, or files without explicit user confirmation** for each change.
- **Protected directories.** Some directories (e.g. `blog/`, publishing pipelines) should never be written to without a direct, explicit, per-instance command from the user. When in doubt, ask.
- **No bulk operations without confirmation.** Moving, renaming, or batch-editing multiple files requires explicit approval.

## Daily Notes

Daily journal files live at `journals/YYYY/YYYY-MM-DD.md`. They combine to-do lists, meeting notes, and general journaling, organized by heading sections. A template exists at `templates/daily.md`.

Common daily note tasks:
- **Append items** to the current day's note (to-dos, meeting notes, quick thoughts)
- **Check off items** as they're completed
- **Someday sweep**: On request, scan recent daily journals for items under `# [[someday]]` headings and consolidate them into `pages/someday.md`

## Common Workflows

### "Check off X on my list"
1. `vault_find_items` to locate the item (if section unknown), or `vault_items` if you know the section
2. `vault_check` with the match text or index

### "Add a task to today"
1. `vault_append` on the target section with `- [ ] task description`

### "Move unchecked items from yesterday to today"
1. `vault_daily_path` with offset=-1 → get yesterday's file path
2. `vault_daily_path` → get today's file path
3. `vault_move_items` from yesterday's file "today" section to today's file "today" section, with `unchecked_only=true`

### "Move today/tomorrow items to the next day"
1. `vault_daily_path` → today's file
2. `vault_daily_path` with offset=1 → tomorrow's file
3. `vault_move_items` from today's "tomorrow" section to tomorrow's "today" section

### "What's tagged #research?"
1. `vault_tagged` with tag `research` — searches the whole file

### "Create today's daily note"
1. `vault_daily_path` → get the path
2. `vault_create_file` with that path and template `templates/daily.md`

### "Sweep someday items to someday.md"
1. `vault_daily_path` → today's file
2. `vault_show` the `someday` section
3. `vault_move_items` from today's file "someday" section to `pages/someday.md` "someday" section (or use `vault_read` + `vault_prepend` if someday.md has a flat structure)

## Choosing the Right Tool

| Task | Tool |
|------|------|
| Set vault location within workspace | `vault_set_path` |
| Check current vault location | `vault_get_path` |
| Get a daily journal path | `vault_daily_path` |
| Read an entire file | `vault_read` |
| Create a new file (or from template) | `vault_create_file` |
| See what files exist | `vault_list` |
| See what sections a file has | `vault_show` (no section) |
| Read a section's content | `vault_show` (with section) |
| See checklist items with indices | `vault_items` |
| Find items by text across sections | `vault_find_items` |
| Mark something done | `vault_check` |
| Mark something not done | `vault_uncheck` |
| Mark all items done/not done | `vault_bulk_check` / `vault_bulk_uncheck` |
| Edit an item's text | `vault_replace_item` |
| Move an item to another section | `vault_move_item` |
| Bulk move items across files | `vault_move_items` |
| Add a new task/item | `vault_append` or `vault_prepend` |
| Add at a specific position | `vault_insert` |
| Remove an item (ask first!) | `vault_delete` |
| List all tags in a file | `vault_tags` |
| Find lines with a tag | `vault_tagged` |
| Tag an item | `vault_add_tag` |
| Untag an item | `vault_remove_tag` |
| Add a new section | `vault_add_section` |
| Rename a section | `vault_rename_section` |
| Rewrite a section's content | `vault_replace_section` |
| Remove a section (ask first!) | `vault_remove_section` |
| Rearrange sections | `vault_move_section` |
