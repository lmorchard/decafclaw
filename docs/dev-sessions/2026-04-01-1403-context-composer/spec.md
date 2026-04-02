# Context Composer — Spec

## Related issue

GitHub issue #182 — Context composer: intentional system for assembling agent turn context

## Goal

Extract the scattered context assembly logic into a unified `ContextComposer` that owns the entire pipeline for building what gets sent to the LLM each turn. This session covers the extraction/refactor (issue phases 1-3); relevance scoring and dynamic budget allocation are deferred to a follow-up session.

## Background

Context is currently assembled piecemeal across multiple modules:
- `prompts/__init__.py` — system prompt (SOUL.md + AGENT.md + USER.md + skill catalog)
- `agent.py:_prepare_messages()` — message array, memory/wiki injection, attachment resolution
- `memory_context.py` — semantic retrieval of relevant memories/journal
- `tool_registry.py` — tool definition budgeting and deferral
- `compaction.py` — history size management (reactive, post-turn)

Each source competes for the same token budget with no unified view. Adding new context sources means finding another ad-hoc injection point.

## Design decisions

### Ownership and lifecycle

- The `ContextComposer` is a **stateful, per-conversation object** that lives on the `Context` object as a sub-object (like `TokenUsage`, `ToolState`, `SkillState`).
- It carries state between turns:
  1. **What was included** — which sources, how many tokens each, what got truncated/omitted
  2. **Token usage actuals** — `prompt_tokens` from LLM response, for calibrating estimates vs reality
  3. **Source relevance history** — which memory/wiki entries were injected recently

### Scope of responsibility

The composer owns the **entire context assembly pipeline**:
- System prompt assembly (SOUL.md + AGENT.md + USER.md + skill catalog + always-loaded skill bodies + activated skill content)
- Conversation history
- Memory/journal context retrieval and injection
- Wiki/vault page context injection
- Tool definition classification (always-loaded vs deferred)
- Attachment resolution
- Role remapping (wiki_context/memory_context → user)
- Event publishing for injected context

### Structured result

`compose()` returns a `ComposedContext` dataclass:
- `messages` — ready-to-send message array
- `tools` — active tool definitions for the `tools=` API parameter
- `deferred_tools` — deferred tool definitions (for tool_search mechanism)
- `total_tokens_estimated` — total token estimate across all sources
- `sources` — list of source diagnostic entries

### Source diagnostics

Each source is tracked as an entry like:
```python
@dataclass
class SourceEntry:
    source: str        # e.g. "system_prompt", "memory", "tools", "history"
    tokens_estimated: int
    items_included: int
    items_truncated: int
    details: dict      # source-specific metadata
```

Diagnostics are **mostly invisible** — not published as events every turn. Surfaced on demand via a health tool, context stats tool, or similar command.

### Token budget model

- Introduce an explicit **`context_window_size`** per-model config representing the model's actual context window.
- `compaction_max_tokens` remains as a separate policy choice ("keep context manageable"), which may be significantly smaller than the window size.
- The composer uses `context_window_size` as the hard upper bound and `compaction_max_tokens` as the comfort threshold for flagging compaction need.

### Mode awareness

The composer is **mode-aware**, with enumerated modes that control which sources are included:
- **interactive** — full context: memory, wiki, tools, history (Mattermost, web UI, terminal)
- **heartbeat** — skip memory context, skip wiki
- **scheduled** — skip memory context, skip wiki
- **child_agent** — skip memory context, minimal tools
- Additional modes as needed

Callers provide the mode; the composer decides what to include accordingly. This replaces scattered `skip_memory_context` flags and per-caller conditional logic.

### Compaction relationship

- Compaction stays as a **post-turn reaction** in the agent loop — the composer has no opinion on when to compact.
- The composer reports `total_tokens_estimated` so the agent loop can apply its existing compaction heuristic.
- Compaction policy is explicitly out of scope for the composer.

## Success criteria

1. **Behavioral equivalence** — composed context for any given turn is identical to what the current scattered code produces (same messages, same tools, same ordering)
2. **All existing tests pass** — no regressions
3. **Old code paths called through the composer** — `agent.py`, `memory_context.py`, and `tool_registry.py` context assembly logic is invoked via the composer, not duplicated alongside it
4. **Diagnostics populated** — source entries tracked per turn, even if nothing displays them yet

## Out of scope (deferred to follow-up sessions)

- Relevance scoring (recency + importance + similarity)
- Dynamic budget allocation across sources
- Budget-aware truncation/summarization of individual sources
- Context stats command or UI for diagnostics
- Calibrating token estimates from actuals feedback loop
- Replacing `estimate_tokens()` char/4 heuristic with something better
- Model switching as an alternative to compaction when context pressure is high (the composer provides the data, but the policy decision lives elsewhere)

## Interface sketch

```python
class ContextComposer:
    def compose(self, ctx, user_message, history) -> ComposedContext:
        """Assemble the complete context for this turn."""
        ...

    def record_actuals(self, prompt_tokens: int, completion_tokens: int):
        """Record actual token usage from LLM response for calibration."""
        ...

@dataclass
class ComposedContext:
    messages: list[dict]
    tools: list[dict]
    deferred_tools: list[dict]
    total_tokens_estimated: int
    sources: list[SourceEntry]

@dataclass
class SourceEntry:
    source: str
    tokens_estimated: int
    items_included: int
    items_truncated: int
    details: dict
```

## Relationship to other issues

- #182 — This is the parent issue; this session covers phases 1-3
- #175 — Vault unification (adds more context sources — future composer inputs)
- #180 — Memory → journal unification (changes memory context source)
- #181 — Self-correction loops (reflection results as future context source)
