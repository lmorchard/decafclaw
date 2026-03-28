# Context Sub-Objects — Plan

**Goal:** Group TokenUsage, ToolState, and SkillState into sub-dataclasses on Context. Keep conversation fields (`conv_id`, `user_id`, etc.) flat.

## Sub-dataclasses

```python
@dataclass
class TokenUsage:
    total_prompt: int = 0
    total_completion: int = 0
    last_prompt: int = 0

@dataclass
class ToolState:
    extra: dict = field(default_factory=dict)
    extra_definitions: list = field(default_factory=list)
    deferred_pool: list = field(default_factory=list)
    allowed: set | None = None
    preapproved: set = field(default_factory=set)
    preapproved_shell_patterns: list[str] = field(default_factory=list)
    current_call_id: str = ""

@dataclass
class SkillState:
    activated: set = field(default_factory=set)
    data: dict = field(default_factory=dict)
```

## Access pattern changes

| Old | New |
|-----|-----|
| `ctx.total_prompt_tokens` | `ctx.tokens.total_prompt` |
| `ctx.total_completion_tokens` | `ctx.tokens.total_completion` |
| `ctx.last_prompt_tokens` | `ctx.tokens.last_prompt` |
| `ctx.extra_tools` | `ctx.tools.extra` |
| `ctx.extra_tool_definitions` | `ctx.tools.extra_definitions` |
| `ctx.deferred_tool_pool` | `ctx.tools.deferred_pool` |
| `ctx.allowed_tools` | `ctx.tools.allowed` |
| `ctx.preapproved_tools` | `ctx.tools.preapproved` |
| `ctx.preapproved_shell_patterns` | `ctx.tools.preapproved_shell_patterns` |
| `ctx.current_tool_call_id` | `ctx.tools.current_call_id` |
| `ctx.activated_skills` | `ctx.skills.activated` |
| `ctx.skill_data` | `ctx.skills.data` |

## Fork semantics

- `fork()`: Create fresh sub-objects (new dataclass instances)
- `fork_for_tool_call()`: Share tools + skills (same reference), fresh tokens
- `for_task()`: Update to use sub-objects

## Steps

1. Define dataclasses + update Context.__init__, fork, fork_for_tool_call, for_task, publish
2. Update source files (agent, mattermost, tools, commands, schedules, etc.)
3. Update test files
4. Verify with make check && make test
