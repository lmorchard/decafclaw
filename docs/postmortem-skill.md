# Postmortem Skill

User-invokable skill for blameless analysis of what went wrong in a
conversation. Produces a structured report, writes it to the vault, and
stops — without pivoting back into the original task.

Use when the agent has stumbled repeatedly and you want pattern-analysis
instead of apology-and-correction.

## Triggers

- Mattermost: `!postmortem`
- Web UI / terminal: `/postmortem`

Optional focus argument narrows the analysis:

```
/postmortem on the repeated tool errors in the wiki search
```

## Report structure

The skill instructs the agent to produce five H2 sections in order:

1. **Anomaly** — one or two sentences stating concretely what went wrong.
2. **Root cause hypotheses** — ranked list, each tagged with a category
   (ambiguous instruction, missing guardrail, tool-description gap,
   skill-body issue, LLM quirk, missing capability, user-facing
   ambiguity).
3. **Proposed patches** — specific, minimal, testable changes that each
   name the artifact being changed (file path, tool name, skill name).
4. **Systemic vs session-specific** — each proposed patch tagged as
   "Systemic" (needs a codebase change) or "Session-specific" (already
   handled in this conversation).
5. **Next steps** — short bulleted list of decisions the user needs to
   make. The skill does not act on any of them.

Blameless framing is enforced: no "I apologize" / "I should have" /
"my mistake" phrasing. Anomalies are attributed to systems, not the
agent as a person.

## Persistence

Every invocation writes the report to the vault at

```
agent/pages/postmortems/{YYYY-MM-DD-HHMM}-{slug}.md
```

with YAML frontmatter (`tags: [postmortem]`, `importance: 0.6`,
`summary: …`) and a `## Sources` section. The report is both delivered
inline and archived, so dream/garden can consolidate postmortem patterns
over time.

## Non-goals

- **No auto-trigger.** v1 is user-invoked only. An auto-trigger on
  repeated errors would either under- or over-fire. Revisit after we
  see how often the skill actually gets used.
- **No cross-session analysis.** v1 analyzes only the current
  conversation. A future version could use `conversation_search` to
  scan prior sessions, but the simpler current-conversation mode ships
  first.
- **No auto-applied patches.** The report surfaces proposed patches;
  the user reviews and commits them separately. The skill never edits
  the codebase on its own.

## Related

- `AGENT.md`'s "Name the pattern on repeated errors" rule is the inline
  reactive counterpart — a nudge the agent follows every turn. The
  postmortem skill is the explicit ritual for going deeper.
- The dream consolidation and garden skills will pick up the archived
  postmortem pages as regular vault content, rolling recurring root
  causes into higher-level pages over time.

## Configuration

Bundled skill at `src/decafclaw/skills/postmortem/SKILL.md`. No Python
tools, no per-skill config. Pre-approved tools: `vault_write`,
`current_time`.
