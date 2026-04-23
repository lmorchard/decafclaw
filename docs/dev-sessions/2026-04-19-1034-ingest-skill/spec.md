# Spec: `/ingest` skill — interactive one-shot ingestion

Tracking discussion: follow-up to the karpathy LLM-wiki analysis. Pairs with deferred issues #287 (garden lint), #288 (index.md), #289 (log.md).

## Problem

Our existing ingest skills (`linkding-ingest`, `mastodon-ingest`) are scheduled firehose-style: they pull from an aggregator on cron, filter for signal, and synthesize into per-source folders. We don't have the **interactive one-shot** mode — "I just came across this, file it away" — which karpathy's doc frames as the primary human-LLM wiki workflow.

The gap costs us: one-off reading doesn't get captured unless the user bookmarks it first (linkding-ingest) or posts about it on Mastodon (mastodon-ingest). Direct "process this now" ingestion is missing.

## Goal

Ship a bundled `ingest` skill that is:

- **User-invokable**: `/ingest <source>` in web UI, `!ingest <source>` in Mattermost.
- **Multi-input**: accepts URL, workspace file path, or file attachment. One source per invocation.
- **Self-directing**: the agent picks the fetch mechanism, extracts content, searches the existing vault, and decides the output folder.
- **Multi-page capable**: a single ingest can create one primary page and update multiple related pages. Cross-graph link refinement is out of scope (handled by the scheduled `garden` skill).
- **Terminal**: after writing, the agent delivers a summary of changes and ends the turn. User drives any follow-up.

## Non-goals

- **Multiple URLs per invocation.** One source per call. User invokes again for a second source.
- **Pasted text as a source.** Tricky to distinguish from user instructions; skip for v1.
- **`log.md` / `index.md` updates.** Deferred to #288 and #289. This skill ships without them; if they land later, the ingest skill body grows to call them.
- **Cross-graph link backfill.** Identifying missing `[[wiki-links]]` across the whole vault after ingestion is the `garden` skill's job (and #287 expands it). This skill only adds links to pages it directly touches.
- **Scheduled operation.** This is interactive-only. Scheduled ingest is covered by linkding-ingest, mastodon-ingest, and future contrib skills.
- **Source-specific optimization.** Unlike linkding-ingest (which enforces `agent/pages/bookmarks/`) or mastodon-ingest (which enforces `agent/pages/mastodon/`), `/ingest` is generic. Agent chooses folder based on content.

## Design

### Invocation shapes

```
/ingest https://example.com/article
/ingest workspace/inbox/paper.md
/ingest @attachment:meeting-notes.pdf     # attachment form (exact syntax TBD, see Phase 1)
/ingest <source> [— optional free-text focus]
```

Examples of focus arg:

```
/ingest https://example.com/post — focus on the security implications
/ingest workspace/imports/rfc.md — compare against our current auth design
```

The agent parses `$ARGUMENTS` to determine the input type and focus. Heuristics (for the skill body):

- Starts with `http://` or `https://` → URL.
- Starts with `workspace/` or is an existing file path → workspace file.
- Bare filename with no slashes → try `list_attachments` to resolve.
- Anything after ` — ` or `; ` in `$ARGUMENTS` is free-text focus.

### Fetch mechanism

Agent chooses. In the skill body we list the options and guidance:

- URLs: try `tabstack_extract_markdown` first for article-style pages. Fall back to `web_fetch` if tabstack errors (rate-limited, unsupported site, credits exhausted).
- Workspace files: `workspace_read`.
- Attachments: `get_attachment(filename)`.

Agent decides; the body doesn't hardcode a first-try order in a way that blocks recovery.

### Synthesis workflow (skill body)

Structured as a short checklist the agent follows:

1. **Identify the source.** Parse `$ARGUMENTS`. If empty, call `list_attachments` and use the most recent attachment. Confirm what you're about to fetch (one short line for the user).
2. **Fetch.** Use the appropriate tool. If it fails, report the error and stop — do not fabricate content. For large sources (long articles, multi-page PDFs), extract the core claims and notable details; don't attempt to mirror the whole thing into the vault.
3. **Understand.** Read the fetched content. Note the main topic, key entities, and any specific claims worth preserving. Verify the fetched text looks like real content, not an error page or stub.
4. **Search the vault.** Use `vault_search` (and `vault_list` if needed) to find existing pages that relate to this source's topics. Typical hits: a page on the primary topic, pages on entities/concepts the source references. **If a page on the exact source or its primary topic already exists, treat it as the primary page and update in place** — do not create a duplicate with a different slug.
5. **Plan the updates.** Decide:
   - One primary page: update existing if match is strong, or create a new page with a topical folder chosen by the agent (e.g. `agent/pages/tools/ripgrep`, `agent/pages/papers/attention-is-all-you-need`, `agent/pages/people/karpathy`).
   - Zero or more secondary pages: related pages that should be updated with a sentence or cross-link, not rewritten wholesale.
   - **Cap at ~5 pages total (primary + secondary).** If more seem relevant, note them in the summary as candidates for the next garden run rather than touching them this turn.
