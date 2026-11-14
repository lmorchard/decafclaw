# Scoped Reminders Before Matching Tool Calls Execute — Experiment Design

**Status:** drafted for delivery (burndown run 2026-11-13)
**Date:** 2026-11-13
**Owner:** Les (via agent delivery)
**Audience:** human + agent implementer
**Issue:** [#1127](https://github.com/lmorchard/decafclaw/issues/1127)

## Problem

Standing instructions consume context even when they are irrelevant. Reflection can also notice a known mistake only after a tool changes external state.

## Goals

- Test trusted, admin-authored rules against finalized tool arguments before execution.
- A matching rule can add a reminder or reject the call with correction guidance.
- Scope rules by tool and relevant argument fields.
- Bound repeated injections and correction attempts.
- Record matched rule identifiers for inspection and evaluation.
- Keep capability grants and approval decisions in existing permission mechanisms; workspace-authored rules must not grant authority or replace trusted rules.

## Non-goals

- Mid-stream interruption, AST matching, and model-based rule judges in the first experiment; defer until the smaller experiment proves useful.

## Current state (as of base `origin/main@579bdb0`)

- Standing instruction / memory paths consume context regardless of relevance.
- Reflection surfaces known mistakes post-hoc.
- `loop_breaker.py` is the existing precedent for trusted mid-turn injection: ephemeral user-role nudge/redirect with an explicit non-authorship disclaimer, appended to `self.messages` only (never archived), plus a published event for observability.

## Design proposal

### 1. Rule format and location (trusted, admin-authored)

Rules live in `data/{agent_id}/reminders.json` — an admin file at the agent data root, structurally read-only to the agent's file tools (same protection tier as `mcp_servers.json` and `skill_permissions.json`). "Trusted" is enforced by layout, not convention: the agent cannot author or modify rules.

Rule shape (deterministic; every field machine-checkable):

```json
{
  "rules": [
    {
      "id": "no-rm-rf-root",
      "tool": "shell",
      "match": [
        {"path": "command", "pattern": "rm\\s+-rf\\s+/"}
      ],
      "action": "reject",
      "message": "Refusing: this would delete the filesystem root. Scope the path to a specific directory and re-issue."
    }
  ]
}
```

- `tool`: exact tool name, or `"*"` for any tool (first slice: exact + wildcard only).
- `match`: list of predicates over the **finalized** argument dict; all must hold (AND). Each predicate is a `path` (dot path into args, e.g. `"command"`, `"args.0"`) plus one operator: `equals` / `contains` / `pattern` (regex) / `exists`.
- `action`: `"reject"` or `"remind"`.
- `message`: correction guidance shown to the model on match.

Malformed rules (bad JSON, unknown operator, unresolvable path) fail open: the rule is skipped with a warning log; tool execution never breaks because of the reminder file.

### 2. Hook point: finalized arguments, before dispatch

The check runs in `_execute_single_tool_impl` (`tool_execution.py`) after `fn_args` parsing and before `execute_tool()` dispatch — the single chokepoint every tool call passes through (direct registry tools, skill extras, and MCP bridges alike), so direct calls and enabled tool bridges apply the same rule boundary.

- Rules load once per agent start (and on config hot-reload); the check is a read-only, deterministic pass over one call's args — no shared mutable state, safe under concurrent batch execution (each call already runs in its own forked ctx).
- The check is an **additional** pre-execution gate: it can only reject or remind. It never approves, so it cannot bypass shell approval, skill permissions, or child tool restrictions — those gates are untouched and still apply to every call that passes the check.

### 3. Actions: reject and remind

- **reject** — the call does not execute (no side effect). The tool result is `ToolResult(text="[error: <message>]")`, giving the model specific correction guidance it can act on.
- **remind** — the call executes normally; after results are collected, a trusted reminder is injected as an **ephemeral message** mirroring the loop-breaker nudge: role `user` (models weight user-role directives more heavily), explicit non-authorship disclaimer ("the agent runtime sent this, not the user" — the #680 lesson), `[scoped-reminder]` prefix, appended to `self.messages` only — never archived, so `restore_history` can never resurrect it. Reminders stay distinguishable from untrusted tool output: they are separate messages, not text inside a tool result.

### 4. Budgets (no retry loops, no context tax)

- **Reject rules never expire.** A matching call is always rejected — a safety gate does not lapse. The budget bounds guidance repetition: after `reminders.max_matches_per_rule` (default 3) matches in a conversation, rejections continue but with terse text ("still not allowed") instead of the full message.
- **Remind rules expire.** After `max_matches_per_rule` matches, the rule stops injecting — advisory reminders are context tax once repeated, and standing-instruction context consumption is the problem this experiment exists to solve.
- **Correction attempts** are bounded by the existing loop breaker, not a new mechanism: if the model retries an identically rejected call, `loop_breaker` advances nudge → redirect → stop as it does today.
- Per-turn injection cap `reminders.max_per_turn` (default 2) bounds worst-case context growth in one turn.
- Config: `RemindersConfig` dataclass (`enabled`, `max_matches_per_rule`, `max_per_turn`) in `config_types.py`; `reminders.enabled` defaults to **false** — the experiment is opt-in.

### 5. Recording for inspection and eval

- Every match publishes a `scoped_reminder` event (`rule_id`, `tool`, `action`) on the existing EventBus — the same observability channel as `loop_breaker` and `tool_status`.
- Matches are appended to a per-conversation sidecar (`{conv_dir}/reminders.jsonl`) for inspection and eval analysis (false positives, added calls/tokens).

### 6. Safety boundaries

- Rules cannot grant capability: the only actions are reject and remind; no rule can approve, elevate, or bypass an existing permission decision.
- Workspace-authored rules are explicitly out of scope for the first slice (the acceptance criterion's "or unsupported paths are explicitly excluded"): only `data/{agent_id}/reminders.json` is read.

## Acceptance criteria mapping

- Matching rejected call → no side effect + correction guidance — §3 (reject).
- Unrelated call → no reminder, normal result — §2 (check is a no-op when nothing matches).
- Repeated matches stop within budget — §4.
- Rules cannot bypass approvals/permissions — §2, §6.
- Direct calls and enabled tool bridges apply same boundary or are explicitly excluded — §2 (single chokepoint); workspace-authored rules explicitly excluded in §6.
- Trusted reminders distinguishable from untrusted tool output — §3 (ephemeral message, non-authorship disclaimer).

## First implementation slice and proposed tests

- Slice: `reminders.json` loading + the check in `_execute_single_tool_impl` (both actions), budgets, and recording; `RemindersConfig` on the config dataclass.
- Unit tests: predicate matching (equals/contains/pattern/exists, AND semantics), reject produces no dispatch + error result, remind executes then injects ephemeral (never archived), budgets expire/terse as specified, malformed rules fail open.
- Eval set (per the issue): genuine mistakes (should match), harmless lookalikes (must not — false-positive check), correction attempts (after a reject the model retries with corrected args and succeeds). Report false positives and added calls/tokens.

## Open design decisions

- None blocking; defer mid-stream interruption, AST matching, model-based judges.

## Evidence

- `src/decafclaw/loop_breaker.py`, `src/decafclaw/tool_execution.py`, oh-my-pi TSR injection lifecycle docs.
- Coordination: #530 pre-write judge.
