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

## Type registry

```python
from decafclaw.extensions import register_extension_type

register_extension_type("mem0", Mem0Extension)
```

A factory has the shape `Callable[[dict[str, Any], Config], Extension]` — it receives the entry's `options` dict and the whole `Config`, and returns an extension instance. Registering an existing type name replaces it and logs a warning.

Registry helpers: `register_extension_type(name, factory)`, `get_extension_type(name)`, `list_extension_types()`, and `clear_extension_types()` (test isolation).

Built-in modules register themselves at import time. `init_extensions` imports `decafclaw.extensions.mem0` lazily so `BaseExtension` is already defined (avoids an import cycle).

## Loading

`init_extensions(config, bus)` runs once at startup (`runner.py`) and:

1. Imports built-in extension modules so their registrations run.
2. Iterates `config.extensions` in insertion order; `enabled: false` is skipped.
3. Resolves the factory with `ext_cfg.type or <config key>`.
4. Calls `factory(ext_cfg.options, config)`, then `manager.register(name, ext)` and `manager.enable(name)`.

Startup is fail-open: an unknown type, a factory exception, or an enable exception is logged with the extension name and skipped; the manager stays usable. Integration and malformed-config behavior: `tests/test_extensions_registry.py`.

## Secret options

`options` is a free-form dict, so dataclass `secret` metadata cannot reach it. Core redacts one fixed structural path, `extensions.*.options.api_key` (the mem0 convention), from admin reads of `config.json`, and `config show` masks it unless `--reveal`. An extension whose credential has a different key name must declare the path in [`secret_policy.redact_paths`](config.md#secrets-and-secret-references-1032); there is no general key-name matcher.

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

Extensions cannot change `TurnLifecycle` or `EventBus` subscription/dispatch, and they do not alter the composer's `<recalled_facts>` envelope or tail placement — hooks return facts, core composes them.
