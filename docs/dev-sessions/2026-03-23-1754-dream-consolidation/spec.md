# Dream Memory Consolidation

## Overview

Periodic "dreaming" process that reviews recent memories and conversations, then distills insights into the wiki knowledge base. Runs as a scheduled skill (hourly) and is also available as a user-invokable command. A separate, less frequent wiki gardening sweep handles holistic maintenance.

Inspired by human memory consolidation during sleep — the agent processes the day's experiences and integrates them into long-term structured knowledge.

## References

- Issue: https://github.com/lmorchard/decafclaw/issues/81
- Related: #15 (Wiki knowledge base — destination for distilled facts, now merged)
- Related: #89 (Selective memory loading — consolidation improves signal quality)
- Related: #8 (Scheduled tasks — consolidation uses the schedule frontmatter extension)

## Implementation Approach

This feature requires:

1. **Skill schedule frontmatter** — extend SkillInfo to parse a `schedule` field from SKILL.md. The schedule timer discovers skills with schedules alongside regular schedule files.
2. Two bundled command skills (`!dream`, `!garden`) that are also scheduled
3. An update to the wiki SKILL.md with the tl;dr convention

### Skill Schedule Frontmatter

Skills can now declare a cron schedule in their SKILL.md frontmatter:

```yaml
---
name: dream
description: Review recent memories and update the wiki
schedule: "0 * * * *"
effort: strong
required-skills:
  - wiki
user-invocable: true
context: fork
---
```

This means the skill is **both** a user-invokable command (`!dream`) **and** a scheduled task (runs hourly). The body is the prompt for both — no duplication.

### Trust Boundary

Schedule discovery from skills follows the same trust model:

- **Bundled skills** (`src/decafclaw/skills/`) — schedules honored
- **Admin-level skills** (`data/{agent_id}/skills/`) — schedules honored (admin-authored, trusted)
- **Workspace skills** (`workspace/skills/`) — schedules **ignored** (agent-writable, untrusted — prevents the agent from scheduling arbitrary tasks)

The schedule timer's `discover_schedules()` is extended to also scan discovered skills with a `schedule` field, filtering by trust level.

## Bundled Skill: Dream Consolidation

### File: `src/decafclaw/skills/dream/SKILL.md`

```yaml
---
name: dream
description: Review recent memories and conversations, distill insights into the wiki
schedule: "0 * * * *"
effort: strong
required-skills:
  - wiki
user-invocable: true
context: fork
---
```

### Phases

The prompt walks the agent through four phases:

#### Phase 1: Orient

- `wiki_list` to see what pages exist
- Read tl;dr summaries from key pages to understand current knowledge state
- `current_time` to note the current date/time for absolute date conversion

#### Phase 2: Gather

- `memory_recent` to get recent memories
- `memory_search` with broad queries to find memories related to active wiki topics
- `conversation_search` to find conversation content not captured in memories — look for overlooked insights, recurring themes, corrections, preferences
- Look for: facts, preferences, corrections, decisions, project context, recurring themes, things that were overlooked in the moment

#### Phase 3: Consolidate

For each finding from the gather phase:

- `wiki_search` for existing relevant pages
- If a page exists: `wiki_read` it, revise with new information, `wiki_write` the updated page
- If no page exists: create a new page with proper structure, `[[wiki-links]]`, and `## Sources` section
- Convert any relative dates ("yesterday", "last week") to absolute dates
- Add `[[wiki-links]]` between related pages
- Add/update tl;dr summaries on pages that exceed ~20 lines

#### Phase 4: Prune

- Check for contradictions between new information and existing wiki content
- Resolve in favor of newer, more authoritative information
- Note corrections in the Sources section ("Updated 2026-03-23: corrected per conversation")
- If nothing new to consolidate, respond with HEARTBEAT_OK

## Bundled Skill: Wiki Gardening Sweep

### File: `src/decafclaw/skills/garden/SKILL.md`

```yaml
---
name: garden
description: Wiki gardening sweep — merge, link, split, and tidy wiki pages
schedule: "0 3 * * 0"
effort: strong
required-skills:
  - wiki
user-invocable: true
context: fork
---
```

Runs weekly (Sunday at 3am). Focuses on:

- **Merge overlapping pages** — find pages covering similar topics, consolidate into one
- **Fix broken links** — find `[[links]]` that point to non-existent pages, create stubs or remove links
- **Add missing connections** — read pages and add `[[wiki-links]]` where topics are mentioned but not linked
- **Update stale tl;dr summaries** — re-read long pages and refresh their summaries
- **Split oversized pages** — break pages that have grown too large into sub-pages with a summary parent
- **Review orphan pages** — find pages with no backlinks, consider linking them from relevant pages

## tl;dr Convention

Update the wiki SKILL.md to include a convention for tl;dr summaries:

- Pages longer than ~20 lines should have a blockquote summary immediately after the `# Title`
- Format: `> tl;dr: One or two sentence summary of this page.`
- The consolidation process adds/updates these as pages grow
- Short pages don't need them

Example:

```markdown
# DecafClaw

> tl;dr: Les's AI agent project — a Mattermost chatbot with tools, skills, memory, and a wiki knowledge base.

DecafClaw is a minimal AI agent for learning how agent frameworks work...
```

## What's NOT in Scope (v1)

- **Custom tools** — no new Python tools needed. The existing wiki + memory tools are sufficient.
- **Memory pruning/archival** — consolidation distills into wiki but doesn't modify or delete memories. Memories remain the append-only source of truth.
- **Automated quality scoring** — no evaluation of whether consolidation improved the wiki. Trust the strong model and tune the prompt.
- **Cross-agent consolidation** — single agent instance only.
