# Flexible Config (JSON/YAML) — Plan

## Status: Ready

## Overview

Six phases. Phase 1 creates the sub-dataclasses. Phase 2 rewrites the loader with JSON file support. Phase 3 does the codebase-wide refactor to nested access. Phase 4 updates tests. Phase 5 adds the CLI tool. Phase 6 updates docs. Each phase ends with lint + test passing and a commit.

The riskiest part is Phase 3 (mechanical refactor of ~50 access sites across ~20 modules). To de-risk, Phase 2 adds a temporary `__getattr__` on Config that maps old flat names to nested paths, so the codebase keeps working between phases. Phase 3 removes it.

---

## Phase 1: Create config_types.py — sub-dataclasses

**Goal**: Define all 8 sub-dataclasses with field metadata (secrets, env aliases). Pure new code, no existing files changed.

**File**: `src/decafclaw/config_types.py`

### Prompt

Create `src/decafclaw/config_types.py` with these dataclasses. Use `field(metadata={...})` for `secret` and `env_alias` markers. All types are standard: `str`, `int`, `float`, `bool`, `list[str]`.

Read the spec at `.claude/dev-sessions/2026-03-19-1420-flexible-config/spec.md` for the full field tables.

```python
from dataclasses import dataclass, field

@dataclass
class LlmConfig:
    url: str = "http://192.168.0.199:4000/v1/chat/completions"
    model: str = "gemini-2.5-flash"
    api_key: str = field(default="dummy", metadata={"secret": True})
    streaming: bool = True

@dataclass
class MattermostConfig:
    url: str = ""
    token: str = field(default="", metadata={"secret": True})
    bot_username: str = ""
    ignore_bots: bool = True
    ignore_webhooks: bool = False
    debounce_ms: int = 1000
    cooldown_ms: int = 1000
    require_mention: bool = True
    user_rate_limit_ms: int = 500
    channel_blocklist: list[str] = field(default_factory=list)
    circuit_breaker_max: int = 10
    circuit_breaker_window_sec: int = 30
    circuit_breaker_pause_sec: int = 60
    enable_emoji_confirms: bool = True
    stream_throttle_ms: int = 200

@dataclass
class CompactionConfig:
    url: str = ""       # empty = resolve from llm at load time
    model: str = ""     # empty = resolve from llm at load time
    api_key: str = field(default="", metadata={"secret": True})  # empty = resolve from llm
    max_tokens: int = 100000
    llm_max_tokens: int = 0  # 0 = use max_tokens
    preserve_turns: int = 5

@dataclass
class EmbeddingConfig:
    model: str = "text-embedding-004"
    url: str = ""       # empty = resolve from llm at load time
    api_key: str = field(default="", metadata={"secret": True})  # empty = resolve from llm
    search_strategy: str = "substring"

@dataclass
class HeartbeatConfig:
    interval: str = "30m"
    user: str = ""
    channel: str = ""
    suppress_ok: bool = True

@dataclass
class HttpConfig:
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 18880
    secret: str = field(default="", metadata={"secret": True})
    base_url: str = ""

@dataclass
class AgentConfig:
    data_home: str = "./data"
    id: str = "decafclaw"
    user_id: str = "user"
    max_tool_iterations: int = 200
    max_concurrent_tools: int = 5
    max_message_length: int = 50000
    tool_context_budget_pct: float = 0.10
    always_loaded_tools: list[str] = field(default_factory=list)
    child_max_tool_iterations: int = 10
    child_timeout_sec: int = 300

@dataclass
class TabstackConfig:
    api_key: str = field(default="", metadata={"secret": True, "env_alias": "TABSTACK_API_KEY"})
    api_url: str = field(default="", metadata={"env_alias": "TABSTACK_API_URL"})

@dataclass
class ClaudeCodeConfig:
    model: str = field(default="", metadata={"env_alias": "CLAUDE_CODE_MODEL"})
    budget_default: float = field(default=2.0, metadata={"env_alias": "CLAUDE_CODE_BUDGET_DEFAULT"})
    budget_max: float = field(default=10.0, metadata={"env_alias": "CLAUDE_CODE_BUDGET_MAX"})
    session_timeout: str = field(default="30m", metadata={"env_alias": "CLAUDE_CODE_SESSION_TIMEOUT"})

@dataclass
class SkillsConfig:
    tabstack: TabstackConfig = field(default_factory=TabstackConfig)
    claude_code: ClaudeCodeConfig = field(default_factory=ClaudeCodeConfig)
```

