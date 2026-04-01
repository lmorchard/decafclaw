# File Editing Tools — Spec

## Problem

The agent's only file modification tools are `workspace_read` (full file) and `workspace_write` (full file). For surgical edits, the agent must read the entire file, mentally modify it, and rewrite the whole thing — which is token-expensive, error-prone (especially for large files), and produces hard-to-verify changes.

Beyond editing, the agent lacks tools for navigating the workspace efficiently. Finding files requires manually walking directories with `workspace_list`. Searching file contents requires reading entire files. These gaps make the agent slow and token-hungry when working with anything beyond trivial file structures.

## Goal

Add core workspace tools for granular file editing and efficient workspace navigation. These should be purpose-built for how LLMs work — clear parameters, unambiguous behavior, helpful error messages — rather than wrapping unix tools that require regex/escaping expertise.

The target is rough parity with Claude Code's file toolkit: read (partial), write, edit, search content, find files by pattern.

## Enhanced Existing Tools

### `workspace_read` — add `start_line` / `end_line` params
Optional parameters for partial file reads. Both are 1-based, inclusive. Omit both for full file (backward compatible). Output includes line numbers so the agent knows where it is in the file.

## New Editing Tools

### `workspace_edit(path, old_text, new_text, replace_all=false)`
Exact string replacement. Finds `old_text` in the file and replaces with `new_text`. By default fails if `old_text` is not found or matches multiple locations (ambiguous). Set `replace_all=true` for intentional bulk replacement. This is the primary editing tool — proven pattern from Claude Code, Cursor, etc.

### `workspace_insert(path, line_number, content)`
Insert text at a specific line number (pushing existing content down). 1-based. Useful for adding imports, new functions, config entries.

### `workspace_replace_lines(path, start_line, end_line, content)`
Replace a range of lines (1-based, inclusive) with new content. Useful for rewriting a function body, replacing a block. Pass empty content to delete lines.

### `workspace_append(path, content)`
Append content to the end of a file (creates if missing). Simpler than insert-at-end for logs, journal entries, config additions.

## New Search/Navigation Tools

### `workspace_search(pattern, path=".", glob="*", context_lines=2)`
Search for a regex pattern across files in the workspace. Returns matching lines with line numbers and surrounding context, grouped by file. Recursive by default. The `glob` param filters which files to search (e.g. `"*.py"`, `"*.md"`). Subsumes single-file search — just pass a specific file path.

### `workspace_glob(pattern, path=".")`
Find files by name/glob pattern, recursively. Returns matching file paths relative to workspace root. Useful for "find all *.py files", "find files named config*", navigating unfamiliar directory structures.

## Tool Summary vs Claude Code

| Claude Code | DecafClaw | Status |
|---|---|---|
| Read (offset/limit) | `workspace_read` (start_line/end_line) | Enhance |
| Write | `workspace_write` | Exists |
| Edit | `workspace_edit` | New |
| Grep | `workspace_search` | New |
| Glob | `workspace_glob` | New |
| Bash | `shell_exec` | Exists |
| — | `workspace_insert` | New (no CC equivalent) |
| — | `workspace_replace_lines` | New (no CC equivalent) |
| — | `workspace_append` | New (no CC equivalent) |

## Design Principles

- All tools sandboxed via existing `_resolve_safe()`
- Return line numbers in output so the agent can chain search → edit
- Error messages designed for LLM consumption (clear, actionable)
- Core tools, not a skill — file editing is fundamental
- Follow existing patterns: `ctx` first param, sync functions, registered in `__init__.py`
- All new tools in `workspace_tools.py` alongside existing workspace tools — unified `workspace_*` namespace
