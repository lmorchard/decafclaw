# Context Composer — Plan

## Overview

Extract scattered context assembly into a unified `ContextComposer` class. The work is broken into 6 steps, each building on the previous. Every step ends with lint + test + commit.

The key files we're refactoring from:
- `agent.py` — `_prepare_messages()` (lines 697-797), `_build_tool_list()` (lines 300-330), `_collect_all_tool_defs()` (lines 261-297), agent loop tool injection (lines 852-867)
- `memory_context.py` — `retrieve_memory_context()`, `format_memory_context()`
- `tool_registry.py` — `classify_tools()`, `build_deferred_list_text()`, `estimate_tool_tokens()`
- `prompts/__init__.py` — `load_system_prompt()`
- `context.py` — needs new `ComposerState` sub-object
- `config_types.py` — needs `context_window_size` on `LlmConfig`

---

## Step 1: Dataclasses and skeleton

Create `src/decafclaw/context_composer.py` with the data structures and an empty `ContextComposer` class. Add `ComposerState` to `context.py`. Add `context_window_size` to `LlmConfig`. Write unit tests for the dataclasses.

No behavior changes — just the types and wiring.

### Prompt

```
We're building a ContextComposer for DecafClaw (issue #182). This step creates the
skeleton — data structures and empty class, no behavior yet.

Create `src/decafclaw/context_composer.py` with:

1. `SourceEntry` dataclass:
   - source: str (e.g. "system_prompt", "memory", "tools", "history", "wiki")
   - tokens_estimated: int
   - items_included: int
   - items_truncated: int
   - details: dict (default empty dict)

2. `ComposedContext` dataclass:
   - messages: list[dict]
   - tools: list[dict]
   - deferred_tools: list[dict]
   - total_tokens_estimated: int
   - sources: list[SourceEntry]

3. `ComposerMode` enum with values: interactive, heartbeat, scheduled, child_agent

4. `ComposerState` dataclass for per-conversation state between turns:
   - last_sources: list[SourceEntry] (default empty)
   - last_total_tokens_estimated: int (default 0)
   - last_prompt_tokens_actual: int (default 0)
   - last_completion_tokens_actual: int (default 0)
   - recent_memory_ids: list[str] (default empty — tracks recently injected memory entries)

5. `ContextComposer` class:
   - __init__(self, state: ComposerState | None = None) — stores or creates state
   - compose(self, ctx, user_message, history) -> ComposedContext — stub that raises NotImplementedError
   - record_actuals(self, prompt_tokens: int, completion_tokens: int) — stores on state

In `context.py`:
- Add `composer: ComposerState` to the `Context.__init__` with default `ComposerState()`
- Include it in `fork()` and `fork_for_tool_call()` — share the reference (same conversation)

In `config_types.py`:
- Add `context_window_size: int = 0` to `LlmConfig` (0 = not specified, fall back to compaction_max_tokens)

Write tests in `tests/test_context_composer.py`:
- Test that ComposedContext and SourceEntry can be constructed
- Test that ComposerMode has all expected values
- Test record_actuals stores values on state
- Test compose raises NotImplementedError
- Test that ctx.composer exists and is a ComposerState after Context creation
- Test that fork() shares the composer reference

Run `make check && make test` and fix any issues.
```

---

## Step 2: System prompt assembly

Move `load_system_prompt()` logic into the composer as a source. The composer calls the existing function internally but wraps it with token estimation and source tracking.

### Prompt

