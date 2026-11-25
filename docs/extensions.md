# Extensions

Runtime middleware extensions subscribe lifecycle hooks on the `EventBus` without touching core turn code. Configuration is generic: `config.extensions` maps an instance name to an [`ExtensionConfig`](../src/decafclaw/config_types.py) (`enabled`, `type`, `options`), and each extension type parses its own options. See [`extensions`](config.md#extensions) in the config reference.

## Protocol

An extension implements two methods (`src/decafclaw/extensions/__init__.py`):

```python
class Extension(Protocol):
    def enable(self, bus: EventBus) -> list[str]: ...
    def disable(self, bus: EventBus) -> None: ...
```

`enable` subscribes hooks and returns the subscription IDs; `disable` unsubscribes them. `BaseExtension` stores the IDs and implements `disable`; subclasses normally override `enable` only. `ExtensionManager` tracks registration (`register`, `get`, `list_extensions`), activation (`enable`, `disable`, `is_enabled`), and bus subscription bookkeeping.

## PRE_COMPOSE context contributions

`TurnLifecycle.PRE_COMPOSE` is an async collector phase: the composer runs
every subscribed hook concurrently before assembling the turn. A hook may
return `None`, legacy fact text (`str` / `list[str]`), or structured
`ContextContribution` objects from `decafclaw.extensions`:

```python
from decafclaw.context_composer import SourceEntry
from decafclaw.extensions import ContextContribution, ContextSlot

async def pre_compose_hook(ctx, user_message, history):
    return [
        ContextContribution(
            slot=ContextSlot.DELTA,
            messages=[{"role": "user", "content": notes_block}],
            source_entry=SourceEntry(source="notes_extension", tokens_estimated=120, items_included=1),
        )
    ]
```

Slots mount in one invariant order (#1486, #1507, #1574):

| Slot | Mount point | Archived? |
|------|-------------|-----------|
| `HEAD` | After the system prompt, before history | No — replays at the same offset so the provider prefix cache stays stable (#1117) |
| `DELTA` | After archived history, before tail envelopes | Yes |
| `TAIL` | Directly before the current user message | Yes |
| `EPILOGUE` | Directly after the current user message, before dispatch | Yes — replays in place so the prefix cache stays stable |
| `WORKING_MEMORY` | Bundled into one `<working_memory>` message after the `EPILOGUE` hints | No — reaches the provider for this turn only and never replays |

Messages follow the composer's normal role filter: `system`, `user`,
`assistant`, and `tool` are sent as-is, while the composer's internal
injection roles (`vault_retrieval`, `conversation_notes`, `recent_journal`,
`skill_hint`, `recalled_facts`, `working_memory`, and friends) are remapped
to `user`. A `DELTA`, `TAIL`, or `EPILOGUE` message with any other role is
archived but left out of the provider request; an unsupported role in `HEAD`
is skipped with a warning.

`DELTA`, `TAIL`, and `EPILOGUE` contributions are archived with the rest of
this turn's injections and replayed from history on later turns. A hook that
returns the same items every turn therefore duplicates them in history —
return only new items. An `EPILOGUE` contribution lands at the immediate
generation boundary, after the user message; use it for hints that must be
read last. `HEAD` mounts at the same offset after the system prompt every
turn and is never archived, so keep it static per conversation: it sits in
the cached prompt prefix (#1117).

`WORKING_MEMORY` is for per-turn discovery — proactive retrieval, recent
journal entries, scratchpad notes, and recalled facts. The composer wraps each
message's content in a tag named after its role and bundles all of them into
one `<working_memory>` block at the end of the request. Nothing in that block
is archived: the conversational prefix through the previous assistant turn
stays byte-identical for the provider's prompt cache, compaction never
summarizes consumed retrieval, and surfacing references for the UI
travel as metadata on the archived user row (see
[working memory](context-composer.md#working-memory-and-the-cache-boundary-1574)).

Token accounting and Context Inspector visibility come from the
contribution's `source_entry`, which is appended to the turn's `sources`
list. A contribution without one still mounts its messages, but adds no
source row and no estimated tokens. Malformed contributions and unsupported
returns are logged and skipped.

A contribution may also set `provenance`, a map from source key to a list of
JSON-ready item dicts. The composer merges those items into the turn's
provenance payload, which the web UI renders as capsule rows under the
assistant message (#1563). In-tree keys are `facts`, `journals`, `vault`, and
`scratchpad`; unknown keys render as generic capsules. Keep items small
(excerpts, not full bodies) — for `WORKING_MEMORY` the capsules are the only
record of what was injected, so include enough to identify it. A bad
provenance entry is logged and dropped without failing the turn.

Legacy `str` / `list[str]` returns are coerced to one `recalled_facts` block
inside the `WORKING_MEMORY` bundle: the configured timeout and
`lifecycle.pre_compose_token_ceiling` still apply, so existing extensions
(including `mem0`) need no changes. The composer also exposes the block's
atomic fact lines (after any ceiling trim) on the turn as the `facts`
provenance capsule (#1514, #1563).
Structured
contributions carry their own messages and are not truncated by that legacy
ceiling. Hooks run with a per-hook bound: core uses
`lifecycle.pre_compose_timeout` (default 300 ms), and an extension may declare
a larger `pre_compose_timeout` on its `BaseExtension` instance (vault
retrieval declares 5 s for its embedding query). Hooks that finish inside
their bound contribute, a slow hook is cancelled and logged, and the turn
still composes. Caller cancellation propagates instead of being swallowed. A
hook that advances durable state (such as an injection cursor) must do so
immediately before it returns, with no `await` in between, so a cancellation
cannot strand the state.

Dispatch runs in two waves so token accounting is correct (#1365). Fixed-cost
hooks run first; the composer then charges their source entries (with the
system prompt, tools, user message, and history) and publishes
`ComposerState.dynamic_token_budget`. Hooks whose extension sets
`consumes_dynamic_budget = True` run in the second wave and trim to that
budget. Message mount order does not change: both waves contribute to the same
slots, so fixed and dynamic content keep their documented positions.

## Reference and journal extensions (default on)

Four small extensions own the remaining per-turn fixed-cost injections, so
`ContextComposer` only orchestrates slots and provider assembly (#1491):

- `vault_references.py` — explicit `@[[Page]]` references and the currently
  open wiki page, deduplicated against history and published as
  `vault_references` events. `TAIL`, archived, source `wiki`.
- `mentions.py` — `@path` workspace files and `@mcp:server/resource`
  references, deduplicated against history and published as
  `mention_references` events. `TAIL`, archived, source `mentions`.
- `vault_guide.py` — the authored vault protocol document (`AGENTS.md` by
  default) as an authoritative `system` message. `HEAD`, not archived, source
  `vault_guide`. Read fresh each turn; traversal and absolute paths are
  refused.
- `recent_journal.py` — recently written journal entries on a small fixed
  budget with the same delta-cursor contract as scratchpad notes.
  `WORKING_MEMORY`, not archived, source `recent_journal` (clock seam at
  `extensions.recent_journal._now`).

All four skip heartbeat / scheduled / child-agent turns and fail open.

The `[Context: ...]` status line stays a core pipeline stage: its content
depends on the final token accounting of every source in the turn, including
the contributions collected during `PRE_COMPOSE`, so it cannot be computed by
a concurrently dispatched hook. The composer appends it to the
`WORKING_MEMORY` bundle, so it reaches the provider last but never replays as
a stale number in history.

## Pre-emptive hints (default on)

`src/decafclaw/extensions/preempt_hints.py` owns tool and skill discovery
hints (#1257, #604, #1507). It subscribes `PRE_COMPOSE` and returns up to two
`EPILOGUE` contributions:

- `<preempt_tool_hint>` lists keyword-matched tools that classification left
  in the deferred pool, telling the agent to `tool_search` before calling
  them. Source `preempt_matches`; details carry the input tokens, scored
  matches, and the deferred names actually suggested.
- `<preempt_skill_hint>` names the strongest keyword-matched non-activated
  skills and directs the agent to `activate_skill`. Source
  `preempt_skill_matches`; the visible hint keeps candidates within a small
  score gap of the top match (#604).

Both hints use the internal `skill_hint` archive role, which remaps to `user`
for the provider request, stays at its archived position on later turns, and
stays hidden in the CLI/web transcript. Matching reads only the raw user
message (#1049). The extension runs after tool classification, so the
deferred pool is known when the tool hint renders. It is registered with
`default_enabled=True`; `extensions.preempt_hints.enabled: false` yanks it
without touching `agent.preemptive_search`, which still gates both matchers.
Every failure logs and yields no contribution, so a broken match cannot fail
the turn.

## CANDIDATE_RESPONSE verdicts

`TurnLifecycle.CANDIDATE_RESPONSE` runs when an LLM response with no tool
calls is about to become the turn's final answer (#1508). Hooks take
`(ctx, candidate)` — a `CandidateResponse` from
`decafclaw.candidate_response` — and return `None` or a `CandidateVerdict`:

- `reject=False`, `text=None`: deliver the candidate unchanged.
- `reject=False`, `text="..."`: deliver the replacement text. `archive_messages`
  adds transcript-only records (reflection's visibility row) after the final
  assistant message.
- `reject=True`: the runner appends the rejected candidate as an assistant
  message (keeping its thinking/replay metadata), then `retry_messages`, to
  history, the outgoing message list, and the archive, and runs another
  iteration.

Hooks run sequentially in registration order and the first verdict settles the
turn, so a critic's decision is never overwritten by a sibling. Delivery,
archival, and compaction stay in core. A raising hook is logged and skipped;
caller cancellation still propagates.

A stateful hook (a retry budget, judge token totals) keeps per-turn state in
`candidate.state` under a key it owns. Core creates a fresh dict for every
turn, so nothing needs resetting between conversations. See
`tests/test_reflection_extension.py` for the contract.

## PRE_COMPACTION collectors

`TurnLifecycle.PRE_COMPACTION` runs immediately before `compact_history`
summarizes the transcript (#1490). Hooks take `(ctx, history)` and exist for
side effects — flushing unpersisted state, recording boundaries, or preparing
a sibling extension for the history rewrite. Return values are ignored.

Each hook is awaited with a fail-open bound: `lifecycle.pre_compaction_timeout`
(default 2 s), or a larger `pre_compaction_timeout` declared on the hook's
extension instance. A raising or slow hook is logged and skipped; caller
cancellation propagates.

## Compaction (default on)

`src/decafclaw/extensions/compaction.py` owns the compaction policy for the
turn loop. Core decides *when* to ask — after each LLM call with actual prompt
usage, and after a provider context-length error — because compaction rewrites
live history and performs its own LLM call, which cannot live inside a bounded
`PRE_COMPOSE` collector. Core looks up the enabled extension through
`ctx.extension_manager` and calls:

- `maybe_compact(ctx, history, prompt_tokens, lowered=None)` — compacts when
  usage exceeds the effective threshold (resolved by
  `effective_compaction_threshold`).
- `compact(ctx, history)` — runs `compact_history`, fail-open.
- `overflow_threshold(prompt_tokens, estimate)` — turn-local threshold after a
  context-length error.

Options override the policy fields the extension owns; summarization settings
(`model`, `preserve_turns`, decisions slice, memory sweep) stay on
`config.compaction` because `compact_history` reads them:

| Option | Legacy fallback | Default |
|--------|-----------------|---------|
| `max_tokens` | `compaction.max_tokens` | `100000` |
| `threshold_ratio` | `compaction.threshold_ratio` | `None` (fixed cap) |

Disabled (`extensions.compaction.enabled: false`), core performs no proactive
compaction and no reactive overflow recovery; the bounded LLM recovery policy
still handles the resulting provider error. `PRE_COMPACTION` collectors run
only when compaction runs.

## Reflection (default on)

`src/decafclaw/extensions/reflection.py` owns response self-critique (#1508).
It subscribes `CANDIDATE_RESPONSE` and returns a verdict: deliver the candidate,
or reject it with a `[reflection]` critique message so the runner iterates
again. It also publishes the `reflection_result` UI event and the
`reflection_turn` telemetry row, and returns the visibility record
(`reflection: PASS` / `reflection: retry N`) for the transcript when
`reflection.visibility` is `visible` or `debug`.

The judge policy, prompt, and model selection stay in
[`reflection.py`](reflection.md): the extension moves only the turn-loop
glue. Its per-turn state (retry count, summed judge tokens, first response,
exhausted flag) lives under the `candidate.state["reflection"]` key.

Gates:

- `extensions.reflection.enabled: false` yanks the hook entirely — no judge
  call, no telemetry.
- `reflection.enabled: false` keeps the hook registered but delivers every
  candidate unjudged.
- `ctx.skip_reflection` (child agents), cancelled turns, empty content, and an
  exhausted retry budget deliver as-is. An exhausted turn appends the
  model-picker escalation nudge.

Judge failures are fail-open: the candidate is delivered and the turn emits an
`errored` telemetry row. Eval runs mount this extension explicitly (and only
this one) so eval turns keep the reflection behavior they had in core.

## Type registry

```python
from decafclaw.extensions import register_extension_type

register_extension_type("mem0", Mem0Extension)
```

A factory has the shape `Callable[[dict[str, Any], Config], Extension]` — it receives the entry's `options` dict and the whole `Config`, and returns an extension instance. Registering an existing type name replaces it and logs a warning.

Registry helpers: `register_extension_type(name, factory, *, default_enabled=False)`, `get_extension_type(name)`, `get_extension_type_meta(name)`, `get_default_enabled_extension_types()`, `list_extension_types()`, and `clear_extension_types()` (test isolation).

`default_enabled=True` marks a core type that `load_config` seeds when `config.json` defines no entry for it. The flag lives in the `ExtensionTypeMeta` record (`get_extension_type_meta`) and is replaced along with the factory on re-registration, so a test or embedder that swaps a type also chooses its default.

Built-in modules register themselves at import time. `ensure_builtin_extension_types()` imports the built-in modules (`compaction`, `diagnostics_and_telemetry`, `mentions`, `preempt_hints`, `recent_journal`, `reflection`, `scratchpad_notes`, `vault_guide`, `vault_references`, `vault_retrieval`, `improvement_scout`, `mem0`) lazily so `BaseExtension` is already defined (avoids an import cycle), registers any missing name, and is idempotent. Both `load_config` (for seeding) and `init_extensions` call it.

## Loading

`init_extensions(config, bus)` runs once at startup (`runner.py`) and:

1. Imports built-in extension modules so their registrations run.
2. Iterates `config.extensions` in insertion order; `enabled: false` is skipped.
3. Resolves the factory with `ext_cfg.type or <config key>`.
4. Calls `factory(ext_cfg.options, config)`, then `manager.register(name, ext)` and `manager.enable(name)`.

Startup is fail-open: an unknown type, a factory exception, or an enable exception is logged with the extension name and skipped; the manager stays usable. Integration and malformed-config behavior: `tests/test_extensions_registry.py`.

## Default-enabled extensions and options precedence

A type registered with `default_enabled=True` needs no `config.json` entry: `load_config` seeds `{"type": <name>, "enabled": true, "options": {}}` after user-configured entries. An explicit entry naming the same type — under its own key or as an alias via `type:` — suppresses the seed, so `"enabled": false` yanks a default-on extension and the entry stays visible in `config show extensions`.

When a core extension is extracted from a top-level config section, it reads its settings in this order:

1. `extensions.<name>.options.<param>`;
2. the legacy top-level `config.<section>.<param>` field (kept for compatibility);
3. the extension's own default.

Extensions implement step 2 themselves, because options are passed to the factory verbatim and core applies no env-var or legacy-field overrides to them.

## Secret options

`options` is a free-form dict, so dataclass `secret` metadata cannot reach it. Core redacts one fixed structural path, `extensions.*.options.api_key` (the mem0 convention), from admin reads of `config.json`, and `config show` masks it unless `--reveal`. An extension whose credential has a different key name must declare the path in [`secret_policy.redact_paths`](config.md#secrets-and-secret-references-1032); there is no general key-name matcher.

## Diagnostics and telemetry (default on)

`src/decafclaw/extensions/diagnostics_and_telemetry.py` is the Context
Inspector's request recorder and the turn-token metric source (#1488). It
subscribes two hooks and keeps no configuration options:

- `BEFORE_LLM_CALL` (synchronous, allocation-only): deep-copies the outgoing
  `messages` and `tools` plus the resolved model name and
  `ctx.composer.last_sources`, replacing the conversation's previous snapshot.
- `ON_TURN_SETTLE` (background task): writes the last snapshot to
  `workspace/sessions/{session_id}/{conv_id}/last_request.json` and folds the
  turn's `ctx.tokens` totals into the Prometheus `llm_tokens_total` counter
  (gated by `telemetry.metrics_enabled`).

Every failure — model resolution, snapshot copy, file write, metric record —
logs a warning and is swallowed, so diagnostics cannot fail or delay a turn.
`diagnostics_and_telemetry` is registered with `default_enabled=True`, so `load_config` seeds an enabled `extensions.diagnostics_and_telemetry` entry when `config.json` does not define one; an explicit entry wins, so `"enabled": false` cleanly suppresses the sidecar writes and token counters. The REST readers in `context_composer.py` stay available for existing files.

## Scratchpad notes (default on)

`src/decafclaw/extensions/scratchpad_notes.py` moves the per-conversation
scratchpad auto-inject (#299) out of the composer (#1487). It subscribes
`PRE_COMPOSE`, reads `notes.md` for the conversation off the event loop, drops
items at or behind `ComposerState.injection_cursors`, and returns one
`WORKING_MEMORY` contribution with role `conversation_notes` and a `notes`
source entry. The note rides in that turn's `<working_memory>` block only; the
cursor keeps it from repeating.

The extension is registered with `default_enabled=True`, so notes injection
works out of the box. It yields nothing in heartbeat, scheduled, and
child-agent modes, and honors both gates: `config.extensions.scratchpad_notes.enabled`
and the legacy `notes.enabled`. Option overrides:

| Option | Legacy fallback | Default |
|--------|-----------------|---------|
| `context_max_entries` | `notes.context_max_entries` | `20` |
| `context_max_chars` | `notes.context_max_chars` | `4096` |

Every failure — config read, file read, cursor update — logs a warning and
returns no contribution, so the turn still composes.

## Vault retrieval (default on)

`src/decafclaw/extensions/vault_retrieval.py` owns proactive vault retrieval
(#301, #1048, #1157, epic #1483 phase 4). It subscribes `PRE_COMPOSE`, embeds
the user message, fuses the dense vector ranking with an FTS5 lexical ranking
via reciprocal rank fusion (#1816), scores candidates by similarity, recency,
and importance, suppresses pages resident in active history, trims to the turn's
token budget, and returns a `WORKING_MEMORY` `ContextContribution` holding the
`vault_retrieval` block plus a `memory` source entry. The block reaches the
provider for that turn only; the surfaced paths travel as metadata on the
archived user row for auditing and Context Inspector display without suppressing
future turns.

Core computes fixed costs (system prompt, tools, history, user message, and
fixed-slot hook contributions such as mentions and references) and publishes
the remainder on `ComposerState.dynamic_token_budget` before dispatching
budget-consuming hooks; the extension declares
`consumes_dynamic_budget = True` so it runs in that second wave and trims to
that budget, capped by its `max_tokens` option (#1365). It also writes the
turn's `last_retrieved_context_text` and `last_memory_results` to composer
state for reflection and the Context Inspector, publishes the
`vault_retrieval` UI event, and publishes `retrieval_event` telemetry.

Modes and gates match the pre-extraction behavior: `always`, `headlines`,
`on_demand`; skipped for heartbeat, scheduled, and child-agent turns; honors
`ctx.skip_vault_retrieval`. The extension declares
`pre_compose_timeout = 5.0` because the first retrieval loads the embedding
model.

Options override the legacy [`vault_retrieval`](context-composer.md#configuration)
section:

| Option | Legacy fallback | Default |
|--------|-----------------|---------|
| `mode` | `vault_retrieval.mode` | `always` |
| `max_results` | `vault_retrieval.max_results` | `5` |
| `max_tokens` | `vault_retrieval.max_tokens` | `4000` |
| `headline_summary_max_chars` | `vault_retrieval.headline_summary_max_chars` | `200` |
| `show_in_ui` | `vault_retrieval.show_in_ui` | `true` |

Disabled (`extensions.vault_retrieval.enabled: false`), the agent keeps
manual `vault_search` / `vault_read` tools and no proactive injection. Every
failure path logs and degrades instead of failing the turn. There is no
in-memory retrieval cache to invalidate on vault writes: each turn queries
`embeddings.db` through the retrieval provider.

## Reference extension: mem0

`src/decafclaw/extensions/mem0.py` provides local-first semantic memory: embedded or remote Qdrant, FastEmbed embeddings, and a local OpenAI-compatible LLM endpoint. It subscribes `PRE_COMPOSE` (search) and `ON_TURN_SETTLE` (fact extraction), honors `ctx.skip_memory_extension`, and reads options with defaults:

| Option | Default |
|--------|---------|
| `qdrant_path` | `data/mem0_qdrant` |
| `qdrant_url` | `""` (remote Qdrant endpoint, e.g. `http://host:6333`) |
| `embedder_model` | `BAAI/bge-small-en-v1.5` |
| `llm_base_url` | `http://synobian:4100/v1` |
| `llm_model` | `mandelbrot3-flint` |
| `api_key` | `""` (falls back to `local` or `ollama`) |
| `top_k` | `5` |
| `pre_compose_timeout` | `5.0` |

The extension declares `pre_compose_timeout = 5.0` because cold-start embedding initialization exceeds the default 300 ms collector timeout. Both hooks offload the synchronous `Memory` client (search and `add`, including its extraction LLM and Qdrant I/O) with `asyncio.to_thread`: calling it inline stalls every conversation and transport, and the timeout cannot fire while the loop is blocked. Sync locks are acquired inside the worker thread so cancelling the awaiting hook cannot release them mid-call. Writes are serialized because `add()` is a read-modify-write that would otherwise double-insert facts when concurrent conversations settle; an embedded Qdrant store (process-local state, no internal locking) serializes every call, while a remote Qdrant store keeps searches concurrent with writes.

**Dependencies:** `mem0ai` is an optional dependency extra (`memory`). Environments running the mem0 extension must be synced with:
```bash
uv sync --extra memory
```
And systemd service units invoking `uv run` should pass `--extra memory`:
```ini
ExecStart=%h/.local/bin/uv run --extra memory decafclaw
```

Example config: [`extensions`](config.md#extensions).

## Improvement scout

`src/decafclaw/extensions/improvement_scout.py` is an opt-in, out-of-band
observer for harness friction (#1482). It subscribes `ON_TURN_SETTLE` and, on
each settled turn, runs a cheap synchronous heuristic first:

- a `tool` message containing `[error:` or a non-zero `[exit code: N]`;
- an explicit user correction such as `stop`, `wait`, `didn't work`,
  `that's wrong`, or `try again`.

A clean turn returns immediately without a model call. A candidate turn is
evaluated by the configured model, which returns a structured suggestion
(title, observed friction, suggested improvement, subsystem, severity). The
extension appends one JSONL record to
`workspace/improvement_inbox.jsonl` (see [data layout](data-layout.md)).
Identical trigger signatures are suppressed in memory for `dedup_ttl_seconds`,
so a multi-turn retry loop does not flood the inbox.

The observer never writes to the conversation: it does not append messages,
publish chat events, or call `ctx.publish`. Every failure — option parsing, the
model call, JSON parsing, or the inbox write — is logged and swallowed, so a
broken evaluator cannot disturb a settled turn.

**Privacy.** A candidate turn is sent to the evaluator as raw transcript text:
the rendered transcript slice includes tool output and is not filtered through
[`secret_policy`](config.md#secrets-and-secret-references-1032) redaction.
Whatever the evaluator model returns is stored as plain text in
`workspace/improvement_inbox.jsonl` and can later be exported into the vault.
Point this extension at a local or otherwise trusted evaluator model, and treat
the inbox as sensitive as the conversations it observed.

| Option | Default | Meaning |
|--------|---------|---------|
| `model` | `effective_default_model(config)` | Evaluator model; the effective default is the sidecar pointer, then `config.default_model`. With neither, evaluation is skipped. |
| `dedup_ttl_seconds` | `3600` | In-memory suppression window for an identical trigger signature. |
| `max_transcript_chars` | `12000` | Tail budget of the rendered transcript sent to the evaluator. |

There is no default configuration: the extension runs only when an
`extensions.improvement_scout` entry is present and enabled, for example:

```json
{
  "extensions": {
    "improvement_scout": {
      "enabled": true,
      "type": "improvement_scout",
      "options": { "model": "gemini-3.8-flash", "dedup_ttl_seconds": 3600 }
    }
  }
}
```

Triage is deliberately separate from the runtime. The opt-in
`contrib/skills/improvement-inbox` skill provides `improvement_inbox_list`,
`improvement_inbox_show`, `improvement_inbox_summary`,
`improvement_inbox_dismiss`, `improvement_inbox_clear`,
`improvement_inbox_export`, `improvement_retro`, and `toil_audit`. Export
appends pending records to `agent/pages/decafclaw/improvement-inbox.md` in the
vault. The skill's `SCHEDULE.md` describes a nightly export sweep; like every
contrib schedule, it stays disabled until the operator copies it into the
admin schedules directory.

## Procedural toil audit

The scout only sees negative friction. [`decafclaw toil audit`](toil-audit.md)
is the companion batched scan for invisible procedural toil: it mines
conversation archives for repeated 2–5-call sequences, normalizes variable
arguments, counts distinct conversations, and reports defensive shell
pipelines, read-after-write verification, and discovery ceremony at three or
more strikes. New candidates can be exported into the same
`workspace/improvement_inbox.jsonl` inbox as `toil_strike_3` records.

## Scope

Extensions cannot change `TurnLifecycle` or `EventBus` subscription/dispatch, and they do not control slot ordering or message assembly — `PRE_COMPOSE` hooks return contributions or legacy facts, and core mounts them.
