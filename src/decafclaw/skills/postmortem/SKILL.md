---
name: postmortem
description: Structured blameless analysis of what went wrong in this conversation — identifies root causes and proposes minimal, specific fixes
user-invocable: true
context: inline
allowed-tools: vault_write, current_time
---

# Blameless postmortem

Stop what you were doing. This is an explicit reflection ritual — not a task to resume. Produce a structured postmortem report about what went wrong in this conversation, then write it to the vault, then stop.

If `$ARGUMENTS` is set (the user supplied a focus), narrow the analysis to that topic. Otherwise analyze whatever is most salient across the conversation so far.

## What this is and isn't

- It *is* a blameless, systems-level analysis: what broke, why, and what minimal change would prevent a recurrence.
- It *isn't* an apology, a retry, or a pivot back into the original task.

**Forbidden phrasing** — do not use "I apologize", "I'm sorry", "I should have", "my mistake", "my fault", or any first-person self-flagellation. Frame anomalies systemically: "the agent did X", "the tool description lacked Y", "the instruction was ambiguous about Z".

## Report structure

Produce the report with these five sections, in order, as H2 headings. Keep each section tight — a few sentences or a short list, not paragraphs.

### `## Anomaly`

One or two sentences stating concretely what went wrong. Reference specific turns, tool calls, or outputs where possible.

### `## Root cause hypotheses`

A ranked list (most likely first). Each hypothesis must name a category:

- **Ambiguous instruction** — the user or system prompt underspecified a requirement.
- **Missing guardrail** — no rule existed to prevent the failure mode.
- **Tool-description gap** — a tool's description was unclear, missing a constraint, or actively misleading.
- **Skill-body issue** — a skill's SKILL.md produced unintended behavior.
- **LLM quirk** — model-level tendency (over-eagerness, sycophancy, context loss, pattern-matching a wrong template).
- **Missing capability** — the agent lacked a needed tool, data source, or skill.
- **User-facing ambiguity** — the UI or command surface let the user form a wrong mental model.

Each hypothesis: one line naming the category, then one line of evidence from the conversation.

### `## Proposed patches`

Specific, minimal, testable changes. Each patch must name the artifact being changed (file path, tool name, AGENT.md line, skill name) and what exactly to change. Examples of acceptable specificity:

- "Add a line to AGENT.md under 'Tool use' saying: …"
- "Tighten the `web_fetch` tool description: add 'Prefer workspace_read when the URL is a local path.'"
- "Create a new `foo` skill that …"

Not acceptable: "the agent should be more careful", "improve the prompt", "rewrite the tool".

### `## Systemic vs session-specific`

For each proposed patch, tag it as:

- **Systemic** — a change to the codebase (code, SKILL.md, AGENT.md, tool description, docs).
- **Session-specific** — already handled in this conversation; no codebase change needed.

### `## Next steps`

A short bulleted list of decisions the user needs to make: accept/reject each proposed patch, file an issue, add an eval case, etc. Do not act on any of them — just surface them.

## Persistence

After producing the report (and only after), write it to the vault so it can be consolidated later by the dream/garden processes.

1. Call `current_time` to get the current timestamp.
2. Derive a short kebab-case slug from the anomaly (3–5 words).
3. Call `vault_write` with:
   - **path**: `agent/pages/postmortems/{YYYY-MM-DD-HHMM}-{slug}.md`
   - **content**: the report, prefixed with YAML frontmatter:

     ```yaml
     ---
     tags: [postmortem]
     importance: 0.6
     summary: <one-sentence anomaly summary>
     ---
     ```

   - Append a `## Sources` section to the bottom referencing the conversation (ID if available, else a brief description of the session).

## Delivery and stop

1. Deliver the report text in the assistant response (the vault page is for archival; the user still reads the report inline).
2. End the turn. Do not re-engage with the original task. If the user wants to act on a proposed patch, they will ask in the next turn.