```
Step 2: Move system prompt assembly into the ContextComposer.

In `context_composer.py`, add a method `_compose_system_prompt(self, config) -> tuple[str, SourceEntry]`:
- Call the existing `load_system_prompt(config)` from `prompts/__init__.py`
- This returns `(prompt_text, discovered_skills)` — we only need prompt_text here
- BUT: we need to handle per-conversation skill activation too. The system prompt
  stored on config is the base. Activated skills append their body content.
  Check `ctx.skills.activated` and for each activated skill that has a body,
  append it to the prompt text (matching the current behavior in `_setup_turn_state`
  and skill activation).
- Use `estimate_tokens()` from `util.py` to estimate the token cost
- Return the prompt text and a SourceEntry with source="system_prompt"
- items_included = count of sections (SOUL + AGENT + USER + skills)

Important: the system prompt is currently loaded once at startup and stored on
`config.system_prompt`. The composer should use that cached value as the base
and only re-compose the dynamic parts (activated skill bodies). Don't re-read
files from disk each turn.

Add a helper `_get_context_window_size(self, config) -> int`:
- Returns config.llm.context_window_size if > 0, else config.compaction.max_tokens
- This is the total budget ceiling the composer works within

Write tests:
- Test _compose_system_prompt returns prompt text and a valid SourceEntry
- Test that token estimate is positive for a non-empty prompt
- Test _get_context_window_size prefers context_window_size over compaction max_tokens
- Test _get_context_window_size falls back when context_window_size is 0

Run `make check && make test` and fix any issues.
```

---

## Step 3: Memory and wiki context

Move memory context retrieval and wiki page injection into the composer. These are currently interleaved in `_prepare_messages()`.

### Prompt

```
Step 3: Move memory and wiki context into the ContextComposer.

In `context_composer.py`, add two methods:

1. `async _compose_memory_context(self, ctx, config, user_message) -> tuple[list[dict], SourceEntry | None]`:
   - If ctx.skip_memory_context or mode is heartbeat/scheduled/child_agent, return ([], None)
   - Otherwise call `retrieve_memory_context(config, user_message)` from memory_context.py
   - If results, call `format_memory_context(results)` and create the memory_context message dict
   - Track which entry IDs/texts were injected in state.recent_memory_ids
   - Return the messages to inject and a SourceEntry with source="memory"
   - SourceEntry.items_included = len(results), tokens_estimated from the formatted text
   - Fail-open: catch exceptions, log warning, return ([], None)

2. `_compose_wiki_context(self, ctx, config, user_message, history) -> tuple[list[dict], SourceEntry | None]`:
   - If mode is heartbeat/scheduled/child_agent, return ([], None)
   - Use the existing `_parse_wiki_references()` and `_read_wiki_page()` helpers from agent.py
   - Check `_get_already_injected_pages(history)` to avoid re-injecting
   - Build wiki_context message dicts (same format as current code)
   - Return messages and SourceEntry with source="wiki"
   - items_included = pages injected, items_truncated = pages skipped (already injected or not found)

The helpers `_parse_wiki_references`, `_read_wiki_page`, `_get_already_injected_pages`
should be importable from agent.py for now. We're not moving them yet — just calling
them from the composer. Make sure they're not underscore-private in a way that prevents
import (they are currently private but we can import them within the package).

Add the `mode` parameter to compose() signature:
  `compose(self, ctx, user_message, history, *, mode: ComposerMode = ComposerMode.INTERACTIVE)`

Write tests:
- Test _compose_memory_context skips when skip_memory_context is True
- Test _compose_memory_context skips for non-interactive modes
- Test _compose_memory_context returns results and SourceEntry when memory is available (mock retrieve_memory_context)
- Test _compose_memory_context is fail-open (mock to raise, returns empty)
- Test _compose_wiki_context skips for heartbeat mode
- Test _compose_wiki_context returns wiki messages for referenced pages (mock file reads)

Run `make check && make test` and fix any issues.
```

---

## Step 4: Tool assembly

Move tool classification and deferral into the composer.

### Prompt

