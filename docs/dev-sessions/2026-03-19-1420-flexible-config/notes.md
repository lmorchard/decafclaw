# Flexible Config (JSON/YAML) — Notes

## Session Recap

Redesigned the configuration system from a flat 54-env-var monolith to a nested, layered config system with JSON file support and a CLI tool.

### What we built
- **config_types.py** — 8 sub-dataclasses (LlmConfig, MattermostConfig, CompactionConfig, EmbeddingConfig, HeartbeatConfig, HttpConfig, AgentConfig, SkillsConfig) with field metadata for secrets and env aliases
- **config.py rewrite** — Generic `_load_sub_config()` loader: defaults → config.json → env vars
- **config_cli.py** — CLI: `decafclaw config show/get/set` with secret masking
- **resolved() pattern** — CompactionConfig and EmbeddingConfig have `resolved(config)` methods for LLM fallback resolution at call site
- **25 config loader tests + 9 CLI tests**

### Key design decisions
1. **Bootstrap phase** — `data_home` and `agent_id` resolved from env only (not config file) since they determine where the file lives
2. **Fallback at call site** — compaction/embedding `resolved(config)` method instead of loader-time resolution
3. **Field metadata** — `secret=True` for masking, `env_alias` for backward-compat env var names
4. **Compat shim** — temporary `__getattr__`/`__setattr__` enabled incremental migration, then removed
5. **List fields** — `list[str]` type with JSON parse → comma-split fallback for env vars
6. **Separate files stay separate** — mcp_servers.json, skill_permissions.json, shell_allow_patterns.json not merged

### Stats
- 54 flat config fields → 8 grouped sub-dataclasses
- ~20 source modules refactored to nested access
- ~15 test files updated
- 34 new tests (25 loader + 9 CLI)
- All 564 tests passing, lint + pyright + tsc clean

### Session observations
- The compat shim was crucial for de-risking the migration — enabled running tests between each batch of module updates
- Parallel agents for the codebase refactor saved a lot of time (5 agents covering 23 modules)
- `__future__.annotations` in config_types.py caused `f.type` to return strings instead of types — fixed by using `typing.get_type_hints()` in the loader
- Les's .env file leaked into config tests via `load_dotenv()` — fixed with autouse fixture