6. **Write.** For each planned update, `vault_read` → revise → `vault_write`. For each new page, `vault_write` with:
   - YAML frontmatter (`tags` including at least `ingested` for the primary, `summary`, optionally `importance`).
   - Body with `[[wiki-links]]` to related pages touched in this ingest.
   - `## Sources` section with the URL, date (from `current_time`), and any relevant attribution.
   - **Prefer synthesis in your own words over direct quotation.** If you quote, attribute inline and keep quotes short.
7. **Summarize.** Deliver a change summary to the user:
   ```
   Ingested: <source>
   Primary page: [[Name]] (new | updated)
   Secondary updates: [[A]], [[B]]
   (Other pages that might benefit: [[C]], [[D]] — deferred to the next garden pass)
   ```
   Then stop. Do not re-engage unless asked.

### Focus arg semantics

When a focus is supplied (e.g. `— focus on the security implications`), it shapes the **content and emphasis** of the written pages — what gets pulled out of the source, which claims are foregrounded. It does **not** override folder choice: a paper on transformers stays under `agent/pages/papers/`, even with a focus on the history of AI.

### Frontmatter

- Primary page gets `tags: [ingested, ...topic-tags]` and a one-line `summary:` field. (`summary` aligns with frontmatter we already support — #288 may later use it for an index.)
- Secondary page updates do not add an `ingested` tag; they already have their own identity.

### Output folder policy

- Agent-chosen, based on content. No forced prefix.
- Prefer existing folders (`agent/pages/tools/`, `agent/pages/people/`, `agent/pages/papers/`) if they exist and fit.
- Create a new subfolder if the topic is distinct and likely to grow.
- Do NOT write outside `agent/pages/`. (The vault skill enforces agent-folder writes already; the ingest body should not override.)

### Pre-approved tools

```
tabstack_extract_markdown, web_fetch, workspace_read,
list_attachments, get_attachment,
vault_search, vault_read, vault_write, vault_list, vault_backlinks,
current_time
```

Rationale: the skill needs all three fetch paths, the full vault CRUD surface, and timestamping. Pre-approval avoids mid-ritual confirmation prompts.

### Required skills

- `tabstack` (for `tabstack_extract_markdown`) — auto-activated when the skill runs.
- `vault` is always-loaded, no declaration needed.

### Context mode

`inline`. The user may have conversational context that should shape the ingest ("focus on the security angle", "this is for the decafclaw project"). Fork mode would drop that.

### Turn boundary

End of turn after the summary. Matches postmortem. User follows up in the next turn if they want more work done (expand a page, add more sources, etc.).

## Open items for Phase 1 verification

- **Attachment reference syntax.** `/ingest @attachment:foo.pdf` is a guess. Need to confirm what the actual convention is, or whether the agent should just inspect `list_attachments` when `$ARGUMENTS` is empty or a bare filename. Worst case: drop the `@attachment:` syntax and let the agent figure it out via `list_attachments`.
- **Focus arg delimiter.** ` — ` is nicer but em-dash input is awkward. `; ` or `|` might be better. Cheap to adjust.
- **Tabstack availability detection.** We assume the agent will see a failure and fall back. If tabstack returns a soft error that the agent interprets as success, we get garbage. Skill body should include a "verify the content looks like a real article, not an error message" check.

## Acceptance

- `/ingest <url>` fetches the URL, writes a primary page under `agent/pages/`, optionally updates related pages, and ends the turn with a change summary.
- `/ingest <workspace/path>` reads a workspace file and produces the same output.
- `/ingest <attachment>` works for a file uploaded in the web UI.
- Focus arg, when provided, visibly shapes the output (e.g. a focus on "security implications" produces a security-angled summary).
- No writes outside `agent/pages/`.
- Eval case validates the end-to-end ingest-from-workspace-file path with a seeded fixture (URL fetching is flaky in evals).
- `docs/ingest-skill.md` documents the skill.