Add a module-level helper to check field metadata:

```python
def is_secret(dc_class, field_name: str) -> bool:
    """Check if a dataclass field is marked as secret."""
    ...

def get_env_alias(dc_class, field_name: str) -> str | None:
    """Get the env var alias for a dataclass field, if any."""
    ...
```

Lint after. No tests yet (Phase 4).

---

## Phase 2: Rewrite config.py — JSON loader + compat shim

**Goal**: Rewrite `load_config()` to support JSON file + env vars + defaults. Add temporary `__getattr__` on Config for backward compat. Existing code keeps working without changes.

**File**: `src/decafclaw/config.py`

### Prompt

Read the current `src/decafclaw/config.py` and the spec at `.claude/dev-sessions/2026-03-19-1420-flexible-config/spec.md`.

Rewrite `config.py` with:

**1. New top-level Config dataclass:**

```python
from .config_types import (
    LlmConfig, MattermostConfig, CompactionConfig, EmbeddingConfig,
    HeartbeatConfig, HttpConfig, AgentConfig, SkillsConfig,
)

@dataclass
class Config:
    llm: LlmConfig = field(default_factory=LlmConfig)
    mattermost: MattermostConfig = field(default_factory=MattermostConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)

    # Runtime-only (not in config file)
    system_prompt: str = ""
    discovered_skills: list = field(default_factory=list)

    @property
    def agent_path(self) -> Path:
        return Path(self.agent.data_home) / self.agent.id

    @property
    def workspace_path(self) -> Path:
        return self.agent_path / "workspace"

    @property
    def http_callback_base(self) -> str:
        if self.http.base_url:
            return self.http.base_url.rstrip("/")
        return f"http://{self.http.host}:{self.http.port}"

    @property
    def tool_context_budget(self) -> int:
        return int(self.compaction.max_tokens * self.agent.tool_context_budget_pct)

    @property
    def compaction_context_budget(self) -> int:
        return self.compaction.llm_max_tokens or self.compaction.max_tokens
```

**2. Temporary backward-compat `__getattr__`:**

Add a `__getattr__` that maps old flat names to nested access so existing code keeps working during migration. Log a deprecation warning on first use of each old name to help find stragglers.

Build the mapping automatically: iterate over sub-config dataclass fields and construct `{old_flat_name: (group_name, field_name)}`. Special cases:
- `mattermost_*` fields → `mattermost.*`
- `llm_*` fields → `llm.*`
- `compaction_*` fields → `compaction.*`
- `embedding_*` / `memory_search_strategy` → `embedding.*`
- `heartbeat_*` → `heartbeat.*`
- `http_*` → `http.*`
- `data_home` / `agent_id` / `agent_user_id` → `agent.*` (note: `agent_id` maps to `agent.id`, `agent_user_id` maps to `agent.user_id`)
- `max_tool_iterations` etc → `agent.*`
- `tabstack_*` → `skills.tabstack.*`
- `claude_code_*` → `skills.claude_code.*`
- `llm_stream_throttle_ms` → `mattermost.stream_throttle_ms` (moved field)

Also support `__setattr__` for the compat names — tests mutate config fields directly.

**3. Generic loader helpers:**

```python
def _parse_bool(value: str, default: bool = False) -> bool: ...  # keep existing

def _parse_list(value: str) -> list[str]:
    """Parse env var to list. Try JSON first, fall back to comma-split."""
    ...

def _load_sub_config(dc_class, json_data: dict, env_prefix: str, env_aliases: dict | None = None):
    """Populate a sub-dataclass from JSON + env vars.

    For each field in dc_class:
    1. Check env var {ENV_PREFIX}_{FIELD_NAME_UPPER}
    2. Check env alias if defined in field metadata
    3. Check json_data[field_name]
    4. Use dataclass default

    Type coercion based on field type annotation.
    """
    ...
```

**4. New `load_config()` function:**

