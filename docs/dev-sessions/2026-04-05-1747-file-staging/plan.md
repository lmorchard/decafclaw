# File Staging — Plan

## Overview

Two steps. Simple feature — two new tool functions following existing patterns.

---

## Step 1: Add push and pull tool functions + TOOL_DEFINITIONS

**What:** Add `tool_claude_code_push_file` and `tool_claude_code_pull_file` functions, register in TOOLS dict and TOOL_DEFINITIONS.

**Files:**
- `src/decafclaw/skills/claude_code/tools.py` — add `shutil` import, two tool functions, TOOLS entries, TOOL_DEFINITIONS entries
- Tests for both tools

**Details:**

1. Add `import shutil` at the top of tools.py.

2. Add `tool_claude_code_push_file(ctx, session_id, source_path, dest_name="")`:
   - Look up session, return error if not found
   - Resolve source_path relative to workspace, enforce sandbox with is_relative_to
   - Check source exists and is a file (not directory)
   - Resolve dest_name (default to basename of source_path) relative to session cwd, enforce sandbox
   - Create parent dirs for dest if needed
   - shutil.copy2(source, dest)
   - Touch session
   - Return ToolResult with status, source, dest, size_bytes

3. Add `tool_claude_code_pull_file(ctx, session_id, source_name, dest_path="")`:
   - Same pattern in reverse
   - Resolve source_name relative to session cwd, enforce sandbox
   - Check source exists and is a file
   - Resolve dest_path (default to basename of source_name) relative to workspace, enforce sandbox
   - Create parent dirs, copy, touch, return ToolResult

4. Add both to TOOLS dict and TOOL_DEFINITIONS.

5. Write tests using tmp_path fixtures — mock _config and _session_manager for sandbox testing.

---

## Step 2: Update SKILL.md + docs

**What:** Document the new tools in SKILL.md.

**Files:**
- `src/decafclaw/skills/claude_code/SKILL.md`