```
Step 4: Move tool assembly into the ContextComposer.

In `context_composer.py`, add a method:

`_compose_tools(self, ctx, config) -> tuple[list[dict], list[dict], str | None, SourceEntry]`:
Returns (active_tools, deferred_tools, deferred_text, source_entry).

This should replicate the logic currently in `_build_tool_list()` and the deferred
text injection from the agent loop (agent.py lines 852-867):

1. Call `_collect_all_tool_defs(ctx)` to get all tool definitions
2. Call `classify_tools(all_defs, config, fetched_names)` to split active vs deferred
3. Apply `ctx.tools.allowed` filter to active set (same as current _build_tool_list)
4. If deferred: set `ctx.tools.deferred_pool`, add SEARCH_TOOL_DEFINITIONS to active,
   build deferred_text via `build_deferred_list_text()`
5. Build SourceEntry with source="tools":
   - tokens_estimated = estimate_tool_tokens(active) + estimate_tokens(deferred_text or "")
   - items_included = len(active)
   - items_truncated = len(deferred)
   - details = {"deferred_mode": bool(deferred)}

The existing functions in tool_registry.py and agent.py stay in place — the composer
calls them. We're centralizing the orchestration, not moving the implementations.

Write tests:
- Test _compose_tools with tools under budget (no deferral)
- Test _compose_tools with tools over budget (deferral active)
- Test _compose_tools applies allowed_tools filter
- Test SourceEntry reflects correct counts

Run `make check && make test` and fix any issues.
```

---

## Step 5: History and full compose()

Implement the history source and the full `compose()` method that assembles everything into a `ComposedContext`.

### Prompt

```
Step 5: Implement history composition and the full compose() method.

In `context_composer.py`:

1. Add `_compose_history(self, ctx, config, history, memory_msgs, wiki_msgs, user_msg, attachments) -> tuple[list[dict], SourceEntry]`:
   - Append wiki_msgs to history (with archiving via _archive from agent.py)
   - Append memory_msgs to history (with archiving)
   - Append user_msg to history (with archiving, using archive_text if provided)
   - Filter history through ROLE_REMAP and LLM_ROLES (same logic as current _prepare_messages)
   - Resolve attachments via _resolve_attachments
   - Return the filtered/remapped message list and a SourceEntry with source="history"
   - tokens_estimated = estimate_tokens of all history messages
   - items_included = number of messages

2. Implement `compose()`:
   - Accept full signature: `compose(self, ctx, user_message, history, *, mode=ComposerMode.INTERACTIVE, archive_text="", attachments=None)`
   - Truncate oversized user messages (same max_message_length logic)
   - Call _compose_system_prompt → system prompt text + source
   - Call _compose_memory_context → memory messages + source
   - Call _compose_wiki_context → wiki messages + source
   - Call _compose_history → filtered history + source (this also does archiving and role remapping)
   - Call _compose_tools → tools + deferred + text + source
   - Build messages array: [system_prompt_msg] + (deferred_text_msg if any) + history_messages
   - Publish events: wiki_context events, memory_context event (same as current code)
   - Calculate total_tokens_estimated = sum of all source token estimates
   - Build ComposedContext with all fields
   - Store sources on self.state.last_sources and total on state.last_total_tokens_estimated
   - Return ComposedContext

3. Also return `retrieved_context_text` somehow — the current code returns it from
   _prepare_messages for use in reflection. Add it as a field on ComposedContext:
   `retrieved_context_text: str = ""`

Write tests:
- Test full compose() produces a valid ComposedContext with messages and tools
- Test compose() message ordering: system prompt first, then deferred text (if any), then history
- Test compose() truncates long user messages
- Test compose() stores sources on state
- Test compose() includes retrieved_context_text when memory context exists
- Mock the LLM-dependent parts (memory retrieval, tool definitions)

Run `make check && make test` and fix any issues.
```

---

## Step 6: Wire into agent loop

Replace the scattered assembly in `run_agent_turn()` with a single `composer.compose()` call. This is the integration step — behavioral equivalence is critical.

### Prompt

