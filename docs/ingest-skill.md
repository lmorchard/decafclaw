# Ingest Skill

User-invokable skill for one-shot ingestion of an external source into the
vault. Fetches the source, synthesizes it in the agent's own words, and
writes one primary page plus optional cross-linked updates to related
pages. Interactive counterpart to the scheduled
[linkding-ingest](https://github.com/lmorchard/decafclaw/blob/main/contrib/skills/linkding-ingest/SKILL.md)
and
[mastodon-ingest](https://github.com/lmorchard/decafclaw/blob/main/contrib/skills/mastodon-ingest/SKILL.md)
contrib skills — when you come across a single thing and want it captured
right now.

## Triggers

- Mattermost: `!ingest <source>`
- Web UI / terminal: `/ingest <source>`

## Input shapes

| Form | Example |
|------|---------|
| URL | `/ingest https://example.com/article` |
| Workspace file | `/ingest workspace/imports/paper.md` |
| Attachment | Upload a file in the web UI, then `/ingest` (or `/ingest <filename>`) |

Optional focus instruction, separated by ` — `:

```
/ingest https://example.com/post — focus on the security implications
```

The focus shapes the content emphasis of the written pages — what gets
pulled out and foregrounded. It does not override folder choice.

## Fetch mechanism

The agent picks the right tool for the source type:

- **URL**: tries `tabstack_extract_markdown` first. Falls back to
  `web_fetch` if tabstack fails (rate limit, unsupported site, credits
  exhausted). The agent stops if what came back looks like an error page
  or stub rather than real content — it won't fabricate.
- **Workspace file**: `workspace_read`.
- **Attachment**: `get_attachment`. Text files return content; images
  return media. Other binary formats (PDF, etc.) currently return only
  base64 metadata — the skill reports the limitation and stops. Extract
  text yourself and pass a workspace file for those.

## Output

One primary page plus zero or more secondary updates, capped at ~5 pages
total. Writes stay under `agent/pages/`.

- **Primary page**: a new page created by the agent in a topical folder
  of its choice (`agent/pages/tools/`, `agent/pages/papers/`,
  `agent/pages/people/`, etc.), OR an existing page updated in place if
  the vault already has a strong match. Includes YAML frontmatter with
  `tags` (including `ingested`), a `summary` line, `[[wiki-links]]` to
  related pages, and a `## Sources` section.
- **Secondary updates**: related pages that gain a sentence or a
  cross-link. Not rewritten wholesale.
- **Deferred candidates**: if the source touches more than ~5 pages, the
  extras are listed in the change summary as candidates for the next
  [garden](https://github.com/lmorchard/decafclaw/blob/main/src/decafclaw/skills/garden/SKILL.md)
  pass rather than edited this turn. Cross-graph link backfill is
  garden's job.

After writing, the agent ends the turn with a change summary:

```
Ingested: <source>
Primary page: [[Name]] (new | updated)
Secondary updates: [[A]], [[B]]
(Candidates for the next garden pass: [[C]], [[D]])
```

You follow up in a new turn if you want to expand a page, add more
sources, or trigger garden.

## Non-goals

- **No multi-URL ingest.** One source per invocation. Run it again for
  the next.
- **No pasted text as source.** The source is a reference (URL, path, or
  attachment), not inline body text.
- **No `log.md` append yet.** Per-ingest structured log entries are
  tracked in [#289](https://github.com/lmorchard/decafclaw/issues/289).
- **No index.md update yet.** LLM-maintained catalog of pages is tracked
  in [#288](https://github.com/lmorchard/decafclaw/issues/288).
- **No cross-vault link refinement.** Identifying `[[wiki-links]]` that
  should exist across pages untouched by this ingest is garden's job;
  [#287](https://github.com/lmorchard/decafclaw/issues/287) expands
  garden with contradiction / stale-claim / data-gap detection.

## Related

- [vault](./vault.md) — the knowledge base this skill writes into.
- [garden](https://github.com/lmorchard/decafclaw/blob/main/src/decafclaw/skills/garden/SKILL.md) — structural maintenance sweep that follows ingest activity.
- [dream](./dream-consolidation.md) — episodic consolidation (journal → pages).
- karpathy's LLM wiki pattern — the design inspiration.