```python
def load_config() -> Config:
    load_dotenv()

    # Bootstrap: resolve data_home and agent_id from env only
    data_home = os.getenv("DATA_HOME", AgentConfig.data_home)
    agent_id = os.getenv("AGENT_ID", AgentConfig.id)

    # Load config file if it exists
    config_path = Path(data_home) / agent_id / "config.json"
    file_data = {}
    if config_path.exists():
        file_data = json.loads(config_path.read_text())

    # Build each sub-config
    llm = _load_sub_config(LlmConfig, file_data.get("llm", {}), "LLM")
    mattermost = _load_sub_config(MattermostConfig, file_data.get("mattermost", {}), "MATTERMOST")
    agent = _load_sub_config(AgentConfig, file_data.get("agent", {}), "",
                             env_aliases={"data_home": "DATA_HOME", "id": "AGENT_ID", "user_id": "AGENT_USER_ID", ...})
    # ... etc for all groups

    # Fallback resolution
    if not compaction.url:
        compaction.url = llm.url
    if not compaction.model:
        compaction.model = llm.model
    if not compaction.api_key:
        compaction.api_key = llm.api_key
    if not embedding.url:
        embedding.url = llm.url.replace("/chat/completions", "/embeddings")
    if not embedding.api_key:
        embedding.api_key = llm.api_key

    return Config(llm=llm, mattermost=mattermost, compaction=compaction, ...)
```

The `agent` group needs special env var handling since its fields don't follow a clean prefix (e.g., `DATA_HOME` not `AGENT_DATA_HOME`). Use explicit `env_aliases` for these. Same for `embedding.search_strategy` → `MEMORY_SEARCH_STRATEGY`.

**5. Keep `_parse_bool` helper** — still used.

Lint and run existing tests — they should pass via the compat shim.

---

## Phase 3: Codebase refactor — nested access

**Goal**: Update all modules to use nested config access. Remove the compat `__getattr__`/`__setattr__`.

**Files**: Every module listed below.

### Prompt

The Config class now has nested sub-configs. Update all access from flat (`config.mattermost_url`) to nested (`config.mattermost.url`). Read each file, make the mechanical replacements, and move on.

**Mapping reference** (old → new):

LLM group:
- `config.llm_url` → `config.llm.url`
- `config.llm_model` → `config.llm.model`
- `config.llm_api_key` → `config.llm.api_key`
- `config.llm_streaming` → `config.llm.streaming`
- `config.llm_stream_throttle_ms` → `config.mattermost.stream_throttle_ms`

Mattermost group:
- `config.mattermost_url` → `config.mattermost.url`
- `config.mattermost_token` → `config.mattermost.token`
- `config.mattermost_bot_username` → `config.mattermost.bot_username`
- `config.mattermost_ignore_bots` → `config.mattermost.ignore_bots`
- `config.mattermost_ignore_webhooks` → `config.mattermost.ignore_webhooks`
- `config.mattermost_debounce_ms` → `config.mattermost.debounce_ms`
- `config.mattermost_cooldown_ms` → `config.mattermost.cooldown_ms`
- `config.mattermost_require_mention` → `config.mattermost.require_mention`
- `config.mattermost_user_rate_limit_ms` → `config.mattermost.user_rate_limit_ms`
- `config.mattermost_channel_blocklist` → `config.mattermost.channel_blocklist`
- `config.mattermost_circuit_breaker_max` → `config.mattermost.circuit_breaker_max`
- `config.mattermost_circuit_breaker_window_sec` → `config.mattermost.circuit_breaker_window_sec`
- `config.mattermost_circuit_breaker_pause_sec` → `config.mattermost.circuit_breaker_pause_sec`
- `config.mattermost_enable_emoji_confirms` → `config.mattermost.enable_emoji_confirms`

Compaction group:
- `config.compaction_url` → `config.compaction.url` (was a property — now a direct field, resolved at load time)
- `config.compaction_model` → `config.compaction.model`
- `config.compaction_api_key` → `config.compaction.api_key`
- `config.compaction_max_tokens` → `config.compaction.max_tokens`
- `config.compaction_llm_max_tokens` → `config.compaction.llm_max_tokens`
- `config.compaction_preserve_turns` → `config.compaction.preserve_turns`

Embedding group:
- `config.embedding_model` → `config.embedding.model`
- `config.effective_embedding_url` → `config.embedding.url` (resolved at load time now)
- `config.effective_embedding_api_key` → `config.embedding.api_key` (resolved at load time now)
- `config.memory_search_strategy` → `config.embedding.search_strategy`

Heartbeat group:
- `config.heartbeat_interval` → `config.heartbeat.interval`
- `config.heartbeat_user` → `config.heartbeat.user`
- `config.heartbeat_channel` → `config.heartbeat.channel`
- `config.heartbeat_suppress_ok` → `config.heartbeat.suppress_ok`

