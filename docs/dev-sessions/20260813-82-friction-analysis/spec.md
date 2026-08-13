Periodically analyze conversation archives to identify things the user keeps correcting or repeating, then propose adding them to AGENT.md or memory.

Pattern from Claude Code: "If user told Claude the same thing in 2+ sessions... that's a PRIME candidate for a persistent instruction."

Implementation ideas:
- Run during heartbeat or as a `!friction` command
- Scan recent archives for user messages containing corrections ("no, don't...", "I told you to...", "stop doing...")
- Group by theme and propose AGENT.md additions or memory entries
- Could use the cheap judge model (like reflection) for classification

This closes the loop between user feedback and system prompt evolution.

Ref: Claude Code system prompt analysis

### Verifiable acceptance criteria

- CRITERION: WHEN the friction analysis scanner runs over conversation archives containing user messages with repeated correction patterns across multiple sessions, THE SYSTEM SHALL group them by theme and emit proposed AGENT.md additions or memory entries.
  CHECK: `pytest tests/test_friction.py::test_friction_analysis_groups_corrections` (asserts scan correctly identifies patterns and proposes entries from mock archives) passes.
  VERIFIED DISCRIMINATING: Fails currently because `tests/test_friction.py` and friction analysis implementation do not exist.

- CRITERION: WHEN triggered via the `!friction` command, THE SYSTEM SHALL scan recent conversation archives for user correction messages and output a summary of proposed persistent instruction updates.
  CHECK: `pytest tests/test_friction.py::test_friction_command_execution` passes.
  VERIFIED DISCRIMINATING: Fails currently because the `!friction` command handler and friction scanner do not exist.

### Regression guards

- GUARD: The existing test suite (`make test`) passes without regressions.

## Tier: auto-ok

**Reason:** User approved the proposed acceptance criteria and triage assessment, resolving initial ambiguity and authorizing `auto-ok` execution.
