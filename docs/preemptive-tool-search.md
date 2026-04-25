# Pre-emptive Tool Search

When a user message arrives, DecafClaw runs a cheap keyword match against tool names and descriptions. Matched tools get promoted into the active tool set for that turn — the agent doesn't need to call `tool_search` first to discover them.

The same pass also matches against the **skill catalog**: non-activated skills whose name + description overlap the user's keywords get surfaced as a short hint message ("these skills look relevant — call `activate_skill`"). This shortcuts the painful "call a skill tool → fail with unknown tool → search → activate → call again" round-trip.

This is a partial implementation of [issue #257](https://github.com/lmorchard/decafclaw/issues/257) covering the pre-emptive search signal. The remaining situational signals (scoring layer, tiered visibility, explicit user mention, MCP disconnect penalty, recent-usage boost) are deferred follow-ups.

## How it works

At turn start, `ContextComposer._compose_preempt_matches` runs before tool classification:

1. **Assemble match input** — tokenize the user message plus the most recent assistant response from history.
2. **Tokenize** — lowercase, split on non-alphanumeric boundaries, drop tokens shorter than 3 characters, drop a small set of common English stopwords.
3. **Score candidates** — for each tool that isn't already force-critical (declared `critical` priority, env-overridden, activated skill tool, already-fetched), compute `score = |input_tokens ∩ tokenize(name + description)|`. Tools with `score ≥ 1` are matches.
4. **Cap by `max_matches`** — sort by `(-score, name)`, keep the top N (default 10).
5. **Promote** — matched tool names land on `ctx.tools.preempt_matches`. The tool classifier treats them as critical for this turn, bypassing the normal/low budget split.

The match runs once at turn start. `_build_tool_list` reuses the same set across agent iterations within the turn, so reclassification after a tool call stays consistent.

## Skill catalog match

Right after the tool match, `ContextComposer._compose_preempt_skill_matches` runs the same tokenization against the discovered skill catalog. Skills already in `ctx.skills.activated` (which includes always-loaded bundled skills like `vault`, `background`, `mcp` that auto-activate at turn start) are excluded from the candidate pool — surfacing them again would be noise.

For each remaining skill, score is `|input_tokens ∩ tokenize(name + description)|`. Top matches (capped by the same `max_matches`) land on `ctx.skills.preempt_matches` and a short system message is appended to the prompt:

```
<preempt_skill_hint>
These skills look relevant to the current message: project, ingest.
Their tools are NOT loaded yet — call activate_skill(name) before trying to use any of their tools.
</preempt_skill_hint>
```

Unlike the tool match, this is purely a **hint** — no auto-activation. The agent still calls `activate_skill` (which respects the user-confirmation flow for non-`auto-approve` skills). The hint just means the agent doesn't have to call a skill-provided tool, hit "unknown tool", search, then activate. The skill name is right there with a directive to activate it.

Diagnostics surface as a parallel source entry, `preempt_skill_matches`, with the same shape as `preempt_matches` (matched skills with score and matched tokens).

## Why "user + previous assistant"?

Short follow-ups like "yes, another one" carry no keywords on their own. Unioning with the previous assistant message's text preserves topical continuity so the matched tools stay promoted across short exchanges. Topic pivots linger one turn (the previous assistant still has the old topic), which is a small acceptable cost.

First turn of a conversation has no prior assistant — only the user message contributes.

## Ephemeral, not persistent

Pre-emptive matches are **recomputed fresh every turn**. They don't persist like tools the agent explicitly fetched via `tool_search`, which stay loaded for the rest of the conversation. If the user pivots topics, the previous match drops out after one extra turn (prior-assistant heuristic).

Calling a matched tool does **not** automatically add it to `fetched_tools`. The auto-fetch fallback in `execute_tool` only fires when a tool name is in the deferred pool but not resolvable — a matched tool resolves directly because it's in the active set, so that path isn't triggered. This is intentional: "matched once" isn't a strong enough signal to persist across topic shifts.

## Configuration

Under `agent.preemptive_search` in `config.json`:

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Master toggle for the match |
| `max_matches` | int | `10` | Cap on tools promoted per turn, sorted by score then name |

No env-var aliases for v1. Disable with:

```json
{
  "agent": {
    "preemptive_search": {"enabled": false}
  }
}
```

## When it applies

Runs in every mode that goes through `ContextComposer.compose`:

| Mode | Applies? |
|---|---|
| Interactive chat (web UI, Mattermost, terminal) | ✅ |
| Child agent (`delegate_task`) | ✅ (matches on the task string) |
| Heartbeat section runs | ✅ (matches on the section preamble) |
| Scheduled tasks | ✅ (matches on the task preamble) |
| Reflection judge | ❌ (no tool calls) |
| Compaction / memory sweep | ❌ (purpose-specific tool sets) |

Heartbeat and scheduled tasks pass `history=[]`, so the prior-assistant heuristic is a no-op — matching runs on the task prompt only.

## Debugging

**Logs (INFO level):** every match fires a log line with the conv ID and promoted tool names:

```
preemptive match: promoted 2 tool(s) for conv ws-abc123: vault_backlinks(2), vault_search(1)
```

**Context diagnostics sidecar** — after each turn, DecafClaw writes `workspace/conversations/{conv_id}.context.json` containing the source breakdown. Look for the `preempt_matches` source entry:

```json
{
  "source": "preempt_matches",
  "tokens_estimated": 342,
  "items_included": 2,
  "details": {
    "input_tokens": ["backlinks", "decafclaw", "vault"],
    "matches": [
      {"name": "vault_backlinks", "score": 2, "matched_tokens": ["backlinks", "vault"]},
      {"name": "vault_search", "score": 1, "matched_tokens": ["vault"]}
    ],
    "max_matches": 10
  }
}
```

**Web UI** — click the context bar in the web UI to open the context inspector popover. Matches show up as a Pre-emptive matches row with the promoted tool names and scores.

## Interactions with other mechanisms

- **`tool_search` / `fetched_tools`** — complementary. If the agent explicitly fetches, those persist. Pre-emptive match runs first and may surface the same tool automatically; a subsequent `tool_search` for the same name is a harmless no-op.
- **`_suggest_tool_names` "did you mean" errors** — those fire in the error path when the agent makes a wrong-name call. Pre-emptive match runs before any call happens. Independent mechanisms.
- **Hyphen/underscore normalization** for MCP tool names — tool identifiers entering the candidate pool already have hyphens normalized to underscores (see `docs/mcp-servers.md`). Matching operates on whatever's in the pool, so this is transparent.

## Design rationale (v1 scope)

**Why keyword overlap and not TF-IDF or embeddings?**

Keyword is the lowest-overhead option: microseconds per turn, no tunable weights, deterministic and debuggable. Tool descriptions are already a control surface in DecafClaw — authors write them to be LLM-readable, which also makes them match-friendly.

The obvious limitation is synonyms (a user saying "browse" won't match a "fetch" tool). Real-world impact depends on how often users phrase requests differently from how tools are described. We'll measure with real traffic before adding TF-IDF or embeddings as follow-ups.

**Why ephemeral promotion and not persistent fetching?**

`tool_search` explicit fetching persists because it reflects agent intent — the agent deliberately chose to pull a tool into the active set. Keyword matches are a heuristic that rides on surface-level word overlap. Persisting every match would accumulate cruft as user messages drift across topics, inflating the active tool context without obvious benefit.

## Follow-ups tracked in #257

- Scoring model combining multiple situational signals (not just keyword match)
- Tiered visibility (Active / Summary / Search-only / Hidden) instead of binary in/out
- Recent-usage boost for tools the agent has called this conversation
- Explicit user mention (e.g., `@vault_read` or backticked tool names)
- MCP disconnected-server penalty — hide their tools entirely when offline
- TF-IDF or embedding-based matching upgrade paths
- Evaluating a maintained stopword library (`stop-words` PyPI, scikit-learn, spaCy)

## Related

- [Tool priority system](tool-priority.md) — critical/normal/low tiers that pre-emptive matches layer onto
- [Tool search](tool-search.md) — `tool_search` tool and the deferred catalog format
- [MCP servers](mcp-servers.md) — how MCP tools enter the candidate pool
- [Context map](context-map.md) — overall turn-lifecycle diagram