HTTP group:
- `config.http_enabled` → `config.http.enabled`
- `config.http_host` → `config.http.host`
- `config.http_port` → `config.http.port`
- `config.http_secret` → `config.http.secret`
- `config.http_base_url` → `config.http.base_url`

Agent group:
- `config.data_home` → `config.agent.data_home`
- `config.agent_id` → `config.agent.id`
- `config.agent_user_id` → `config.agent.user_id`
- `config.max_tool_iterations` → `config.agent.max_tool_iterations`
- `config.max_concurrent_tools` → `config.agent.max_concurrent_tools`
- `config.max_message_length` → `config.agent.max_message_length`
- `config.tool_context_budget_pct` → `config.agent.tool_context_budget_pct`
- `config.always_loaded_tools` → `config.agent.always_loaded_tools`
- `config.child_max_tool_iterations` → `config.agent.child_max_tool_iterations`
- `config.child_timeout_sec` → `config.agent.child_timeout_sec`

Skills group:
- `config.tabstack_api_key` → `config.skills.tabstack.api_key`
- `config.tabstack_api_url` → `config.skills.tabstack.api_url`
- `config.claude_code_model` → `config.skills.claude_code.model`
- `config.claude_code_budget_default` → `config.skills.claude_code.budget_default`
- `config.claude_code_budget_max` → `config.skills.claude_code.budget_max`
- `config.claude_code_session_timeout` → `config.skills.claude_code.session_timeout`

Properties that stay on top-level Config (no change needed):
- `config.agent_path`
- `config.workspace_path`
- `config.http_callback_base`
- `config.tool_context_budget`
- `config.compaction_context_budget`

**Modules to update** (sorted by impact):

1. `src/decafclaw/mattermost.py` — heaviest user (~20 field accesses)
2. `src/decafclaw/runner.py` — startup dispatch (~10 accesses)
3. `src/decafclaw/agent.py` — agent loop (~6 accesses)
4. `src/decafclaw/__init__.py` — entry point (~4 accesses)
5. `src/decafclaw/llm.py` — LLM calls (~4 accesses)
6. `src/decafclaw/compaction.py` — compaction (~2 accesses)
7. `src/decafclaw/embeddings.py` — embeddings (~3 accesses)
8. `src/decafclaw/http_server.py` — HTTP server (~5 accesses)
9. `src/decafclaw/web/websocket.py` — web socket handler (~2 accesses)
10. `src/decafclaw/web/auth.py` — web auth (~1 access)
11. `src/decafclaw/web/conversations.py` — web conversations (~1 access)
12. `src/decafclaw/prompts/__init__.py` — prompt loader (~1 access)
13. `src/decafclaw/tools/delegate.py` — child agent config (~4 accesses, plus `dataclasses.replace`)
14. `src/decafclaw/tools/core.py` — workspace path (~2 accesses)
15. `src/decafclaw/tools/workspace_tools.py` — workspace path (~2 accesses)
16. `src/decafclaw/tools/memory_tools.py` — search strategy (~3 accesses)
17. `src/decafclaw/tools/shell_tools.py` — workspace/agent path (~2 accesses)
18. `src/decafclaw/tools/skill_tools.py` — agent path (~1 access)
19. `src/decafclaw/tools/heartbeat_tools.py` — heartbeat config (~5 accesses)
20. `src/decafclaw/memory.py` — workspace path (~1 access)
21. `src/decafclaw/todos.py` — workspace path (~1 access)
22. `src/decafclaw/persistence.py` — workspace path (~2 accesses)
23. `src/decafclaw/eval/runner.py` — multiple fields + `dataclasses.replace`

**Special cases for `dataclasses.replace()`:**

In `delegate.py`, the current code:
```python
child_config = replace(config, max_tool_iterations=config.child_max_tool_iterations, system_prompt="...")
```
Becomes:
```python
child_config = replace(config,
    agent=replace(config.agent, max_tool_iterations=config.agent.child_max_tool_iterations),
    system_prompt="...",
)
```

In `eval/runner.py`:
```python
test_config = replace(config, data_home=tmp, agent_id="eval")
```
Becomes:
```python
test_config = replace(config, agent=replace(config.agent, data_home=tmp, id="eval"))
```

**After all modules updated**: remove `__getattr__` and `__setattr__` compat from Config in `config.py`.

Also update `mattermost.channel_blocklist` usage — it was a comma-separated string, now it's `list[str]`. Find any `.split(",")` calls on it and remove them.

Lint and test after.

---

