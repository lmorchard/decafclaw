Skills and heartbeat sections should support restricting which tools are available.

- `allowed-tools` frontmatter field in SKILL.md (parsed but not enforced)
- Tool allowlist/blocklist for heartbeat sections via frontmatter
- `user-invocable` / `disable-model-invocation` parsed but not enforced
- Multiple skills with conflicting tool names — no handling yet

Deferred from skills and heartbeat sessions.

## Verifiable acceptance criteria

- CRITERION: WHEN a skill defines `allowed-tools` in its frontmatter, THEN the agent runner SHALL restrict available tools to only those listed when executing that skill.
  CHECK: `pytest tests/test_skills.py::test_skill_allowed_tools_enforced` passes.

- CRITERION: WHEN a heartbeat section or schedule defines tool allowlists or blocklists in frontmatter, THEN the system SHALL enforce them during heartbeat execution.
  CHECK: `pytest tests/test_heartbeat.py::test_heartbeat_tool_restrictions` passes.

- CRITERION: WHEN a skill defines `user-invocable` or `disable-model-invocation`, THEN the command/tool registry SHALL enforce execution restrictions accordingly.
  CHECK: `pytest tests/test_skills.py::test_skill_invocation_flags_enforced` passes.

- CRITERION: WHEN multiple active skills declare conflicting tool names, THEN the tool registration layer SHALL apply disambiguation or scoping.
  CHECK: `pytest tests/test_skills.py::test_skill_tool_conflict_resolution` passes.

## Regression guards

- GUARD: `pytest tests/test_skills.py` and `pytest tests/test_schedules.py` pass and existing suites stay green.

## Tier: auto-ok

Approved by human review (THUMBS_UP reaction on proposal comment). Risk-gated architectural decisions ratified.

## Design decisions

- **Decision:** Tool restriction enforcement, heartbeat blocklist/allowlist, skill invocation flags, and skill tool namespace collision resolution.
  - **Why:** Approved by human review following initial proposal.
  - **Rejected:** Leaving architectural decisions unhandled.
