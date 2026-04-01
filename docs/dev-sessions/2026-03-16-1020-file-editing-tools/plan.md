# File Editing Tools — Implementation Plan

## Overview

Seven new/enhanced tools in `workspace_tools.py`, built incrementally. Each step adds one tool (or enhances one), its tool definition, registry wiring, and tests. Steps are ordered so each builds on the previous — search tools first (they help verify editing tools), then editing tools from simplest to most complex.

All tools follow the existing pattern: sync functions, `ctx` first param, use `_resolve_safe()` for sandboxing, return strings (or error strings starting with `[error:`).

## File Inventory

- **Modify:** `src/decafclaw/tools/workspace_tools.py` — all new tool functions + definitions
- **Modify:** `tests/test_workspace_tools.py` — all new tests
- **No changes to `__init__.py`** — new tools are added to the existing `WORKSPACE_TOOLS` and `WORKSPACE_TOOL_DEFINITIONS` dicts/lists in `workspace_tools.py`, which are already imported

---

## Step 1: Enhance `workspace_read` with `start_line` / `end_line`

### Context
`workspace_read` currently returns full file contents as plain text. We need to add optional `start_line` and `end_line` parameters for partial reads, and include line numbers in the output so the agent can reference specific lines for subsequent edit operations.

### Prompt

In `src/decafclaw/tools/workspace_tools.py`:

1. Modify `tool_workspace_read(ctx, path, start_line=None, end_line=None)` to:
   - Read the file content as before
   - Split into lines
   - If `start_line` and/or `end_line` are provided (1-based, inclusive), slice to that range
   - Format output with line numbers: `{line_number}| {content}` (padded line numbers for alignment)
   - When partial, include a header like `Lines {start}-{end} of {total}:`
   - When full file, still include line numbers for consistency

2. Update the `workspace_read` tool definition in `WORKSPACE_TOOL_DEFINITIONS` to add optional `start_line` and `end_line` integer parameters with clear descriptions. Mention that both are 1-based and inclusive, and that omitting both returns the full file.

3. In `tests/test_workspace_tools.py`, add tests:
   - `test_read_with_line_numbers` — full read includes line numbers
   - `test_read_start_line` — start_line only, returns from that line to end
   - `test_read_end_line` — end_line only, returns from start to that line
   - `test_read_line_range` — both start_line and end_line
   - `test_read_line_range_header` — partial reads show "Lines X-Y of Z" header
   - `test_read_out_of_range` — end_line beyond file length just returns to end (no error)

4. Verify existing tests still pass — the line-number format change will break `test_write_and_read` and `test_write_creates_dirs` which check for exact content. Update those to account for line-numbered output.

5. Run `make lint && make test`.

---

## Step 2: `workspace_append`

### Context
Simplest new tool. Appends content to a file, creating it if it doesn't exist. No line-number logic needed. Good warm-up before the more complex editing tools.

### Prompt

In `src/decafclaw/tools/workspace_tools.py`:

1. Add `tool_workspace_append(ctx, path, content)`:
   - Resolve path with `_resolve_safe()`
   - Create parent dirs if needed
   - If file exists, read current content and check if it ends with a newline — if not, prepend a newline to the appended content (prevents joining with last line)
   - Append content to the file
   - Return confirmation: `Appended {len(content)} characters to {path}`
   - Handle standard errors (outside workspace, permission denied)

2. Add to `WORKSPACE_TOOLS` dict and add tool definition to `WORKSPACE_TOOL_DEFINITIONS` with clear description: "Append content to the end of a file. Creates the file (and parent directories) if it doesn't exist."

3. Tests:
   - `test_append_creates_file` — append to nonexistent file creates it
   - `test_append_to_existing` — content is added at end
   - `test_append_adds_newline` — if existing file doesn't end with newline, one is added before the appended content
   - `test_append_escape_blocked` — path traversal rejected

4. Run `make lint && make test`.

---

## Step 3: `workspace_edit`

### Context
The primary editing tool — exact string replacement, proven pattern from Claude Code. This is the most important tool in the set. Must handle: unique match (success), no match (error), multiple matches (error unless `replace_all=true`).

### Prompt