## Phase 4: Update tests

**Goal**: Update all test files to construct Config with nested sub-configs and access fields via nested paths.

**Files**: `tests/conftest.py`, `tests/test_agent_turn.py`, `tests/test_compaction.py`, `tests/test_skills.py`, `tests/test_delegate.py`, `tests/test_workspace_tools.py`, `tests/test_tool_registry.py`, `tests/test_heartbeat.py`, `tests/test_web_auth.py`, `tests/test_web_conversations.py`, `tests/test_todos.py`, `tests/test_claude_code_permissions.py`, `tests/test_mcp.py`, `tests/test_handle_posted.py`, `tests/test_llm_streaming.py`, `tests/test_context.py`

### Prompt

Read each test file and update:

**1. Config construction in conftest.py fixture:**
```python
# Old:
Config(data_home=str(tmp_data), agent_id="test-agent", agent_user_id="testuser")

# New:
from decafclaw.config_types import AgentConfig
Config(agent=AgentConfig(data_home=str(tmp_data), id="test-agent", user_id="testuser"))
```

**2. Direct field mutation in tests:**
```python
# Old:
ctx.config.llm_streaming = False
ctx.config.max_tool_iterations = 5
ctx.config.compaction_preserve_turns = 2

# New:
ctx.config.llm.streaming = False
ctx.config.agent.max_tool_iterations = 5
ctx.config.compaction.preserve_turns = 2
```

**3. Direct field reads in assertions:**
```python
# Old:
assert ctx.config.max_tool_iterations == 10

# New:
assert ctx.config.agent.max_tool_iterations == 10
```

**4. Add new tests for the config loader itself** — in a new `tests/test_config.py`:
- `test_defaults` — Config() with no file/env gives expected defaults
- `test_json_file_loading` — write a config.json, verify fields loaded
- `test_env_var_override` — set env vars, verify they override file and defaults
- `test_env_alias` — verify TABSTACK_API_KEY works as alias for SKILLS_TABSTACK_API_KEY
- `test_list_field_comma` — `MATTERMOST_CHANNEL_BLOCKLIST=a,b` → `["a", "b"]`
- `test_list_field_json` — `MATTERMOST_CHANNEL_BLOCKLIST=["a","b"]` → `["a", "b"]`
- `test_fallback_resolution` — compaction/embedding fields resolve from llm
- `test_bootstrap_order` — data_home/id from env only, not from config file
- `test_secret_metadata` — verify secret fields are marked correctly
- `test_compat_removed` — verify old flat names raise AttributeError

Lint and run full test suite.

---

## Phase 5: CLI tool

**Goal**: Add `python -m decafclaw config` subcommand with show/get/set.

**Files**: `src/decafclaw/__init__.py` (or a new `src/decafclaw/cli.py`)

### Prompt

Read `src/decafclaw/__init__.py` to see how the current entry point works.

Add CLI config subcommand handling. When `sys.argv[1] == "config"`, dispatch to the config CLI instead of starting the agent. This can live in a new `src/decafclaw/config_cli.py` to keep it separate.

**`config_cli.py`:**

