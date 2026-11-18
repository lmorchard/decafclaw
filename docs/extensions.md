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

Slots mount in one invariant order (#1486):

| Slot | Mount point | Archived? |
|------|-------------|-----------|
| `HEAD` | After the system prompt, before history | No — replays at the same offset so the provider prefix cache stays stable (#1117) |
| `DELTA` | After archived history and this turn's other chronological injections (wiki, journal, memory), before tail envelopes | Yes |
| `TAIL` | Directly before the current user message | Yes |

Messages follow the composer's normal role filter: `system`, `user`,
`assistant`, and `tool` are sent as-is, while the composer's internal
injection roles (`vault_retrieval`, `conversation_notes`, `recent_journal`,
`skill_hint`, `recalled_facts`, and friends) are remapped to `user`. A
`DELTA` or `TAIL` message with any other role is archived but left out of
the provider request; an unsupported role in `HEAD` is skipped with a
warning.

`DELTA` and `TAIL` contributions are archived with the rest of this turn's
injections and replayed from history on later turns. A hook that returns the
same items every turn therefore duplicates them in history — return only new
items. `HEAD` mounts at the same offset after the system prompt every turn
and is never archived, so keep it static per conversation: it sits in the
cached prompt prefix (#1117).

Token accounting and Context Inspector visibility come from the
contribution's `source_entry`, which is appended to the turn's `sources`
list. A contribution without one still mounts its messages, but adds no
source row and no estimated tokens. Malformed contributions and unsupported
returns are logged and skipped.

Legacy `str` / `list[str]` returns are coerced to one `ContextContribution`
in the `TAIL` slot containing the bounded `<recalled_facts>` envelope: the
configured timeout and `lifecycle.pre_compose_token_ceiling` still apply, so
existing extensions (including `mem0`) need no changes. Structured
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

## Type registry

```python
from decafclaw.extensions import register_extension_type

register_extension_type("mem0", Mem0Extension)
```

A factory has the shape `Callable[[dict[str, Any], Config], Extension]` — it receives the entry's `options` dict and the whole `Config`, and returns an extension instance. Registering an existing type name replaces it and logs a warning.

Registry helpers: `register_extension_type(name, factory, *, default_enabled=False)`, `get_extension_type(name)`, `get_extension_type_meta(name)`, `get_default_enabled_extension_types()`, `list_extension_types()`, and `clear_extension_types()` (test isolation).

`default_enabled=True` marks a core type that `load_config` seeds when `config.json` defines no entry for it. The flag lives in the `ExtensionTypeMeta` record (`get_extension_type_meta`) and is replaced along with the factory on re-registration, so a test or embedder that swaps a type also chooses its default.

Built-in modules register themselves at import time. `ensure_builtin_extension_types()` imports the built-in modules (`diagnostics_and_telemetry`, `scratchpad_notes`, `vault_retrieval`, `improvement_scout`, `mem0`) lazily so `BaseExtension` is already defined (avoids an import cycle), registers any missing name, and is idempotent. Both `load_config` (for seeding) and `init_extensions` call it.

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
`DELTA` contribution with role `conversation_notes` and a `notes` source
entry. The composer archives it with the turn, so later turns replay it from
history; the cursor keeps it from repeating.

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
the user message, scores candidates by similarity, recency, and importance,
suppresses pages the conversation already surfaced, trims to the turn's token
budget, and returns a `TAIL` `ContextContribution` holding the
`vault_retrieval` envelope plus a `memory` source entry.

Core computes fixed costs (system prompt, tools, history, response reserve)
and publishes the remainder on `ComposerState.dynamic_token_budget` before
dispatching hooks; the extension trims to that budget, capped by its
`max_tokens` option. It also writes the turn's `last_retrieved_context_text`
and `last_memory_results` to composer state for reflection and the Context
Inspector, publishes the `vault_retrieval` UI event, and publishes
`retrieval_event` telemetry.

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

`src/decafclaw/extensions/mem0.py` provides local-first semantic memory: embedded Qdrant on disk, FastEmbed embeddings, and a local OpenAI-compatible LLM endpoint. It subscribes `PRE_COMPOSE` (search) and `ON_TURN_SETTLE` (fact extraction), honors `ctx.skip_memory_extension`, and reads options with defaults:

| Option | Default |
|--------|---------|
| `qdrant_path` | `data/mem0_qdrant` |
| `embedder_model` | `BAAI/bge-small-en-v1.5` |
| `llm_base_url` | `http://synobian:4100/v1` |
| `llm_model` | `mandelbrot3-flint` |
| `api_key` | `""` (falls back to `local`) |
| `top_k` | `5` |

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
      "options": { "model": "gemini-2.5-flash", "dedup_ttl_seconds": 3600 }
    }
  }
}
```

Triage is deliberately separate from the runtime. The opt-in
`contrib/skills/improvement-inbox` skill provides `improvement_inbox_list`,
`improvement_inbox_show`, `improvement_inbox_summary`,
`improvement_inbox_dismiss`, `improvement_inbox_clear`,
`improvement_inbox_export`, and `improvement_retro`. Export appends pending
records to `agent/pages/decafclaw/improvement-inbox.md` in the vault. The
skill's `SCHEDULE.md` describes a nightly export sweep; like every contrib
schedule, it stays disabled until the operator copies it into the admin
schedules directory.

## Scope

Extensions cannot change `TurnLifecycle` or `EventBus` subscription/dispatch, and they do not control slot ordering or message assembly — `PRE_COMPOSE` hooks return contributions or legacy facts, and core mounts them.
