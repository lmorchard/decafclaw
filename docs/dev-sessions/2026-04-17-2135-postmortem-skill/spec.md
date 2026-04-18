# Spec: Postmortem skill

Tracking issue: [#279](https://github.com/lmorchard/decafclaw/issues/279)

## Problem

When the agent hits repeated errors in a conversation, it defaults to apology-and-correction rather than pattern-analysis. We already added a narrow AGENT.md rule ("Name the pattern on repeated errors") as an inline reactive nudge, but deferred a heavier blameless-postmortem ritual because baking it into every error path would be too heavyweight.

We want RCA available as an explicit, user-invoked ritual: the user says "stop and reflect on what just happened" and gets a structured report back.

## Goal

Ship a bundled `postmortem` skill that is:

- **User-invokable** via `/postmortem` (web UI) and `!postmortem` (Mattermost).
- **Lean** — SKILL.md-only for v1, no new Python tools.
- **Structured** — produces a report with named sections (Anomaly / Root cause hypotheses / Proposed patches / Next steps).
- **Archival** — persists the report as a vault page so the dream/garden processes can consolidate patterns across sessions over time.
- **Bounded** — stops when the report is delivered; does not pivot back into the original task.
- **Non-destructive** — proposes fixes, never auto-applies them.

## Non-goals

- Auto-triggering based on repeated errors. v1 is user-invoked only; revisit after we see how often it gets used and whether auto-trigger would add signal or noise.
- Cross-session pattern analysis. v1 analyzes only the current conversation. `conversation_search` is available for later versions.
- New Python tool code. If v1 needs only existing tools (`vault_write`, `current_time`, optionally `vault_search`/`vault_list`), keep it SKILL.md-only.
- Changing AGENT.md or the reactive "name the pattern" rule. Those stay as-is.

## Design

### Name and trigger

- Skill name: `postmortem`.
- Trigger: `!postmortem` / `/postmortem`.
- Rationale: "postmortem" has industry-standard associations (blameless, structured, shared artifact). `rca` is jargon. `retro`/`debrief` feel too lightweight for the intended depth.

### Frontmatter

```yaml
---
name: postmortem
description: Structured blameless analysis of what went wrong in this conversation — identifies root causes and proposes minimal fixes
user-invocable: true
context: inline
allowed-tools: vault_write, current_time
---
```

- `context: inline` so the skill sees the current conversation's full context. (`fork` would isolate it and force us to replay via `conversation_search`, which is both slower and unreliable for recent turns.)
- `allowed-tools` pre-approved so the skill doesn't prompt for confirmation mid-ritual.
- Not `always-loaded`. Skill body lives behind `activate_skill` as usual; user invocation activates it for the turn.

### Report structure

Prompt the agent to produce a markdown report with these sections in order:

1. **Anomaly** — What specifically went wrong? One or two sentences. Concrete and blameless (no "I should have…").
2. **Root cause hypotheses** — Ranked list. Each hypothesis names a *category*: ambiguous instruction, missing guardrail, tool-description gap, skill-body issue, LLM quirk, missing capability, user-facing ambiguity.
3. **Proposed patches** — Specific, minimal, testable changes. Each patch names the artifact being changed ("AGENT.md line 42", "tool `foo` description", "add skill `bar`") and what to change. No "rewrite everything."
4. **Systemic vs session-specific** — Which proposed patches belong in the codebase (systemic) vs which were just handled in this conversation (session-specific)?
5. **Next steps** — Short list of what the user should decide: accept/reject each proposed patch, file an issue, run an eval case, etc.

### Persistence

- Always write the report to `agent/pages/postmortems/YYYY-MM-DD-HHMM-slug.md` via `vault_write`.
- Slug derives from the anomaly framing (agent generates).
- Include a `## Sources` section with the conversation ID, for traceability.
- Include optional YAML frontmatter (`tags: [postmortem]`, `importance: 0.6`) so dream/garden pick it up.

### Turn boundary

The skill body ends with an explicit instruction: "Once the report is written and delivered, stop. Do not resume the original task unless the user asks." This matches the issue's "stop when done" requirement.

### `$ARGUMENTS` support

Optional focus arg: `/postmortem tool-call thrashing in the wiki search` lets the user narrow the analysis. Substituted into the skill body via the existing argument mechanism. If no args, agent analyzes whatever is salient.

## Decisions (confirmed 2026-04-17)

1. **Name**: `postmortem`. Trigger: `/postmortem`, `!postmortem`.
2. **Vault persistence**: always-write. Every invocation writes `agent/pages/postmortems/YYYY-MM-DD-HHMM-slug.md`. No `--save`/`--quick` flag in v1.
3. **Vault folder path**: `agent/pages/postmortems/` (default; assumed confirmed — if Les wants a different home, fix during Phase 1).
4. **Invocation context**: `inline`. Skill runs in the current turn with full conversation visible.
5. **Eval scope**: one case — seeded three-repeat tool error. Assert report structure (all five section headings present) and framing (no "I apologize"/"I should have" phrases; proposed patches reference specific artifacts).
6. **Blameless framing**: skill body explicitly forbids self-flagellation phrasing and requires third-person / system-level language. (Assumed — confirm during Phase 2 prompt tuning if output disagrees.)

## Acceptance

- `/postmortem` in the web UI triggers the skill and produces a structured report with the five sections above.
- The report is written to `agent/pages/postmortems/…md`.
- The agent does not resume the prior task after the report.
- The skill is pre-approved for its `allowed-tools` (no mid-ritual confirmations).
- At least one eval case validates the report structure and pattern-analysis framing.
- `docs/` has a page (or a section in an existing page) describing the skill.
