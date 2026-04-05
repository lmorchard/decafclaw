# Claude Code Subagent Phase 2 — Plan

## Overview

Four steps, each building on the previous. Each step ends with lint + test + commit.

---

## Step 1: Context injection — Session.instructions + prompt assembly

**What:** Add `instructions` field to Session, `instructions` param to `claude_code_start`, `context` param to `claude_code_send`, and prompt assembly logic. Update TOOL_DEFINITIONS.

**Files:**
- `src/decafclaw/skills/claude_code/sessions.py` — add `instructions` field
- `src/decafclaw/skills/claude_code/tools.py` — add params, prompt assembly helper, wire into send

**Details:**

1. In `sessions.py`, add `instructions: str = ""` to the `Session` dataclass after `approved`.

2. In `tools.py`, add a helper function to assemble the prompt:
   ```python
   def _assemble_prompt(prompt: str, instructions: str = "", context: str = "") -> str:
       parts = []
       if instructions:
           parts.append(f"<instructions>\n{instructions}\n</instructions>")
       if context:
           parts.append(f"<context>\n{context}\n</context>")
       parts.append(prompt)
       return "\n\n".join(parts)
   ```

3. Add `instructions: str = ""` parameter to `tool_claude_code_start`. Pass it through to `manager.create()`, which needs to accept and store it on the Session.

4. Update `SessionManager.create()` in `sessions.py` to accept `instructions` and pass it to the Session constructor.

5. Add `context: str = ""` parameter to `tool_claude_code_send`.

6. In `tool_claude_code_send`, replace the raw `prompt` with `_assemble_prompt(prompt, session.instructions, context)` when building the prompt stream.

7. Update TOOL_DEFINITIONS:
   - `claude_code_start`: add `instructions` property
   - `claude_code_send`: add `context` property

8. Write tests for `_assemble_prompt` (all combinations of empty/non-empty instructions and context).

**Prompt:**

> In `src/decafclaw/skills/claude_code/sessions.py`:
> 1. Add `instructions: str = ""` to the `Session` dataclass, after the `approved` field.
> 2. Update `SessionManager.create()` to accept `instructions: str = ""` parameter and pass it to the Session constructor.
>
> In `src/decafclaw/skills/claude_code/tools.py`:
> 1. Add a module-level helper before `tool_claude_code_start`:
>    ```python
>    def _assemble_prompt(prompt: str, instructions: str = "", context: str = "") -> str:
>        """Build the full prompt with optional instructions and context preamble."""
>        parts = []
>        if instructions:
>            parts.append(f"<instructions>\n{instructions}\n</instructions>")
>        if context:
>            parts.append(f"<context>\n{context}\n</context>")
>        parts.append(prompt)
>        return "\n\n".join(parts)
>    ```
>
> 2. Add `instructions: str = ""` parameter to `tool_claude_code_start` (after `setup_command`). Pass it to `manager.create(instructions=instructions)`.
>
> 3. Add `context: str = ""` parameter to `tool_claude_code_send` (after `prompt`).
>
> 4. In `tool_claude_code_send`, where the prompt_stream is built (around line 350), replace the raw `prompt` with `_assemble_prompt(prompt, session.instructions, context)`:
>    ```python
>    full_prompt = _assemble_prompt(prompt, session.instructions, context)
>    async def prompt_stream():
>        yield {
>            "type": "user",
>            "message": {"role": "user", "content": full_prompt},
>            ...
>        }
>    ```
>
> 5. Update `claude_code_start` TOOL_DEFINITIONS to add:
>    ```python
>    "instructions": {
>        "type": "string",
>        "description": (
>            "Persistent instructions prepended to every send in this session. "
>            "Use for project conventions, coding style, constraints."
>        ),
>    },
>    ```
>
> 6. Update `claude_code_send` TOOL_DEFINITIONS to add:
>    ```python
>    "context": {
>        "type": "string",
>        "description": (
>            "Per-task context prepended to this send only. "
>            "Use for relevant specs, vault pages, conversation excerpts."
>        ),
>    },
>    ```
>
> 7. Write tests in `tests/test_claude_code_context.py`:
>    - `test_assemble_prompt_both`: instructions + context + prompt → correct XML tags and ordering
>    - `test_assemble_prompt_instructions_only`: just instructions + prompt
>    - `test_assemble_prompt_context_only`: just context + prompt
>    - `test_assemble_prompt_neither`: prompt only → no tags, prompt unchanged
>    - `test_session_stores_instructions`: create session with instructions, verify field is set
>
> Run `make lint` and `make test`.

