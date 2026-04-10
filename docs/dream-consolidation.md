# Dream Memory Consolidation

DecafClaw can periodically "dream" — reviewing recent memories and conversations to distill insights into the [wiki knowledge base](wiki.md). A separate wiki gardening sweep handles structural maintenance.

## Commands

| Command | Schedule | Description |
|---------|----------|-------------|
| `!dream` / `/dream` | Hourly (`0 * * * *`) | Review recent memories/conversations, update wiki pages |
| `!garden` / `/garden` | Weekly (Sunday 3am: `0 3 * * 0`) | Structural wiki maintenance: merge, link, split, tidy |

Both commands can be configured with a specific model for quality wiki writing. They can be triggered manually or run automatically via [scheduled tasks](schedules.md).

## How It Works

### Dream Consolidation (`!dream`)

Runs through four phases:

1. **Orient** — survey existing wiki pages and their tl;dr summaries
2. **Gather** — scan recent memories and search conversations for new insights, corrections, preferences, and overlooked themes
3. **Consolidate** — update existing wiki pages or create new ones, add `[[wiki-links]]`, convert relative dates to absolute
4. **Prune** — resolve contradictions, note corrections in Sources sections

If nothing new is found, responds with HEARTBEAT_OK. In scheduled runs this is logged only, not posted to any channel.

### Wiki Gardening (`!garden`)

Focuses on structural quality:

- Merge overlapping pages
- Fix broken `[[wiki-links]]`
- Add missing connections between related pages
- Update stale tl;dr summaries
- Split oversized pages into sub-pages
- Review orphan pages (no backlinks)

## Skill Schedule Frontmatter

These skills use a new feature: **schedule frontmatter**. Skills can declare a cron expression in their SKILL.md:

```yaml
---
name: dream
schedule: "0 * * * *"
user-invocable: true
context: fork
---
```

This makes the skill both a user command and a scheduled task — no separate schedule file needed.

### Trust Boundary

Schedule frontmatter is only honored for:
- **Bundled skills** (`src/decafclaw/skills/`)
- **Admin-level skills** (`data/{agent_id}/skills/`)

Workspace skills (`workspace/skills/`) cannot self-schedule — this prevents the agent from creating arbitrary scheduled tasks.

File-based schedules in `data/{agent_id}/schedules/` take precedence over skill frontmatter if names collide.

## Customization

To change the consolidation schedule, create a file-based schedule that overrides the skill:

```markdown
---
schedule: "0 */3 * * *"
model: gemini-pro
required-skills:
  - wiki
---

(Your custom consolidation prompt here, or copy from the bundled skill.)
```

Save as `data/{agent_id}/schedules/dream.md` — it will override the bundled skill's schedule.

## tl;dr Convention

The consolidation process maintains tl;dr summaries on longer wiki pages:

```markdown
# DecafClaw

> tl;dr: Les's AI agent project — a Mattermost chatbot with tools, skills, memory, and a wiki.

(Full page content follows...)
```

Pages shorter than ~20 lines don't need summaries. The dream and garden processes add/update these automatically.