```python
import argparse
import json
from dataclasses import fields
from pathlib import Path
from .config import load_config, Config
from .config_types import is_secret

def cmd_show(args):
    """Show resolved config, optionally filtered by group."""
    config = load_config()
    reveal = args.reveal
    group_filter = args.group

    for group_field in fields(config):
        if group_field.name in ("system_prompt", "discovered_skills"):
            continue  # runtime-only
        group = getattr(config, group_field.name)
        if not hasattr(group, "__dataclass_fields__"):
            continue  # skip non-dataclass fields
        if group_filter and group_field.name != group_filter:
            continue

        _print_group(group_field.name, group, reveal)

def _print_group(prefix, dc_instance, reveal):
    """Print all fields of a dataclass with dotted prefix."""
    for f in fields(dc_instance):
        value = getattr(dc_instance, f.name)
        # Recurse for nested dataclasses (e.g., skills.tabstack)
        if hasattr(value, "__dataclass_fields__"):
            _print_group(f"{prefix}.{f.name}", value, reveal)
            continue
        display = _format_value(value, f, reveal)
        print(f"{prefix}.{f.name} = {display}")

def _format_value(value, field_info, reveal):
    if not reveal and field_info.metadata.get("secret") and value:
        return "****"
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)

def cmd_get(args):
    """Get a single config value."""
    config = load_config()
    parts = args.path.split(".")
    obj = config
    for part in parts:
        obj = getattr(obj, part, None)
        if obj is None:
            print(f"Unknown config path: {args.path}", file=sys.stderr)
            sys.exit(1)
    print(obj)

def cmd_set(args):
    """Set a config value in config.json."""
    config = load_config()
    config_path = config.agent_path / "config.json"

    # Load existing file or start fresh
    if config_path.exists():
        file_data = json.loads(config_path.read_text())
    else:
        file_data = {}

    # Navigate to the right nesting level
    parts = args.path.split(".")
    # Coerce value based on field type
    value = _coerce_value(config, parts, args.value)

    # Set in nested dict
    d = file_data
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value

    config_path.write_text(json.dumps(file_data, indent=2) + "\n")
    print(f"Set {args.path} = {value}")

def main():
    parser = argparse.ArgumentParser(prog="decafclaw config")
    sub = parser.add_subparsers(dest="command")

    show_p = sub.add_parser("show")
    show_p.add_argument("group", nargs="?")
    show_p.add_argument("--reveal", action="store_true")

    get_p = sub.add_parser("get")
    get_p.add_argument("path")

    set_p = sub.add_parser("set")
    set_p.add_argument("path")
    set_p.add_argument("value")

    args = parser.parse_args()
    if args.command == "show":
        cmd_show(args)
    elif args.command == "get":
        cmd_get(args)
    elif args.command == "set":
        cmd_set(args)
    else:
        parser.print_help()
```

**Wire into `__init__.py`:**
```python
if len(sys.argv) > 1 and sys.argv[1] == "config":
    from .config_cli import main as config_main
    sys.argv = sys.argv[1:]  # shift so argparse sees "config show" not "decafclaw config show"
    config_main()
    sys.exit(0)
```

**Add tests** in `tests/test_config_cli.py`:
- `test_show` — verify output format, secrets masked
- `test_show_reveal` — secrets shown
- `test_show_group` — filter by group name
- `test_get` — single value retrieval
- `test_set` — writes to config.json, verify file contents
- `test_set_creates_file` — creates config.json if missing

Lint and test after.

---

## Phase 6: Docs and cleanup

**Goal**: Update documentation, example files, CLAUDE.md.

**Files**: `.env.example`, `CLAUDE.md`, `README.md`, `docs/`

### Prompt

**1. Create `data/decafclaw/config.json.example`** — minimal example with comments-as-docs (JSON doesn't support comments, so use a separate `CONFIG.md` or inline descriptions in the example's values).

Actually, since JSON doesn't support comments, create a documented example as a markdown file `docs/config.md` that shows the full schema with descriptions, and a minimal `config.json.example` at the data level.

**2. Update `.env.example`** — add a note at the top pointing to config.json as the preferred approach. Keep the env var examples for secrets.

**3. Update `CLAUDE.md`**:
- Key files: add `config_types.py`, `config_cli.py`
- Conventions: update "Config via env vars" to mention config.json
- Update `dataclasses.replace()` convention with nested example

**4. Update `README.md`** — config table, CLI usage.

**5. Add `docs/config.md`** — full config reference with all groups, fields, defaults, env vars.

**6. Add Makefile target**: `make config` → `python -m decafclaw config show`

Lint after. Final commit.

---

## Dependency Graph

```
Phase 1 (config_types.py — pure new code)
  ↓
Phase 2 (config.py rewrite — loader + compat shim)
  ↓
Phase 3 (codebase refactor — nested access everywhere)
  ↓
Phase 4 (tests — update + add new config tests)
  ↓
Phase 5 (CLI tool)
  ↓
Phase 6 (docs + cleanup)
```

Phases are strictly sequential — each depends on the previous.

## Risk Notes

- **Phase 3 is the biggest risk** — 20+ modules, ~60 access sites. The compat shim from Phase 2 means we can do this incrementally (update a few modules, test, continue). If a module is missed, the shim catches it with a deprecation warning.
- **`channel_blocklist` type change** — was comma-separated string, now `list[str]`. Any code that calls `.split(",")` on it will break. Search for this pattern.
- **`enable_emoji_confirms` behavior change** — was auto-derived from `http_enabled`. Now defaults to `true`. Users who relied on auto-detection need to explicitly set it to `false` in config. Document this.
- **Test mutation patterns** — tests that do `ctx.config.field = value` need updating to `ctx.config.group.field = value`. The compat shim handles this during transition but must be removed.