---

## Step 2: Diff capture helper

**What:** Add an async helper function that captures git diff in a session's cwd. Self-contained, tested independently before wiring into send.

**Files:**
- `src/decafclaw/skills/claude_code/tools.py` — add `_capture_git_diff` helper

**Details:**

1. Add `async def _capture_git_diff(cwd: str, baseline_ref: str | None) -> str | None`:
   - If `baseline_ref` is None, return None (not a git repo or no commits)
   - Run `git diff <baseline_ref>` in cwd — catches committed changes since baseline
   - Run `git diff` in cwd — catches unstaged changes to tracked files
   - Run `git ls-files --others --exclude-standard` in cwd — catches new untracked files
   - Combine results: committed diff + unstaged diff + untracked file list
   - For untracked files, append a section: `\nNew untracked files:\n  path/to/file1\n  path/to/file2`
   - If all three are empty, return empty string (no changes)
   - On any git command failure, log warning and return None
   - Whole function wrapped in try/except for robustness

2. Add `async def _get_git_head(cwd: str) -> str | None`:
   - Run `git rev-parse HEAD` in cwd
   - Return the hash string on success, None on failure (not a repo, empty repo, etc.)

3. Write tests:
   - Test `_get_git_head` in a real tmp git repo (init, commit, verify hash)
   - Test `_get_git_head` in a non-git directory (returns None)
   - Test `_capture_git_diff` with a modified file (shows diff)
   - Test `_capture_git_diff` with a new untracked file (listed)
   - Test `_capture_git_diff` with a committed change (shows diff from baseline)
   - Test `_capture_git_diff` with None baseline (returns None)

**Prompt:**

