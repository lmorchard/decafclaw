# User-Invokable Commands — Plan

## Status: Ready

## Overview

Four phases. Each ends with lint + test passing and a commit. Phase 1 adds the new frontmatter fields and command lookup infrastructure. Phase 2 implements argument substitution and the command execution engine. Phase 3 wires up the chat layers (Mattermost + web UI). Phase 4 adds pre-approved tools and a sample command.

---

## Phase 1: Frontmatter fields and command lookup

**Goal**: Parse new frontmatter fields (`allowed-tools`, `context`, `argument-hint`) from SKILL.md. Provide a function to look up a command by name from discovered skills.

**Files**: `skills/__init__.py`

### Prompt

Read `src/decafclaw/skills/__init__.py` — focus on `SkillInfo` dataclass (line 16) and `parse_skill_md` (line 29).

1. **Add fields to `SkillInfo`**:
   - `allowed_tools: list[str] = field(default_factory=list)` — parsed from `allowed-tools` frontmatter
   - `context: str = "inline"` — `"inline"` or `"fork"`
   - `argument_hint: str = ""` — for future UI use

2. **Update `parse_skill_md`** to read these from frontmatter:
   - `allowed-tools`: split comma-separated string into list, strip whitespace
   - `context`: read as string, default `"inline"`
   - `argument-hint`: read as string, default `""`

3. **Add `find_command(name: str, discovered_skills: list[SkillInfo]) -> SkillInfo | None`**:
   - Search `discovered_skills` for a skill where `user_invocable is True` and `name` matches
   - Return the first match (workspace > agent > bundled is already the scan order)

4. **Add `list_commands(discovered_skills: list[SkillInfo]) -> list[SkillInfo]`**:
   - Return all skills where `user_invocable is True`, sorted by name

5. **Tests**: parse frontmatter with new fields, find_command lookup, list_commands filtering.

Lint and test after.

---

## Phase 2: Command execution engine

**Goal**: Implement argument substitution and the core command execution function that the chat layers will call. This lives in the agent layer — no chat-specific code.

**Files**: new `src/decafclaw/commands.py`

### Prompt

Read:
- `src/decafclaw/skills/__init__.py` — `SkillInfo`, `find_command`
- `src/decafclaw/tools/skill_tools.py` — `tool_activate_skill` for how skills get activated
- `src/decafclaw/tools/delegate.py` — `_run_child_turn` for fork execution
- `src/decafclaw/agent.py` — `run_agent_turn` for inline execution

Create `src/decafclaw/commands.py`:

1. **`substitute_arguments(body: str, arguments: str) -> str`**:
   - Replace `$ARGUMENTS` with the full argument string
   - Replace `$0`, `$1`, `$2`, ... with positional args (whitespace-split)
   - If `$ARGUMENTS` does not appear in body and arguments is non-empty, append `\n\nARGUMENTS: {arguments}`
   - Return the substituted body

2. **`async def execute_command(ctx, skill: SkillInfo, arguments: str) -> str`**:
   The core command execution function. Steps:

   a. **Auto-activate the skill** — load native tools and register on ctx, but skip permission checks. Extract from `tool_activate_skill` in skill_tools.py:
      - The existing code after the permission check (lines ~130-160) does: load native tools, call init, register on ctx.extra_tools/extra_tool_definitions, add to activated_skills, return body + tool list.
      - Extract this into `activate_skill_internal(ctx, skill_info) -> str` that does the loading/registration and returns the body text. No permission check, no heartbeat check.
      - `tool_activate_skill` calls `activate_skill_internal` after its permission check.
      - `execute_command` calls `activate_skill_internal` directly (user already consented by invoking the command).

   b. **Substitute arguments** into the body via `substitute_arguments`.

   c. **Set pre-approved tools** on ctx — `ctx.preapproved_tools = set(skill.allowed_tools)`. This is a new Context field that confirmation-checking tools will inspect.

   d. **Execute**:
      - If `skill.context == "fork"`: call `delegate_task`'s `_run_child_turn` with the substituted body as the task. Pass `preapproved_tools` through to the child ctx.
      - If `skill.context == "inline"`: return the substituted body as the user message text. The caller (chat layer) will pass this to `run_agent_turn` instead of the raw user message.

   e. **Return**: for fork mode, return the child's response text. For inline mode, return the substituted body (the chat layer uses it as the user message).

3. **Add `preapproved_tools: set = set()` to Context.__init__**

