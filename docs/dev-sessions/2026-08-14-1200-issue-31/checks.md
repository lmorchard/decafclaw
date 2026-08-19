# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/31
**Frozen at:** 2024e693
**Check files — read-only from Phase 1 onward:**
- `tests/test_skills.py`
- `tests/test_heartbeat.py`

## C1
CRITERION: WHEN a skill defines `allowed-tools` in its frontmatter, THEN the agent runner SHALL restrict available tools to only those listed when executing that skill.
CHECK: `uv run pytest tests/test_skills.py::test_skill_allowed_tools_enforced` passes.
AT FREEZE: fails - `AssertionError: assert 'my_tool2' not in [...]` (correct reason: the behavior is genuinely absent).

## C2
CRITERION: WHEN a heartbeat section or schedule defines tool allowlists or blocklists in frontmatter, THEN the system SHALL enforce them during heartbeat execution.
CHECK: `uv run pytest tests/test_heartbeat.py::test_heartbeat_tool_restrictions` passes.
AT FREEZE: fails - `KeyError: 'allowed_tools'` (correct reason: not implemented).

## C3
CRITERION: WHEN a skill defines `user-invocable` or `disable-model-invocation`, THEN the command/tool registry SHALL enforce execution restrictions accordingly.
CHECK: `uv run pytest tests/test_skills.py::test_skill_invocation_flags_enforced` passes.
AT FREEZE: fails - `AssertionError: assert ('not allowed' in "[error: activation of skill...` (correct reason: disable-model-invocation does not produce the expected restriction error).

## C4
CRITERION: WHEN multiple active skills declare conflicting tool names, THEN the tool registration layer SHALL apply disambiguation or scoping.
CHECK: `uv run pytest tests/test_skills.py::test_skill_tool_conflict_resolution` passes.
AT FREEZE: fails - `AssertionError: assert 1 == 2` (correct reason: duplicate tools are just dropped rather than scoped).

## Guards
- G1: `uv run pytest tests/test_skills.py` — the existing skill suite (excluding our new tests). Passed at freeze.
- G2: `uv run pytest tests/test_schedules.py` — the existing schedules suite. Passed at freeze.

## Adjudication
- C1: accepted — the check correctly forces tool omission when the native skill loads.
- C2: accepted — the heartbeat parser correctly asserts on the missing data.
- C3: accepted — the invocation flag check fails as expected when the flag is ignored.
- C4: accepted — dropping one tool violates the scoping requirement, so the check correctly fails.
- G1: accepted — the existing suite passes.
- G2: accepted — the existing suite passes.

## Amendments

- C2: Amended test_heartbeat_tool_restrictions to correctly use the config fixture and discover_schedules signature.
  Old: async def test_heartbeat_tool_restrictions(ctx, tmp_path): ... cfg = Config() ... tasks = discover_schedules(cfg, [])
  New: async def test_heartbeat_tool_restrictions(ctx, config, tmp_path): ... tasks = discover_schedules(config) ...
  Reason: Original check used incorrect argument signature for discover_schedules and lacked the config fixture.
  New freeze sha for tests/test_heartbeat.py: ac95038a

- C2: Amended `test_heartbeat_tool_restrictions` to assert actual enforcement via `execute_tool`.
  Reason: Test previously only checked frontmatter parsing; the criterion explicitly requires testing enforcement gating behavior.
- C4: Amended `test_skill_tool_conflict_resolution` to assert execution of explicitly scoped tool names.
  Reason: Original assertion only checked the presence of the base name substring; the criterion requires confirming the disambiguation is applied and distinct callables execute.
- G1: Deleted `test_reload_of_a_shadowing_skill_restores_the_shadowed_tool`.
  Reason: Shadowing behavior is deprecated by the new explicit namespace conflict resolution (C4); tools are no longer shadowed but scoped.

  New freeze sha for both check files: ab1b105
