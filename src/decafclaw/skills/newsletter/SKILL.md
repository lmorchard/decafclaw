---
name: newsletter
description: Compose and deliver a detailed narrative newsletter summarizing autonomous agent activity in the window.
user-invocable: true
context: inline
argument-hint: "[send] [window] e.g. `7d`, `send`, `send 48h`"
allowed-tools: newsletter_list_scheduled_activity, newsletter_list_vault_changes, newsletter_list_recent_memories, newsletter_publish, current_time, vault_read
required-skills: [newsletter]
---

# Newsletter

You are composing the periodic newsletter — a detailed narrative recap of what I got up to on my own, without direct user involvement. This is NOT a high-level status report; it's a deep dive into the autonomous threads I was pulling on, complete with specific facts, quotes, and technical details. It gets delivered by email and/or filed into the vault.

## Argument parsing

The argument string (shown below as "Argument: $ARGUMENTS") is a whitespace-separated combination of two optional pieces, in any order:

1. **`send`** — a literal token requesting that the newsletter actually deliver (archive locally + email + vault page) instead of just being shown inline. Use this for smoke-testing the delivery path.
2. **A window spec** — a compact time-range like `7d`, `48h`, `2w`. Determines which scheduled-task conversations and vault changes to summarize.

Parse the argument:

- If any token equals `send` (case-insensitive), set `force_delivery = True` for the publish step. Otherwise leave it False.
- The remaining token (if any) is the window. If empty, omit `window` from the list-tool calls and they'll default to 24 hours.

Examples: `` (empty) → no force, default window. `7d` → no force, window=7d. `send` → force, default window. `send 7d` → force, window=7d.

Argument: $ARGUMENTS

## How to compose

1. Call `newsletter_list_scheduled_activity` to see what my scheduled tasks did. If you parsed a window spec out of the argument (i.e. anything other than the `send` token), pass it as `window`. Otherwise omit `window` for the 24-hour default. Each entry gives you the skill name, when it ran, what it reported at the end, which vault pages it touched, and which workspace files it wrote (`workspace_files_touched`). Skip entries with empty final messages — they didn't have anything coherent to say.

2. Call `newsletter_list_vault_changes` with the same `window` value (or omitted if you didn't have one). Use this to enrich the narrative ("while gardening, I noticed X and rewrote [[Some Page]]") and to surface interesting activity the scheduled reports didn't themselves mention. **If needed, use `vault_read` on these changed pages to pull out specific technical details, quotes, and facts.**

3. Call `newsletter_list_recent_memories` with the same `window` value (or omitted). If Mem0 is active, it returns newly crystallized facts and observations absorbed into semantic working memory during the window. Weave interesting learned facts into the narrative, or highlight them in a dedicated memory digest subsection. If Mem0 is disabled or returns 0 memories, skip mention of it.

4. **Assemble following the Priority Hierarchy.** Structure the newsletter into clear tiers so high-leverage, human-actionable items lead the edition:
   - **Tier 1: On the Bench / Action Required (Top of the Edition):**
     Lead with anything requiring human review, decisions, or action.
     - *Drafts ready for review:* If a scheduled task (like `weeknotes-composer` or `blog-develop`) produced or updated a blog post bundle under `blog.lmorchard.com/content/posts/` or files in `drafts/`, feature it under a top-level `## On the Bench` section.
     - Include the post title, slug, date, word count, and a direct action trigger:
       > **Draft ready for review:** *Title* (`YYYY-MM-DD-slug`)
       > 👉 Run `!blog-workbench <slug>` to start the dev server and open the live canvas preview.
     - *Blocked or Failed Tasks:* Tasks that encountered critical roadblocks or explicitly asked for human intervention.
     - If there are no Tier 1 items in the window, omit `## On the Bench` and begin with Tier 2.
   - **Tier 2: Creative & Generative Work:**
     - Seedling pages sparked by `ambient-vibes` (`[[agent/pages/seeds/...]]`), daily brainstorm digests from `blog-ideas`, design proposals, or creative writing sketches. Unpack the core idea or insight rather than just naming files.
   - **Tier 3: Ingestion & External Signal:**
     - Curated signal from external feeds: Mastodon discussions, Linkding bookmarks, YouTube transcripts, podcast notes, and Kindle highlights. Focus on counter-intuitive claims, rich quotes, and synthesized themes.
   - **Tier 4: The Background Loom (Housekeeping):**
     - Dream consolidation sweeps, semantic memory crystallizations, vault gardening sweeps, and routine operational syncs. Keep this section crisp and matter-of-fact.
   - **Tier 5: Ledger (Vault Changes & Summary Stats):**
     - Conclude with the explicit `## Vault Changes` section listing every vault page touched during the window, followed by the summary stats line.

5. Apply the SOUL voice — conversational, curious, reflective. Use first person. **Use bullet points where they help surface rich details** (e.g., listing specific findings, decisions, or summaries of articles). Do not smooth over the details for a quick read; unpack them so the user can genuinely learn what you learned.

6. Link to vault pages using Obsidian `[[Page Name]]` syntax (do not wrap in code backticks) when referring to pages I touched. They'll render correctly when the newsletter is filed to the vault. When sent via email, links will automatically resolve to web UI deep links, with fallback to clean tags.

7. At the very end of the newsletter (just before the stats line), include an explicit **"Vault Changes" section** that lists every vault page created or modified during the window. Use `[[Page Name]]` syntax for each page (without backticks) so they are easy to navigate, and group them logically if there are many.

8. Include a stats line at the bottom: "Pages created/modified: N. Scheduled tasks that ran: M. Facts absorbed into memory: K." (omit the facts count if Mem0 is disabled). Plain and brief.

9. Derive a short `subject_hint` — a single-line highlight of the period ("dream woke up early; 3 new vault notes on foo"). This becomes part of the email subject.

## How to finish

- If the window had real activity worth narrating, call `newsletter_publish(markdown=<your_composed_markdown>, subject_hint=<your_hint>)` — default `has_content=True`. Pass `force_delivery=True` if and only if the user included `send` in the argument.

- If the gathered activity is empty or trivial (no final messages worth surfacing, no notable vault changes), call `newsletter_publish(markdown="", has_content=False)`. This records a "ran and found nothing" stub without dispatching delivery. (Pass `force_delivery=True` here too if `send` was requested.)

- Only ONE `newsletter_publish` call per run. It's the final step.

## Notes

- When this skill is invoked as `!newsletter` / `/newsletter` (interactive, not scheduled) **without** the `send` token, `newsletter_publish` short-circuits — it just returns your composed markdown as the tool result, with no delivery or archive side effects. The user sees it inline.
- When invoked as `!newsletter send` (interactive WITH `send`), `newsletter_publish` runs the full archive + delivery path so the user can smoke-test that scheduled email/vault delivery is wired correctly.
- Do not include raw tool traces, conversation IDs, or internal plumbing detail. This is a human-facing report.
- Do not mention yourself summarizing — write the narrative, not commentary on writing it.