4. **Tests**:
   - substitute_arguments with $ARGUMENTS, $0/$1, fallback append
   - execute_command in fork mode (mock _run_child_turn)
   - execute_command in inline mode (returns substituted body)

Lint and test after.

---

## Phase 3: Wire up chat layers

**Goal**: Detect command triggers in Mattermost and web UI, call the command engine, handle responses.

**Files**: `mattermost.py`, `web/websocket.py`

### Prompt

Read:
- `src/decafclaw/commands.py` from Phase 2
- `src/decafclaw/mattermost.py` — `_process_conversation` (line 413), where `combined_text` is formed and `run_agent_turn` is called
- `src/decafclaw/web/websocket.py` — `_handle_send` (line 111), where the user message is passed to `_run_agent_turn`

### Part 3a: Command detection helper

Create a shared helper (in `commands.py`):

```python
def parse_command_trigger(text: str, prefix: str = "!") -> tuple[str, str] | None:
    """Parse a command trigger from message text.

    Returns (command_name, arguments) if the text starts with the prefix
    followed by a letter (not whitespace/punctuation), or None if it's
    a regular message. This avoids false positives on "!!! wow" or "/ path".
    """
```

Validation: after stripping the prefix, the first character must be a letter (`str.isalpha()`). If not, return None (treat as normal message).

### Part 3b: Built-in help

In `commands.py`:

```python
def format_help(discovered_skills: list[SkillInfo], prefix: str = "!") -> str:
    """Format the help text listing all available commands."""
```

### Part 3c: Mattermost integration

In `_process_conversation`, after forming `combined_text` (line 447) but BEFORE creating `req_ctx`:

1. Check `parse_command_trigger(combined_text, prefix="!")`
2. If `help`: send `format_help(app_ctx.config.discovered_skills, prefix="!")` directly as a message, return (no agent turn, no ctx needed)
3. If unknown command: send error message directly, return
4. If a valid command:
   - For fork mode: create a minimal ctx, call `execute_command(ctx, skill, arguments)`, send response as message
   - For inline mode: stash the skill info and substituted body. Continue to ctx creation, then set `ctx.preapproved_tools = set(skill.allowed_tools)` on the newly created `req_ctx`. Use the substituted body as `combined_text` for `run_agent_turn`.
5. If not a command: proceed as normal

**Key timing detail**: help and error responses happen before ctx creation. Inline command execution uses the normal ctx creation flow but overrides `combined_text` and sets `preapproved_tools`.

### Part 3d: Web UI integration

In `_handle_send`, after extracting `text`:

1. Check `parse_command_trigger(text, prefix="/")`
2. If `help`: send `format_help(state["config"].discovered_skills, prefix="/")` as a message_complete event, return
3. If unknown command: send error, return
4. If a valid command: call `execute_command` with a ctx
   - Fork mode: send response as message_complete
   - Inline mode: replace `text` with returned body, set `preapproved_tools` on the ctx inside `_run_agent_turn`, proceed
5. If not a command: proceed as normal

### Part 3e: Interactive mode

In `agent.py` `_interactive_loop` (or wherever interactive input is read), add command detection with `!` prefix (same as Mattermost). Handle help inline, fork/inline same as other layers.

### Part 3d: Web UI integration

In `_handle_send`, after extracting `text`:

1. Check `parse_command_trigger(text, prefix="/")`
2. If `help`: send `format_help(...)` as a message_complete event
3. If a valid command: call `execute_command(ctx, skill, arguments)`
   - Fork mode: send response as message_complete
   - Inline mode: replace `text` with returned body, proceed to `_run_agent_turn`
4. If unknown command: send error
5. If not a command: proceed as normal

### Tests:
- parse_command_trigger with valid commands, no prefix, help
- Integration: Mattermost command detection (mock)
- Integration: web UI command detection (mock)

Lint and test after.

---

## Phase 4: Pre-approved tools and sample command

**Goal**: Make `preapproved_tools` actually bypass confirmation. Create a sample command to test end-to-end.

**Files**: `tools/shell_tools.py`, `tools/confirmation.py`, `tools/delegate.py`, `context.py`

### Prompt

Read:
- `src/decafclaw/tools/shell_tools.py` — `tool_shell` (line 88), specifically the heartbeat auto-approve pattern
- `src/decafclaw/tools/confirmation.py` — `request_confirmation`
- `src/decafclaw/tools/skill_tools.py` — `tool_activate_skill`, the permission check section
- `src/decafclaw/context.py` — `fork_for_tool_call` (preapproved_tools needs to be copied)

