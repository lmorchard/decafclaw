# Pre-emptive Tool Search

Partial implementation of GitHub issue [#257](https://github.com/lmorchard/decafclaw/issues/257) — *Situational tool selection: dynamic tool visibility based on conversation context*.

This session ships the **pre-emptive tool search** slice only. The other situational signals (scoring layer, tiered visibility, explicit user @mention, MCP disconnect penalties, etc.) are deferred to follow-ups. We'll leave #257 open with a punch list after merge.

## Problem

After the priority system (#263) and skill extraction, most "normal" tools end up deferred under typical budget pressure. The agent can discover them via `tool_search` but must first *know* a tool exists and then type its name. In practice this adds a round-trip (search → fetch → call) even for obvious cases — e.g., if the user says "show me my vault backlinks for X," the `vault_backlinks` tool should already be active by the time the agent reads the message.

RAG-MCP (Gan & Sun, 2025) showed that retrieval-based tool pre-filtering tripled selection accuracy (13.6% → 43.1%) and cut prompt tokens by 50%+ on average. Our slice adapts this to our deferred-catalog architecture: run a cheap keyword match against the current user message before the first LLM call, and promote matching tools into the active set for that turn.

## Goals

- Surface relevant deferred tools automatically based on the current user message, without the agent having to call `tool_search` first.
- Do it cheaply — no embeddings, no LLM inference, microseconds per turn.
- Keep it deterministic and debuggable — it's always clear *why* a tool was promoted.
- Layer onto the existing priority/deferral architecture without new code paths for non-matched tools.

## Non-goals (deferred to follow-ups)

- **TF-IDF or embedding-based matching** — start with keyword overlap; revisit after practical trials show the failure modes.
- **Scoring / tiered visibility** — #257's broader framing of a composite score and Active/Summary/Search-only/Hidden tiers. We'll treat pre-emptive matches as a binary promotion for this session.
- **Other situational signals** — active skill boost (already implicit via `force_critical`), recent usage, explicit user @mention, MCP disconnect penalty.
- **Auto-activation of unactivated skills based on match** — different feature with different trust implications (auto-approve semantics, user confirmation). Don't silently activate skills.
- **Stopword library** — hardcoded list for v1, comment flagging to evaluate a maintained source.

## Design

### Matching algorithm

**Keyword overlap, case-insensitive, with a stopword filter.**

Tokenization:
- Lowercase the input.
- Split on any run of non-alphanumeric characters (treat hyphens and underscores as separators for matching purposes, even though we preserve them elsewhere).
- Drop tokens shorter than 3 characters.
- Drop tokens in a hardcoded English stopword list (~50 common words: "the", "a", "is", "and", "for", "with", "this", "that", etc.).

Scoring:
- For each candidate tool, compute `tool_tokens = tokenize(name + description)`.
- `score = |input_tokens ∩ tool_tokens|` — count of intersecting tokens.
- A tool matches if `score >= 1`.
- Ties broken by tool name (alphabetical) for determinism.

**Stopword source:** hardcoded constant in the implementation module, with a code comment noting we should evaluate a maintained library (e.g., `stop-words` on PyPI, or sourcing from scikit-learn / spaCy) as a follow-up if the list becomes a maintenance burden.

### Match input

When a new user message arrives (triggering a turn):
- Set A = tokens from the user message.
- Set B = tokens from the most recent `role: "assistant"` message with non-empty text content in history. Skips tool-result messages, confirmation requests/responses, cancelled markers (`"[cancelled]"`), vault_retrieval injections, and any non-assistant roles. Empty if no prior assistant message exists (first turn of a conversation).
- Matching input = A ∪ B.

This handles the "yes, one more" short-follow-up case — short user messages inherit topical continuity from the previous assistant reply. Single-turn topic shifts linger one extra turn (acceptable cost).

Computed once at the start of the turn, before the first LLM call. Reused across all agent iterations within the turn — no mid-turn re-matching.

### Candidate pool

All tools that would otherwise be subject to the normal/low budget split in `classify_tools`. That is: everything in the full tool list, *minus* tools already in `force_critical`:
- Declared `priority: critical` tools
- Env override (`CRITICAL_TOOLS`)
- Activated skill tools (via `ctx.tools.extra`) — already critical
- Fetched tools (via `ctx.skills.data["fetched_tools"]`) — already critical
- Always-loaded skill tools

Excluding already-critical tools matters: if a tool is already in the active set by some other means, matching it is a no-op (wasted cycles) and we'd double-count in the `max_matches` cap. The match *extends* critical; it doesn't compete with it or re-promote tools that are already in.

MCP and `normal`/`low` core tools are the main candidates in practice.

### Effect of a match

**Ephemeral promotion for the current turn only.** Matches are added to a new `ctx.tools.preempt_matches: set[str]` and merged into the `force_critical` set passed to `classify_tools`. The existing classifier logic promotes them to the active set — no new code path.

Matches are **not persistent** (unlike `fetched`). The set is recomputed fresh on each new user message. If the user pivots topics, the old matches drop out (with one turn of lag via the "match on previous assistant" heuristic).

Specifically: **calling a matched tool does NOT add it to `fetched_tools`.** Auto-fetch (in `execute_tool`) only fires when a called tool is in the deferred pool but not resolvable — a matched tool resolves directly because it's in the active set, so that code path doesn't trigger. This is intentional — "the agent called a matched tool once" isn't a strong enough signal to persist across topic shifts. (A future "recent usage" signal from #257 can re-promote frequently-called tools without conflating with fetched.)

### Interaction with existing mechanisms

- **`tool_search` / `fetched_tools`** — complementary. If the agent explicitly searches and fetches, those persist. Pre-emptive match runs first and may surface the same tool automatically; if the agent later calls `tool_search` for the same name, it's a harmless no-op (tool already active, `add_fetched_tools` idempotent).
- **`_suggest_tool_names` "did you mean" errors** — fires only in the error path when the agent makes a wrong-name call. Pre-emptive match runs before any call happens. They don't interact.
- **Hyphen/underscore normalization** (registration-time, via `_normalize_server_segment`) — tool names already have hyphens normalized when they enter the candidate pool. Matching operates on whatever's in the pool, so this is transparent.

### Safety cap

At most `max_matches` tools are promoted (default 10), top-N by score, ties broken alphabetically. Prevents a keyword-heavy message from promoting the entire tool set.

### When does pre-emptive search apply?

| Mode | Apply? |
|---|---|
| Interactive chat (web, Mattermost, terminal) | ✅ yes |
| Child agent via `delegate_task` | ✅ yes — the `task` string acts as the user message |
| Heartbeat sections | ✅ yes — the section preamble acts as the user message |
| Scheduled tasks | ✅ yes — the task preamble acts as the user message |
| Reflection judge | ❌ no — doesn't make tool calls |
| Compaction / memory sweep | ❌ no — purpose-specific tool sets |

### Configuration

New nested config under `AgentConfig`:

```python
@dataclass
class PreemptiveSearchConfig:
    enabled: bool = True
    max_matches: int = 10
```

Accessed as `config.agent.preemptive_search.enabled` / `.max_matches`. No env var aliases for v1 (defaults are fine).

### Observability

**Logs (INFO level):**

```
preemptive match: promoted 3 tool(s) for conv <id>:
  - vault_backlinks (score 2, tokens: backlinks, vault)
  - vault_search (score 1, tokens: vault)
```

**Context diagnostics sidecar** (`{conv_id}.context.json`, already produced after each turn): add a `preempt_matches` section listing each promoted tool with score and triggering tokens, plus the token budget impact (how many matches got in, how many hit the cap).

### Integration point

Where the match runs in the lifecycle:

1. User message arrives → `ConversationManager.send_message` → `_start_turn` → agent turn begins.
2. Before the first LLM call, as part of context assembly (`ContextComposer.compose` or the tool-list build): compute matches from the user message + previous assistant response.
3. Store result on `ctx.tools.preempt_matches`.
4. `_build_tool_list()` passes it as a fourth source into the force_critical set alongside `fetched_names` and `skill_tool_names`.
5. `classify_tools()` treats matched names as critical — included regardless of budget pressure (same hard-floor logic as other critical tools).
6. Emit logs + add to sidecar.

The match runs once per turn; subsequent iterations within the same turn reuse the cached set.

## Acceptance criteria

- `make check` (lint + type) passes.
- `make test` passes. New unit tests cover:
  - Tokenization: lowercasing, non-alphanumeric splitting, stopword filtering, min-length filtering.
  - Matching: score computation, ≥1 threshold, empty input, candidates-minus-critical.
  - Safety cap: top-N selection when matches exceed `max_matches`.
  - User + previous-assistant union: short user message inherits topic from prior agent response.
  - Turn lifecycle: `ctx.tools.preempt_matches` populated before classification, merged into critical set.
  - Mode gating: disabled modes (reflection, compaction) skip matching.
  - Config toggle: `enabled: false` short-circuits the match.
- Manual smoke test in the web UI:
  - Fresh conversation, no prior activated skills.
  - User: "what are my vault backlinks for decafclaw?" → observe `vault_backlinks` promoted in the context diagnostics sidecar and called by the agent directly (no `tool_search` round-trip).
  - User follow-up "and for tool-priority?" → `vault_backlinks` still promoted via previous-assistant heuristic.
  - User pivots "draw me an oblique strategy" → `vault_backlinks` no longer promoted; MCP oblique-strategies tools promoted instead.
- Context diagnostics popover in the web UI surfaces the matches (adapter-level work — add to the source breakdown).
- Docs: new `docs/preemptive-tool-search.md` or section in `docs/tool-search.md` explaining the mechanism. Cross-reference from `docs/tool-priority.md` and `docs/context-map.md`.

## Follow-up punch list (for #257)

Post-merge, comment on #257 noting what shipped and what remains:

- **Scoring model** — composite score combining base priority + multiple situational signals (not just pre-emptive match).
- **Tiered visibility** — Active / Summary / Search-only / Hidden tiers instead of binary in/out.
- **Additional situational signals:**
  - Recent tool usage boost (tools the agent has called this conversation)
  - Explicit user mention (user types `@vault_read` or a tool name in backticks)
  - MCP disconnected server penalty (hide their tools entirely)
  - Unactivated skill tools fully hidden (already current behavior; codify in the scoring model)
- **TF-IDF** matching as a drop-in upgrade to keyword overlap if keyword coverage turns out to be too narrow.
- **Embedding-based matching** as a further upgrade if TF-IDF also hits limits.
- **Stopword library** — swap hardcoded list for a maintained package.
- **Usage-graph prediction (AutoTool pattern)** — learn from historical tool-use sequences.
- **Per-skill tool relevance hints** — skills declare which conversation contexts make their tools relevant.

## Edge cases

- **Empty user message** (attachments-only) — A is empty, B contributes from prior assistant. Fine.
- **Very long user message** (e.g., pasted content) — tokenization is bounded O(n) on message length; matching is O(tools × tokens). With ~50 tools and any realistic message size, stays in microseconds. No length cap needed for v1.
- **Whitespace-only or stopword-only input** — A is empty after tokenization; B-only match, or no match at all. The `force_critical` set just doesn't grow, classifier runs as normal.
- **First turn of a conversation** — no prior assistant, B = ∅, match runs on user message only. Works without special casing.
- **Compacted history** — the "most recent assistant" may be a compaction summary. The summary is typically topical, so matching against it is reasonable. If it misfires, the safety cap limits damage.
- **Tool name starts with an integer / has special chars** — tokenizer treats non-alphanumeric as separators, so `mcp__oblique_strategies__get_strategy` tokenizes to `mcp`, `oblique`, `strategies`, `get`, `strategy`. All 3+ chars, none are stopwords. Matches "strategy", "oblique", etc.
- **Agent message is exactly "[cancelled]"** — tokenizer drops the word "cancelled" only if it's a stopword (it isn't; stopwords are common grammatical words). The token "cancelled" is unlikely to match anything meaningful, so no spurious promotions. Acceptable.

## Open questions for plan phase

- **Exact integration point:** should the match run in `ContextComposer` (composer already handles pre-LLM-call assembly) or in `_build_tool_list` in `agent.py` (tool classification happens there, tightly coupled)? Likely the composer, then pass `preempt_matches` through to `_build_tool_list`.
- **Sidecar schema:** how does `preempt_matches` fit into the existing `SourceEntry` / `ComposedContext` structure? Probably a new `SourceEntry` with `source="preempt_matches"` and match details in `details`.
- **Tokenization perf:** cache tool-side tokenization in a module-level dict keyed by `(tool_name, description_hash)`? Tools don't change mid-process but do after `refresh_skills` or MCP reconnect. Probably over-optimized for v1; measure first.
- **Name-vs-description weighting:** should tokens matched in the tool name count more than tokens matched in the description? Starting with equal weight (simpler). If results show bias toward verbose descriptions, add weighting later.
