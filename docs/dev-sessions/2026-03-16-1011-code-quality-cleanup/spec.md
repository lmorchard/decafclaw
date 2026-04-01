# Code Quality Cleanup — Spec

## Goal

Refactor and clean up the DecafClaw codebase to address code quality issues identified during a comprehensive review. Focus on maintainability, consistency, and reducing complexity in the largest modules.

## Scope

### Big Issues

1. **`mattermost.py` monolith (850 lines)**
   - `run()` is 352 lines with 11 parallel state dicts tracking conversation state
   - `_process_conversation()` is 189 lines — a god function handling circuit breaking, cooldown, history, streaming, skill state, and response posting
   - `_subscribe_progress()` defines an 80-line inner callback
   - Extract `ConversationState` dataclass from parallel dicts
   - Extract circuit breaker into its own class
   - Split `_process_conversation()` into phases

2. **`agent.py` oversized functions**
   - `run_agent_turn()` at 145 lines handles too many concerns
   - `run_interactive()` at 151 lines handles too many concerns
   - Missing JSON error handling when parsing tool call arguments
   - Decompose into smaller, focused functions

3. **Missing tests for core infrastructure**
   - `mattermost.py` — zero tests
   - `events.py` — zero tests
   - `context.py` — zero tests
   - `agent.py` — zero tests for the core loop
   - `archive.py` — zero tests
   - Add unit tests for at least events.py and context.py (small, high-leverage)

### Medium Issues

4. **Inconsistent error handling across tools**
   - Some tools return `ToolResult(text="[error: ...]")`, others return bare strings
   - Standardize on `ToolResult` everywhere

5. **Duplicated confirmation request pattern**
   - `shell_tools.py` (2x) and `skill_tools.py` each have near-identical 30-line confirmation blocks
   - Extract a shared `request_confirmation()` helper

6. **`config.py` — 40+ fields in one dataclass**
   - No sub-grouping of related fields
   - `load_config()` is 40+ lines of `os.getenv()` boilerplate
   - Repeated `.lower() == "true"` pattern needs a helper
   - Consider sub-dataclasses or at minimum a `_parse_bool()` helper

7. **Global mutable state**
   - `heartbeat_tools.py`: `_heartbeat_running` flag can get stuck if task crashes
   - `tabstack/tools.py`: `_client` global with no idempotency check
   - `mcp_client.py`: module-level `_registry`
   - Replace flags with `asyncio.Lock` where appropriate

8. **Type annotations sparse**
   - Most function params typed, but return types frequently missing
   - Especially in llm.py, agent.py, mattermost.py, tool functions

## Out of Scope

- New features
- Low-priority cleanup items (memory.py parsing duplication, embeddings.py context manager, etc.)
   - memory.py: entry parsing logic duplicated between search_entries and recent_entries
   - embeddings.py: DB connection open/close pattern repeated — could use a context manager
   - heartbeat.py: parse_interval("30m") doesn't work (only handles plain ints or 1h30m format)
   - compaction.py: _estimate_tokens (chars//4) is very rough — fine as long as you know it's a heuristic
   - skills/__init__.py: _split_frontmatter() uses string manipulation instead of a proper YAML parser; fragile with --- in content
   - skills/__init__.py: SkillInfo.disable_model_invocation field appears unused
   - shell_tools.py: _suggest_pattern() heuristic is lossy — python train.py --lr=0.001 becomes python train.py *
   - Magic numbers scattered (60s timeout, 50000 char truncation, 4 chars/token) should be named constants
   - docs/backlog/ referenced in CLAUDE.md but doesn't exist
- Adding mypy/pyright to CI (can be a follow-up)
- Restructuring data layer modules (archive, compaction, embeddings)

## Success Criteria

- All existing tests still pass
- Linting clean
- `mattermost.py` broken into smaller, testable pieces
- `agent.py` functions decomposed
- New unit tests for events.py and context.py
- Consistent tool error handling pattern
- No global mutable flags without proper guards