### Part 4a: Shell tool pre-approval

In `tool_shell` (shell_tools.py), after the heartbeat auto-approve check:

```python
# Command pre-approved tools bypass confirmation
preapproved = ctx.preapproved_tools
if "shell" in preapproved:
    log.info(f"[tool:shell] pre-approved by command: {command}")
    return _execute_command(ctx, command)
```

### Part 4b: Generic tool confirmation pre-approval

In `request_confirmation` (confirmation.py), check ctx.preapproved_tools before publishing the confirm request:

```python
preapproved = ctx.preapproved_tools
if tool_name in preapproved:
    log.info(f"Confirmation pre-approved for {tool_name}")
    return {"approved": True}
```

This handles `activate_skill` and any other confirmation-based tools.

### Part 4c: Skill activation pre-approval

**Not needed as a separate change.** `tool_activate_skill` already calls `request_confirmation` for the permission check, and Part 4b's `request_confirmation` pre-approval covers it. When `preapproved_tools` contains `"activate_skill"`, the confirmation returns `{"approved": True}` immediately. No code change needed in skill_tools.py.

### Part 4d: Propagate to child contexts

In `context.py`, `preapproved_tools` is already copied by `__dict__.update` in `fork_for_tool_call`. Verify with a test.

In `delegate.py`, `_run_child_turn` uses `parent_ctx.fork(config=child_config)` which does NOT use `__dict__.update`. Add explicit copy:
```python
child_ctx.preapproved_tools = getattr(parent_ctx, "preapproved_tools", set())
```
Wait — use `parent_ctx.preapproved_tools` directly since we cleaned up getattr.

### Part 4e: Sample command

Create a sample bundled command for testing. In `data/{agent_id}/workspace/skills/` or as a test fixture:

```yaml
---
name: weather-check
description: "Get weather for a location using wttr.in"
user_invocable: true
allowed-tools: shell
context: fork
argument-hint: "[city]"
---

Get the current weather for the specified location using curl and wttr.in.

Run: curl "wttr.in/$0?format=3"

Report the result concisely.
```

### Part 4f: Docs

- Update CLAUDE.md with commands convention
- Update README if appropriate
- Create docs/commands.md

### Tests:
- Shell pre-approval bypasses confirmation
- Confirmation pre-approval returns approved immediately
- Skill activation pre-approval
- preapproved_tools propagated through fork_for_tool_call (already via __dict__.update)
- preapproved_tools propagated through delegate child
- End-to-end: command trigger → execution → tool use without confirmation

Lint and test after.

---

## Dependency Graph

```
Phase 1 (frontmatter + lookup)
  ↓
Phase 2 (command engine: substitution + execution)
  ↓
Phase 3 (chat layer wiring: Mattermost + web UI)
  ↓
Phase 4 (pre-approved tools + sample command + docs)
```

Each phase builds on the previous. No orphaned code.

## Testing Strategy

- **Phase 1**: Unit tests for frontmatter parsing and command lookup
- **Phase 2**: Unit tests for argument substitution and execute_command (mocked agent)
- **Phase 3**: Unit tests for trigger parsing, integration tests for chat layer command detection
- **Phase 4**: Unit tests for pre-approval bypass in shell/confirmation/skill tools, end-to-end manual QA

## Risk Notes

- **Inline mode message replacement**: when a command runs inline, the substituted body replaces the user's message entirely. The agent sees the command instructions, not "!migrate-todos". This is correct — but if the body is very long, it could be a large prompt. No different from skill activation today.
- **Pre-approved tools scope**: `preapproved_tools` is set on ctx for the duration of the agent turn. In inline mode, this means ALL tool calls during that turn get pre-approval — not just the ones the command directly makes. If the agent decides to call other tools beyond what the command intended, they'd also be pre-approved. This is acceptable — the user invoked the command knowing what tools it pre-approves.
- **Fork mode + allowed-tools**: the child agent gets `preapproved_tools` but also inherits `allowed_tools`. Pre-approval only affects confirmation — it doesn't grant access to tools the child doesn't have.
- **Interactive mode**: uses `!` prefix same as Mattermost. The interactive loop is simpler (no threading, no placeholder management) so the integration is straightforward — detect, substitute, pass to `run_agent_turn`.