In `src/decafclaw/tools/workspace_tools.py`:

1. Add `tool_workspace_edit(ctx, path, old_text, new_text, replace_all=False)`:
   - Resolve path, read file content
   - Count occurrences of `old_text` in content
   - If count == 0: return `[error: text not found in {path}. Make sure old_text matches exactly, including whitespace and indentation.]`
   - If count > 1 and not replace_all: return `[error: found {count} matches in {path}. Use replace_all=true for bulk replacement, or provide more surrounding context to make old_text unique.]`
   - Replace (once or all) and write back
   - Return confirmation: `Edited {path}: replaced {count} occurrence(s)`

2. Add to `WORKSPACE_TOOLS` and `WORKSPACE_TOOL_DEFINITIONS`. Description should emphasize: exact string match, fails if ambiguous, use replace_all for bulk. Include clear parameter descriptions.

3. Tests:
   - `test_edit_simple` — replace a unique string, verify file content
   - `test_edit_not_found` — returns clear error
   - `test_edit_ambiguous` — multiple matches without replace_all returns error with count
   - `test_edit_replace_all` — multiple matches with replace_all=true succeeds
   - `test_edit_preserves_rest` — content outside the match is unchanged
   - `test_edit_multiline` — old_text and new_text spanning multiple lines
   - `test_edit_escape_blocked` — path traversal rejected
   - `test_edit_nonexistent_file` — returns file not found error

4. Run `make lint && make test`.

---

## Step 4: `workspace_insert`

### Context
Line-based insertion. Depends on understanding line numbers, which workspace_read now provides. Insert at a specific line, pushing existing content down.

### Prompt

In `src/decafclaw/tools/workspace_tools.py`:

1. Add `tool_workspace_insert(ctx, path, line_number, content)`:
   - Resolve path, read file, split into lines
   - Validate line_number: must be >= 1 and <= len(lines) + 1 (inserting after the last line is valid)
   - Insert content at the specified position (convert 1-based to 0-based index)
   - Write back joined with newlines
   - Return: `Inserted {n} line(s) at line {line_number} in {path}`

2. Add to `WORKSPACE_TOOLS` and `WORKSPACE_TOOL_DEFINITIONS`. Description: "Insert text at a specific line number, pushing existing content down. Line numbers are 1-based."

3. Tests:
   - `test_insert_at_beginning` — line_number=1 inserts before all content
   - `test_insert_at_middle` — content appears at correct position
   - `test_insert_at_end` — line_number = total_lines + 1 appends
   - `test_insert_invalid_line` — line_number 0 or beyond end+1 returns error
   - `test_insert_multiline_content` — inserting multiple lines
   - `test_insert_escape_blocked`

4. Run `make lint && make test`.

---

## Step 5: `workspace_replace_lines`

### Context
Replace a range of lines with new content. Covers "rewrite this function" and "delete these lines" (empty content) use cases.

### Prompt

In `src/decafclaw/tools/workspace_tools.py`:

1. Add `tool_workspace_replace_lines(ctx, path, start_line, end_line, content="")`:
   - Resolve path, read file, split into lines
   - Validate: start_line >= 1, end_line >= start_line, end_line <= len(lines)
   - Replace lines[start_line-1:end_line] with content (split into lines if non-empty)
   - Write back
   - If content is empty: return `Deleted lines {start_line}-{end_line} from {path}`
   - Else: return `Replaced lines {start_line}-{end_line} with {n} line(s) in {path}`

2. Add to `WORKSPACE_TOOLS` and `WORKSPACE_TOOL_DEFINITIONS`. Description: "Replace a range of lines (1-based, inclusive) with new content. Pass empty content to delete lines."

3. Tests:
   - `test_replace_lines_basic` — replace a range, verify content
   - `test_replace_lines_delete` — empty content removes the lines
   - `test_replace_lines_expand` — replacing 2 lines with 5 lines works
   - `test_replace_lines_shrink` — replacing 5 lines with 2 lines works
   - `test_replace_lines_invalid_range` — start > end, out of bounds
   - `test_replace_lines_escape_blocked`

4. Run `make lint && make test`.

---

## Step 6: `workspace_search`