```
Step 6: Wire the ContextComposer into the agent loop. This is the critical
integration step — the composed output must be identical to current behavior.

In `agent.py`, modify `run_agent_turn()`:

1. Create a ContextComposer instance using ctx.composer state:
   `composer = ContextComposer(state=ctx.composer)`

2. Determine the mode from ctx flags:
   - ctx.is_child → ComposerMode.CHILD_AGENT
   - ctx.skip_memory_context and no other child indicators → check if this looks
     like heartbeat/scheduled (you may need to add a ctx.mode hint, or infer from
     skip_memory_context — for now, default to INTERACTIVE unless is_child or
     skip_memory_context is set)
   - For this first pass, a simple mapping is fine:
     - is_child=True → CHILD_AGENT
     - skip_memory_context=True and not is_child → HEARTBEAT (covers heartbeat + scheduled)
     - else → INTERACTIVE

3. Replace the `_prepare_messages()` call and tool assembly with:
   ```python
   composed = await composer.compose(
       ctx, user_message, history,
       mode=mode, archive_text=archive_text, attachments=attachments,
   )
   messages = composed.messages
   ctx.messages = messages
   retrieved_context_text = composed.retrieved_context_text
   ```

4. In the iteration loop, replace `_build_tool_list()` with getting tools from composed:
   - First iteration: use composed.tools and composed.deferred_tools
   - Subsequent iterations (after tool calls modify ctx.tools): rebuild tools via
     composer._compose_tools() or keep calling _build_tool_list() for now since
     tool state can change mid-turn when tool_search fetches new tools
   
   Actually, the cleanest approach: keep _build_tool_list() for per-iteration tool
   assembly (since fetched tools change between iterations), but use the composer
   for the initial message assembly. The composer's _compose_tools runs once for
   diagnostics; the iteration loop re-runs _build_tool_list as before.

5. After the LLM response, call:
   `composer.record_actuals(prompt_tokens, completion_tokens)`

6. Remove the direct calls to _prepare_messages() — all message assembly now goes
   through the composer. The _prepare_messages function can be removed or marked
   deprecated.

7. Update the deferred_text injection in the loop to use the same pattern but
   sourced from the initial compose() result for the first iteration.

CRITICAL: Run the full test suite after this change. The composed output must be
behaviorally equivalent. If any test fails, debug carefully — don't paper over
differences.

Also update `tests/test_agent_turn.py` to account for the new composer integration:
- Existing tests should still pass (they test helpers and the full turn)
- Add a test that verifies composer.state has sources populated after a turn

Run `make check && make test` and fix any issues.

After this step, update CLAUDE.md:
- Add context_composer.py to the key files list
- Add a convention note about the ContextComposer pattern
- Update any references to _prepare_messages

Also update docs/ if there's a relevant page, and create a new docs/context-composer.md
documenting the module.
```

---

## Summary of changes per step

| Step | New/Modified Files | Tests |
|------|-------------------|-------|
| 1 | `context_composer.py` (new), `context.py`, `config_types.py` | `test_context_composer.py` (new) |
| 2 | `context_composer.py` | `test_context_composer.py` |
| 3 | `context_composer.py` | `test_context_composer.py` |
| 4 | `context_composer.py` | `test_context_composer.py` |
| 5 | `context_composer.py` | `test_context_composer.py` |
| 6 | `agent.py`, `context_composer.py`, `CLAUDE.md`, `docs/` | `test_context_composer.py`, `test_agent_turn.py` |

## Risk notes

- **Step 6 is the riskiest** — it's where we swap the actual code path. All prior steps are additive.
- **Tool iteration rebuild**: The agent loop rebuilds tools each iteration (fetched tools change). The composer handles initial assembly; the loop still calls `_build_tool_list()` per-iteration. This is a deliberate compromise for this session — full tool lifecycle in the composer is a follow-up.
- **Event ordering**: Memory context and wiki context events must publish in the same order as today (wiki during injection, memory after user message). The composer must preserve this.
- **Archive side effects**: `_prepare_messages()` calls `_archive()` as a side effect. The composer must do the same, in the same order.
