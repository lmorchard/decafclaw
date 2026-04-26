# Tool-result clearing as a lightweight compaction tier

Tracking issue: #298

## Problem

Compaction today is all-or-nothing at the `compaction.max_tokens`
threshold. Large tool outputs — `web_fetch` bodies, `vault_read` /
`workspace_read` dumps, `tool_search` schema payloads, MCP responses —
sit in history long after the assistant has synthesized them, costing
attention budget and accelerating the next compaction cycle.

Anthropic's [Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
names tool-result clearing as the simplest starting point for context
management: remove raw tool output the agent won't re-examine, leave
the call record intact.

## Goal

A lightweight clear tier that runs every iteration well before the
compaction threshold, rewriting old tool results in place without
summarizing surrounding conversation. Configurable, transparent (the
stub IS the announcement), and reversible only through the JSONL
archive.

## Decisions (autonomous brainstorm)

Six open questions resolved without back-and-forth, optimizing for
shipping a small, defensible default:

1. **One-way clear, not soft-delete.** Soft-delete adds storage and
   a recovery API for a debugging case the JSONL archive already
   covers. Original tool output is durably written to the per-
   conversation archive at the moment it lands; in-memory clearing
   doesn't touch that. Anyone debugging can pull from the archive.

2. **Preserve recent N turns AND a tool-name allowlist.** Cheap
   insurance: even huge tool outputs from the most recent turns
   stay intact because the agent may still be reasoning about them.
   Plus a hard allowlist for tools whose output is fundamentally
   load-bearing — `activate_skill` (announces tools the agent will
   use), `checklist_*` (the per-conversation execution loop's
   step-by-step state).

3. **Transparent stub, not announced.** The stub `[tool output
   cleared: 4213 chars]` IS the announcement. Adding a separate
   system message listing what was cleared duplicates information
   the model can already see by walking history. Less ceremony.

4. **Run every iteration, before the compaction check.** The
   clearing pass is cheap (in-memory string surgery, no LLM call,
   no I/O). Running every iteration keeps history lean before
   compaction has to do the expensive summarization work.

5. **Mutate in place.** Tool messages are dicts; we replace the
   `content` field. The agent loop already mutates `history` in
   place; this is consistent.

6. **Defaults: `enabled: true`, `min_turn_age: 2`, `min_size_bytes:
   1024`, `preserve_tools: ["activate_skill", "checklist_create",
   "checklist_step_done", "checklist_abort", "checklist_status"]`.**
   `min_turn_age: 2` means tool results from the current user turn
   and the immediately prior user turn stay intact; older results
   are eligible. `min_size_bytes: 1024` (1 KiB) is the floor — small
   tool outputs (booleans, short status messages) aren't worth
   clearing because the stub isn't smaller. Allowlist is
   conservative; can grow as we learn.

## Architecture

### `clear_old_tool_results(history, config, *, now=None) -> ClearStats`

Pure function in a new `src/decafclaw/context_cleanup.py`. Walks
history once, mutates eligible tool messages' `content` field, returns
`ClearStats(cleared_count, cleared_bytes)`.

Eligibility rules, applied in order:

1. Message must have `role == "tool"`.
2. Message must already have been "stubbed" check — skip if `content`
   already starts with `"[tool output cleared:"` (idempotent re-runs
   are fine; double-clearing is a waste).
3. Originating tool name (resolved by walking the assistant messages
   for the matching `tool_call_id`) must NOT be in `preserve_tools`.
4. Message must be older than `min_turn_age` user-turn boundaries.
5. `len(content)` must be at least `min_size_bytes`.

Replacement: `content` becomes `f"[tool output cleared: {n} chars]"`.
Other fields (`tool_call_id`, `role`, `display_short_text`, `widget`)
are untouched — the model still sees the call happened, the UI still
has the original short-text and widget for display.

### Configuration

New `CleanupConfig` sub-dataclass on `Config`:

```python
@dataclass
class CleanupConfig:
    enabled: bool = True
    min_turn_age: int = 2
    min_size_bytes: int = 1024
    preserve_tools: list[str] = field(default_factory=lambda: [
        "activate_skill",
        "checklist_create",
        "checklist_step_done",
        "checklist_abort",
        "checklist_status",
    ])
```

Hung off `Config.cleanup`. Loaded via the same `load_sub_config` path
as other sub-configs.

### Invocation

In `src/decafclaw/agent.py`, call `clear_old_tool_results(history,
config)` at the top of `_maybe_compact` (or as a sibling helper called
just before it). Stats accumulate on `ctx.composer` for the sidecar.

### Diagnostics

`ComposerState` stores cumulative cleanup counters as
`cleanup_cleared_count: int` and `cleanup_cleared_bytes: int`. Each
call increments those totals. After the turn, the sidecar
(`workspace/conversations/{conv_id}.context.json`) records the same
cumulative cleared count and bytes.

## Out of scope

- Soft-delete with archived-pointer API. JSONL archive serves the
  debugging case.
- Per-tool size thresholds (e.g. higher floor for `web_fetch`).
  Single global floor is simpler; revisit when a tool actually
  produces lots of small valuable outputs that the global default
  shouldn't clear.
- LLM-summarize-in-place (the "summarize each tool result" tier).
  That's separate from #298 and arguably belongs in a different
  ticket — clearing is the cheap baseline; summarization is its
  costly cousin.
- UI exposure of cleared-bytes stats. The sidecar entry is
  available for the existing context inspector to surface
  whenever someone wants to extend that.
- Compaction interaction. Compaction-time summarization sees the
  stubs as part of history, treats them as small messages,
  produces a fine summary. No special-casing needed.

## Acceptance criteria

- Tool messages older than `min_turn_age` boundaries with content
  ≥ `min_size_bytes` and tool name not in `preserve_tools` have
  their `content` replaced by the stub on every agent iteration
  after the cutoff.
- Recent tool messages (within the protected window) stay intact.
- Allowlisted tool messages (`activate_skill` etc.) stay intact
  regardless of size or age.
- Already-cleared messages are not re-cleared (idempotent stat
  accounting).
- The agent's system prompt / tool schema / non-tool messages are
  untouched.
- Sidecar records the cumulative cleared count and bytes for the
  conversation.
- `enabled: false` config disables the pass entirely (no message
  mutation).

## Testing

- **Unit tests** for `clear_old_tool_results` covering: every
  eligibility rule, both directions (clear / skip); idempotency
  (re-running on a stubbed history is a no-op); size of stub
  message; preservation of non-content fields; `enabled: false`
  short-circuit.
- **Integration test** at the agent-loop level: synthesize a
  history with mixed tool messages, run `_maybe_compact`, assert
  the right messages were cleared and that compaction (if
  triggered) sees the stubs.
- **No real-LLM test in CI.** Manual smoke after merge: hold a
  long conversation with web_fetch and vault_read calls; verify
  the context inspector shows cleared bytes growing.

## Files touched

- `src/decafclaw/context_cleanup.py` (new) — clearing logic.
- `src/decafclaw/config_types.py` — `CleanupConfig` dataclass.
- `src/decafclaw/config.py` — register the sub-config.
- `src/decafclaw/agent.py` — invoke before `_maybe_compact`.
- `src/decafclaw/context.py` — `ComposerState.cleanup_stats`.
- `src/decafclaw/context_composer.py` — surface stats in the
  sidecar.
- `tests/test_context_cleanup.py` (new) — unit tests.
- `docs/context-composer.md` — describe the clear tier.
- `docs/config.md` — `cleanup.*` reference.
- `CLAUDE.md` — context-engineering bullet update.
