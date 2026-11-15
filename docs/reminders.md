# Scoped Reminders

Admin-authored rules that run against a tool call's **finalized arguments** before dispatch. A rule either blocks the call with correction guidance or lets it run and adds one short reminder to the model's context (#1127).

The design rationale and experiment plan live in
[the dev-session spec](dev-sessions/2026-11-13-1445-issue-1127-scoped-reminders-experiment/spec.md).

## Why

Standing instructions sit in the system prompt on every turn, whether or not they apply, so they cost context and attention in every conversation. Reflection catches mistakes after a tool has already changed something. A scoped reminder is the narrow middle: a deterministic check, paid only when a matching call actually happens.

## Rules file

Rules live at `data/{agent_id}/reminders.json` — the admin tier, so the agent's own file tools cannot author or edit them. "Trusted" is enforced by layout, not convention.

```json
{
  "rules": [
    {
      "id": "no-rm-rf-root",
      "tool": "shell",
      "match": [{"path": "command", "pattern": "rm\\s+-rf\\s+/"}],
      "action": "reject",
      "message": "Refusing: this would delete the filesystem root. Scope the path to a directory and re-issue."
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `id` | Stable identifier. Recorded in the ledger and the event, quoted in terse rejections. |
| `tool` | Exact tool name, or `*` for every tool. |
| `match` | List of predicates over the argument dict. All must hold (AND). Empty list matches every call of that tool. |
| `action` | `reject` stops the call before dispatch. `remind` runs the call and injects one ephemeral message. |
| `message` | Correction guidance shown to the model. |

Each predicate has a `path` (dotted path into the arguments, e.g. `command`, `args.0`) and exactly one operator, which carries its own operand:

```json
{"path": "command", "pattern": "rm\\s+-rf\\s+/"}
{"path": "command", "contains": "make"}
{"path": "cwd", "equals": "/home/les/repo"}
{"path": "env.CI", "exists": true}
```

| Operator | Matches when |
|---|---|
| `equals` | The value at `path` equals the operand. |
| `contains` | A string value contains the operand, or a list value contains it. |
| `pattern` | A string value matches the operand as a regular expression (search, not full match). |
| `exists` | The path resolves, whatever the value. The operand is ignored. |

A path that does not resolve is never a match for `equals`, `contains`, or `pattern`: absence does not trigger a reminder.

Malformed rules — bad JSON, unknown operator, missing field, invalid regex — are skipped with a warning. Tool execution never fails because of the rules file.

## Actions

**reject** returns `[error: <message>]` to the model without dispatching, so the call has no side effect and the model gets specific guidance to correct it.

**remind** lets the call run, then the agent loop appends a user-role message with a `[scoped-reminder]` prefix and an explicit non-authorship disclaimer ("the user did not send this"), mirroring the loop breaker nudge. The message is ephemeral: it goes into the LLM-facing message list only and is never archived, so a restart cannot resurrect it. It stays separate from the tool result so it cannot be confused with untrusted tool output.

The workflow engine's `wf.tool_call` path gets the same gate for rejection; remind matches are recorded there but no message is injected, because workflows own their message construction.

## Budgets

| Setting | Default | Effect |
|---|---|---|
| `reminders.enabled` | `false` | The whole gate is off unless enabled. This is an experiment. |
| `reminders.max_matches_per_rule` | `3` | Reject rules keep rejecting after this budget but switch to terse text. Remind rules stop injecting. |
| `reminders.max_per_turn` | `2` | Cap on injected reminders per turn, bounding context growth. |

Reject rules never expire: a safety gate that lapses is not a safety gate. Only the guidance text gets shorter.

Correction attempts are bounded by the existing [loop breaker](loop-breaker.md), not a new mechanism: a model that retries an identically rejected call advances its nudge → redirect → stop ladder.

## Recording

Every match that changes behavior publishes a `scoped_reminder` event (`rule_id`, `tool`, `action`, `terse`) and appends one JSON line to `{conv_dir}/reminders.jsonl`, colocated with the conversation's other sidecars. Match counts seed from that ledger, so budgets survive a restart.

## Boundaries

Rules can only reject or remind. They never approve, elevate, or bypass shell approval, skill permissions, child tool restrictions, or workflow allowlists. Workspace-authored rules are out of scope: only `data/{agent_id}/reminders.json` is read.

## Configuration

```json
{
  "reminders": {
    "enabled": true,
    "max_matches_per_rule": 3,
    "max_per_turn": 2
  }
}
```

Environment overrides follow the standard pattern: `REMINDERS_ENABLED`, `REMINDERS_MAX_MATCHES_PER_RULE`, `REMINDERS_MAX_PER_TURN`.

## Evaluating the experiment

The spec's eval set is `evals/reminders.yaml`: a genuine mistake (blocked), a harmless lookalike (must not fire), and a correction attempt (retries with corrected arguments and succeeds). Run it with `uv run python -m decafclaw.eval evals/reminders.yaml` and report false positives, added calls, and added tokens. The point is evidence for adopting or dropping the mechanism, not the rule format.