### Context
Regex search across files. This is the workspace-wide grep equivalent. More complex than the editing tools — needs to walk directories, filter by glob, handle binary files gracefully, and format output with line numbers and context.

### Prompt

In `src/decafclaw/tools/workspace_tools.py`:

1. Add `tool_workspace_search(ctx, pattern, path=".", glob="*", context_lines=2)`:
   - Resolve base path with `_resolve_safe()`
   - Compile regex pattern (return clear error if invalid regex)
   - Walk the directory tree (or just search one file if path is a file)
   - Filter files by glob pattern using `fnmatch`
   - For each matching file, search line by line
   - Collect matches with `context_lines` lines of surrounding context
   - Format output grouped by file: file path header, then matching lines with line numbers, context lines prefixed with space, match lines prefixed with `>`
   - Skip binary files (try UTF-8 decode, skip on failure)
   - Cap results to prevent huge outputs (e.g., max 50 matches total, with a note if truncated)
   - Return "(no matches)" if nothing found

2. Add to `WORKSPACE_TOOLS` and `WORKSPACE_TOOL_DEFINITIONS`. Description: "Search for a regex pattern across files in the workspace. Returns matching lines with line numbers and surrounding context. Use the glob parameter to filter file types (e.g. '*.py')."

3. Tests:
   - `test_search_basic` — find a string in a file
   - `test_search_regex` — regex pattern works (e.g., `\d+`)
   - `test_search_multiple_files` — results from multiple files, grouped
   - `test_search_glob_filter` — glob limits which files are searched
   - `test_search_context_lines` — surrounding context appears
   - `test_search_single_file` — path pointing to a specific file works
   - `test_search_no_matches` — returns "(no matches)"
   - `test_search_invalid_regex` — returns clear error
   - `test_search_escape_blocked`

4. Run `make lint && make test`.

---

## Step 7: `workspace_glob`

### Context
Find files by name pattern. Simpler than workspace_search — just walks the tree and matches filenames. Last tool because it's independent of the others.

### Prompt

In `src/decafclaw/tools/workspace_tools.py`:

1. Add `tool_workspace_glob(ctx, pattern, path=".")`:
   - Resolve base path with `_resolve_safe()`
   - Use `Path.rglob()` with the pattern to find matching files
   - Return paths relative to the workspace root, one per line
   - Include file size in parentheses for files
   - Cap results (e.g., max 200 entries, with a note if truncated)
   - Return "(no matches)" if nothing found

2. Add to `WORKSPACE_TOOLS` and `WORKSPACE_TOOL_DEFINITIONS`. Description: "Find files by name/glob pattern, recursively. Returns matching file paths relative to workspace root. Useful for finding files by extension or name pattern."

3. Tests:
   - `test_glob_basic` — find files by extension
   - `test_glob_nested` — finds files in subdirectories
   - `test_glob_specific_name` — exact filename match
   - `test_glob_no_matches` — returns "(no matches)"
   - `test_glob_with_subpath` — path param limits search scope
   - `test_glob_escape_blocked`

4. Run `make lint && make test`.

---

## Step 8: Update tool descriptions in system prompt

### Context
Now that all tools exist and are tested, update the agent's system prompt guidance (AGENT.md) so the LLM knows how to use the new tools effectively. Also update docs.

### Prompt

1. Check `data/*/AGENT.md` (or wherever system prompt tool guidance lives) for any references to workspace_read/workspace_write workflow. Add guidance for the new tools — when to use search vs read, when to use edit vs write, the search→edit workflow.

2. Update `CLAUDE.md`:
   - Add new tool module info to key files if needed
   - Note the new tools in conventions

3. Update `README.md` tool table with the new tools.

4. Update `docs/` — check if there's a tools doc page that needs the new tools listed.

5. Run `make lint && make test` one final time to ensure everything is clean.

---

## Execution Notes

- Each step is one commit: implement, lint, test, commit
- Steps 1-7 are the code. Step 8 is docs cleanup
- Steps are ordered by dependency: read enhancement first (line numbers used by later tools), then append (simplest), edit (most important), insert, replace_lines, then search tools last (most complex, no dependencies from editing tools)
- All tools go in the same file — no new modules, no changes to `__init__.py`