> In `src/decafclaw/skills/claude_code/tools.py`, add two helper functions after the existing `_run_setup_command`:
>
> 1. `async def _get_git_head(cwd: str) -> str | None`:
>    - Run `git -C {cwd} rev-parse HEAD` via `asyncio.create_subprocess_exec`
>    - On success (returncode 0), return stdout stripped
>    - On failure, return None (not a git repo, empty repo, etc.)
>
> 2. `async def _capture_git_diff(cwd: str, baseline_ref: str | None) -> str | None`:
>    - If `baseline_ref` is None, return None
>    - Run three git commands in the cwd, all via `asyncio.create_subprocess_exec`:
>      a. `git diff <baseline_ref>` — committed changes since baseline
>      b. `git diff` — unstaged changes to tracked files
>      c. `git ls-files --others --exclude-standard` — new untracked files
>    - For each, capture stdout as string. If a command fails, log warning and treat as empty.
>    - Build the result:
>      - Start with committed diff (if non-empty)
>      - Append unstaged diff (if non-empty and different from committed diff)
>      - If untracked files exist, append `\nNew untracked files:\n` followed by each path indented with two spaces
>    - If everything is empty, return "" (no changes detected)
>    - Wrap the entire function in try/except, returning None on unexpected errors
>
> Write tests in `tests/test_claude_code_diff.py`:
> - `test_get_git_head_returns_hash(tmp_path)`: init a git repo, make a commit, verify `_get_git_head` returns a 40-char hex string
> - `test_get_git_head_not_a_repo(tmp_path)`: verify returns None for a plain directory
> - `test_capture_diff_modified_file(tmp_path)`: init repo, commit a file, modify it, verify diff contains the change
> - `test_capture_diff_new_untracked_file(tmp_path)`: init repo, commit, create a new file (don't add), verify "New untracked files" section
> - `test_capture_diff_committed_change(tmp_path)`: init repo, commit file, save head, make another commit, verify diff from baseline shows the change
> - `test_capture_diff_no_baseline(tmp_path)`: verify returns None when baseline_ref is None
> - `test_capture_diff_no_changes(tmp_path)`: init repo, commit, verify returns "" when nothing changed
>
> All tests should use real git repos in tmp_path (via `git init`, `git add`, `git commit` subprocess calls).
>
> Run `make lint` and `make test`.

---

## Step 3: Wire diff capture into claude_code_send

**What:** Call `_get_git_head` before SDK streaming and `_capture_git_diff` after. Add `include_diff` parameter. Pass diff to `build_data()`.

**Files:**
- `src/decafclaw/skills/claude_code/tools.py` — wire into send flow
- `src/decafclaw/skills/claude_code/output.py` — add `diff` param to `build_data()`

**Details:**

1. In `output.py`, add `diff: str | None = None` parameter to `build_data()`. Include it in the returned dict.

2. In `tools.py`, add `include_diff: bool = True` parameter to `tool_claude_code_send`.

3. Before the SDK streaming starts (before the `prompt_stream` definition), capture baseline:
   ```python
   baseline_ref = None
   if include_diff:
       baseline_ref = await _get_git_head(session.cwd)
   ```

4. After SDK streaming completes (after `session.send_count += 1`), capture diff:
   ```python
   diff = None
   if include_diff:
       diff = await _capture_git_diff(session.cwd, baseline_ref)
   ```

5. Pass `diff=diff` to `logger.build_data()`.

6. Also add `diff=None` to the `_send_error_data` helper.

7. Update `claude_code_send` TOOL_DEFINITIONS to add:
   ```python
   "include_diff": {
       "type": "boolean",
       "description": "Capture git diff of changes made during this send (default true)",
   },
   ```

**Prompt:**

> 1. In `src/decafclaw/skills/claude_code/output.py`, modify `build_data()`:
>    - Add `diff: str | None = None` parameter
>    - Add `"diff": diff` to the returned dict
>
> 2. In `src/decafclaw/skills/claude_code/tools.py`:
>    - Add `include_diff: bool = True` parameter to `tool_claude_code_send` (after `context`)
>    - Before the `prompt_stream` definition (around line 348), add:
>      ```python
>      baseline_ref = None
>      if include_diff:
>          baseline_ref = await _get_git_head(session.cwd)
>      ```
>    - After `manager.touch(session_id)` (around line 398), add:
>      ```python
>      diff = None
>      if include_diff:
>          diff = await _capture_git_diff(session.cwd, baseline_ref)
>      ```
>    - Pass `diff=diff` to the `logger.build_data()` call
>    - Add `"diff": None` to the `_send_error_data` helper function
>    - Update `claude_code_send` TOOL_DEFINITIONS to add:
>      ```python
>      "include_diff": {
>          "type": "boolean",
>          "description": "Capture git diff of changes made during this send (default true)",
>      },
>      ```
>
> 3. Update `tests/test_claude_code_output.py`:
>    - Update `test_build_data_shape` to pass `diff="some diff"` and verify it appears in the result
>    - Update `test_build_data_defaults` to verify `diff` is None by default
>
> Run `make lint` and `make test`.

---

## Step 4: Update SKILL.md + docs

**What:** Update skill documentation with new parameters, diff output, git guidance, and context injection workflow.

**Files:**
- `src/decafclaw/skills/claude_code/SKILL.md`
- `CLAUDE.md` (if conventions need noting)

**Prompt:**

> Update `src/decafclaw/skills/claude_code/SKILL.md`:
>
> 1. Update `claude_code_start` tool section:
>    - Add `instructions` parameter description
>    - Note that instructions are prepended to every send in the session
>
> 2. Update `claude_code_send` tool section:
>    - Add `context` parameter description
>    - Add `include_diff` parameter description (default true)
>    - Document the `diff` field in the structured result
>    - Note the diff mechanism: captures committed changes, unstaged edits, and lists new untracked files
>
> 3. Add a "Git Best Practice" section or note advising:
>    - Always use git-initialized working directories for Claude Code sessions
>    - This ensures reliable diff capture and change tracking
>    - The parent agent should consider running `git init` via `claude_code_exec` if the cwd isn't already a repo
>
> 4. Update the workflow section to show context injection:
>    ```
>    start (with instructions) → send (with context + include_diff) → review diff → send (fix) → exec (verify) → stop
>    ```
>
> 5. Check `CLAUDE.md` for any conventions worth noting. Context injection and diff capture are feature additions, not new conventions — likely no CLAUDE.md changes needed.
>
> Run `make check` one final time.
