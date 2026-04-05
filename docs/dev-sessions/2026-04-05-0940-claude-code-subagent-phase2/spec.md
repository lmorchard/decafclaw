# Claude Code Subagent Phase 2 — Spec

## Goal

Add context injection and diff output to the Claude Code skill. Context injection lets the parent agent share project conventions, vault knowledge, and per-task context with Claude Code. Diff output lets the parent agent review what changed without separately reading files.

Covers issues: #207 (context injection), #209 (diff output). Part of umbrella #213.

## 1. Context injection (#207)

### Session-level instructions

Add `instructions: str = ""` parameter to `claude_code_start`. Stored on the `Session` dataclass. Set once, immutable for the session lifetime.

Purpose: persistent context prepended to every `claude_code_send` in the session — project conventions, coding style, user preferences.

### Per-send context

Add `context: str = ""` parameter to `claude_code_send`. Ephemeral, applies only to that send.

Purpose: task-specific context — relevant vault pages, specs, conversation excerpts.

### Prompt assembly

When building the prompt for the Claude Code SDK, prepend instructions and context using XML-style tags:

```
<instructions>
{session.instructions}
</instructions>

<context>
{per_send_context}
</context>

{actual_prompt}
```

- If `instructions` is empty, omit the `<instructions>` block entirely
- If `context` is empty, omit the `<context>` block entirely
- If both are empty, the prompt is sent as-is (no change from current behavior)

### Responsibility model

The skill is dumb — it accepts plain strings. The parent agent is responsible for assembling context from vault pages, conversation history, user preferences, etc. No coupling between the skill and vault internals.

### Not on claude_code_exec

Context injection only applies to `claude_code_send` (which involves an LLM). `claude_code_exec` is direct shell execution — no LLM to receive context.

## 2. Diff output (#209)

### Auto git-diff after sends

After `claude_code_send` completes, automatically capture a git diff of what changed during the send.

### Mechanism

1. **Before** SDK streaming starts: capture the current HEAD via `git rev-parse HEAD` (may fail in empty repos — handle gracefully)
2. **After** streaming completes, capture three categories of changes:
   - `git diff <saved_head>` — catches committed changes (if Claude Code made commits)
   - `git diff` — catches unstaged changes to tracked files
   - `git ls-files --others --exclude-standard` — catches new untracked files
3. Combine into a single diff string. For untracked files, generate a pseudo-diff by reading their content (`git diff --no-index /dev/null <file>` for each, or just list them with a header)

**Simplified approach:** Rather than combining three commands, use a two-step strategy:
1. Before: `git rev-parse HEAD` to save the baseline
2. After: `git add -A --dry-run` to see what would be staged (without mutating), then `git diff <saved_head>` for committed changes, plus `git diff` for working tree changes. For untracked files, just list them in the diff field with a `[new file]` marker — the parent can use `claude_code_exec` to read them if needed.

**Even simpler:** Just run `git diff <saved_head>` (for commits) concatenated with `git diff` (for unstaged edits) and append a list of new untracked files. Three commands, no index mutation.

If the cwd is not a git repo, or `git rev-parse HEAD` fails (empty repo, not initialized), skip the diff entirely (return `None`).

### Parameter

Add `include_diff: bool = True` parameter to `claude_code_send`. Defaults to `True` — the parent agent gets diffs by default and can opt out if not needed.

### Result placement

The diff goes in the structured `data` dict as a `diff` string field (or `None` if not a git repo or diff capture failed). It is NOT duplicated in the text summary — the text summary already lists changed files, and the LLM can read the diff from the JSON block.

### No output capping

No size limit on the diff. The parent agent can advise the subagent to keep changes small if diffs are too large.

### SKILL.md guidance

Update the skill documentation to advise the parent agent to use git whenever possible when starting a coding project. This ensures reliable diff capture.

## 3. Edge cases and constraints

### Diff capture failures

- **Not a git repo:** `diff` field is `None`, no error
- **Empty repo (no commits):** `git rev-parse HEAD` fails — skip diff, `diff` field is `None`
- **Git command errors:** Log warning, return `None` — diff is best-effort, never blocks the send result
- **Claude Code committed during send:** `git diff <saved_head>` catches the committed changes; `git diff` catches any remaining unstaged changes

### Untracked files in diff

New files created by Claude Code that aren't git-added won't appear in `git diff`. These are listed separately at the end of the diff string with a `New untracked files:` header. The parent can use `claude_code_exec` to inspect their contents if needed.

### TOOL_DEFINITIONS updates

Both `claude_code_start` and `claude_code_send` TOOL_DEFINITIONS need new parameter entries:
- `claude_code_start`: add `instructions` property
- `claude_code_send`: add `context` and `include_diff` properties

## 4. Files changed

- `src/decafclaw/skills/claude_code/sessions.py` — add `instructions: str` field to `Session`
- `src/decafclaw/skills/claude_code/tools.py` — add parameters, prompt assembly, diff capture
- `src/decafclaw/skills/claude_code/output.py` — add `diff` field to `build_data()` return
- `src/decafclaw/skills/claude_code/SKILL.md` — update tool docs, add git guidance
- Tests for context injection and diff capture

## 5. Out of scope

- Smart context assembly (pulling vault pages, formatting conventions) — parent agent's job
- Diff from tracked SDK edits (fragile, incomplete for Write/new files)
- Diff output capping
- Error classification (#210) — next phase
- File staging (#211) — next phase
- Progress reporting (#212) — next phase
