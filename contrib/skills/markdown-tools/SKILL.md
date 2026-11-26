---
name: markdown-tools
description: Semantic Markdown structure tools for workspace files. Read and replace heading sections, manipulate ASCII tables as row objects, and manage checklist items without brittle text surgery.
user-invocable: true
argument-hint: "[path] [heading-or-section]"
allowed-tools: markdown_get_section, markdown_replace_section, markdown_table_read, markdown_table_append, markdown_table_update, markdown_checklist_get, markdown_checklist_toggle
context: inline
---

# Markdown Tools

Structured helpers for Markdown files in the workspace.

Use this skill when the target is a Markdown heading subtree, an ASCII table, or
checklist items. The tools resolve paths inside the workspace, parse structure
with `markdown-it-py`, and apply deterministic edits through the workspace file
lock.

## Available Tools

- `markdown_get_section(path, heading)`
  Returns the matching heading section, including nested subsections, up to the
  next heading of equal or higher rank.
- `markdown_replace_section(path, heading, content)`
  Replaces the matched section body while preserving the heading itself and the
  rest of the document.
- `markdown_table_read(path, section)`
  Reads the single ASCII markdown table inside a section and returns row objects.
- `markdown_table_append(path, section, rows)`
  Appends row objects to the table in a section and re-renders column widths.
- `markdown_table_update(path, section, match, updates)`
  Updates rows whose cells match the given key/value pairs and re-renders the
  table cleanly.
- `markdown_checklist_get(path, section=None, status="all")`
  Returns structured checklist items from the whole file or one section.
- `markdown_checklist_toggle(path, text, checked=True)`
  Toggles the single matching checklist item by task text.

## Notes

- `heading` / `section` can be either the visible heading text (`"Plan"`) or
  the full ATX heading string (`"## Plan"`).
- Table tools expect exactly one markdown table in the selected section.
- Checklist toggles match task text exactly. If more than one item has the same
  text, the tool returns an error instead of guessing.
