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

## Scope

Extensions cannot change `TurnLifecycle` or `EventBus` subscription/dispatch, and they do not alter the composer's `<recalled_facts>` envelope or tail placement — hooks return facts, core composes them.
