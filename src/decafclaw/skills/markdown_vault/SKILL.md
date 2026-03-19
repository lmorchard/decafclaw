---
name: markdown_vault
description: "Section-aware markdown tools for daily notes and to-do lists. Adds line-numbered section display, cross-file line moves, and section manipulation to workspace tools. Use for navigating note files, migrating to-dos between daily pages, or managing sections. Triggers on: 'show my notes,' 'what's on my list,' 'move todos,' 'create today's note,' or references to daily pages."
---

# Markdown Vault — Section-Aware Note Tools

Tools for navigating and manipulating markdown notes organized by headings. Works alongside workspace tools — use workspace_read, workspace_edit, workspace_insert for basic file operations; these tools add section awareness and cross-file moves.

## Getting Started

1. Check your memory for the vault base path (e.g. `obsidian/main`). You'll pass it to `vault_daily_path`.
2. Use `vault_daily_path` to get the workspace-relative path for today's note.
3. Use `md_show` to see section structure and line numbers.
4. Use workspace tools (workspace_read, workspace_edit, workspace_replace_lines, workspace_insert) for reading and editing file content.

## Concepts

- **Workspace-relative paths**: all tools take paths relative to the workspace root (e.g. `obsidian/main/journals/2026/2026-03-19.md`). Use `vault_daily_path` to compute daily note paths.
- **Line numbers**: `md_show` displays every line with its absolute line number. Use these numbers with workspace tools and `md_move_lines`.
- **Section path**: headings form a navigable tree. Use slash-separated paths like `today`, `notes/standup`. Case-insensitive, wiki-link-aware (`[[someday]]` matches `someday`).

## Available Tools

### `vault_daily_path` — Get daily journal path
Returns the workspace-relative path for a daily journal file. Pass `base_path` from memory.

### `md_show` — Show sections with line numbers
Without `section`: shows document outline (all headings with line numbers).
With `section`: shows that section's content with line numbers on every line.

### `md_move_lines` — Move lines between files
Move specific lines (by number) from one file to a section in another file. Use `md_show` first to see line numbers. Lines are appended to the target section.

### `md_section` — Section operations
Add, remove, rename, or move sections. Actions: `add`, `remove`, `rename`, `move`.

### `md_create` — Create file from template
Create a new markdown file, optionally from a template. `{{date}}` in templates is replaced with today's date.

## Using Workspace Tools

For basic file operations, use the standard workspace tools:

| Task | Workspace Tool |
|------|---------------|
| Read a file | `workspace_read` |
| Edit a line (check/uncheck, change text) | `workspace_replace_lines` |
| Insert a new line/item | `workspace_insert` |
| Append to end of file | `workspace_append` |
| List files | `workspace_list` |
| Search files | `workspace_search` |

**To check off a to-do item**: use `md_show` to find the line number, then `workspace_replace_lines` to change `- [ ]` to `- [x]`.

**To add a task**: use `md_show` to find the section's last line number, then `workspace_insert` at the next line.

## Common Workflows

### "What's on my list today?"
1. `vault_daily_path(base_path="obsidian/main")` → get today's path
2. `md_show(file=path, section="today")` → see items with line numbers

### "Check off X"
1. `md_show` to find the line number
2. `workspace_replace_lines(path, start_line=N, end_line=N, content="- [x] ...")` to toggle the checkbox

### "Add a task to today"
1. `md_show` to see the section and find where to insert
2. `workspace_insert(path, line_number=N, content="- [ ] new task\n")`

### "Move unchecked items from yesterday to today"
1. `vault_daily_path(base_path="obsidian/main", offset=-1)` → yesterday's path
2. `vault_daily_path(base_path="obsidian/main")` → today's path
3. `md_show(yesterday, section="today")` → see items with line numbers
4. Identify unchecked items (lines with `- [ ]`)
5. `md_move_lines(from_file=yesterday, to_file=today, to_section="today", lines="6,7,11")`

### "Create today's daily note"
1. `vault_daily_path(base_path="obsidian/main")` → get path
2. `md_create(file=path, template="obsidian/main/templates/daily.md")`

## Daily Notes

Daily journal files live at `{base_path}/journals/YYYY/YYYY-MM-DD.md`. They combine to-do lists, meeting notes, and general journaling, organized by heading sections. A template typically exists at `{base_path}/templates/daily.md`.

## Safety Protocols

- **Append-only by default.** Creating new files and adding items is fine. But **do not delete items, sections, or files without explicit user confirmation**.
- **Protected directories.** Some directories (e.g. `blog/`) should never be written to without a direct command from the user.
