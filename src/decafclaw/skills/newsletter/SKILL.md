---
name: newsletter
description: Compose and deliver a detailed narrative newsletter summarizing autonomous agent activity in the window.
user-invocable: true
context: inline
argument-hint: "[send] [window] e.g. `7d`, `send`, `send 48h`"
allowed-tools: newsletter_list_scheduled_activity, newsletter_list_vault_changes, newsletter_publish, current_time
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

1. Call `newsletter_list_scheduled_activity` to see what my scheduled tasks did. If you parsed a window spec out of the argument (i.e. anything other than the `send` token), pass it as `window`. Otherwise omit `window` for the 24-hour default. Each entry gives you the skill name, when it ran, what it reported at the end, and which vault pages it wrote. Skip entries with empty final messages — they didn't have anything coherent to say.

2. Call `newsletter_list_vault_changes` with the same `window` value (or omitted if you didn't have one). Use this to enrich the narrative ("while gardening, I noticed X and rewrote [[Some Page]]") and to surface interesting activity the scheduled reports didn't themselves mention. **If needed, use `vault_read` on these changed pages to pull out specific technical details, quotes, and facts.**

3. Group related entries into a flowing narrative, but **dive deep into the actual content**. Don't just say "I researched X" — include specific technical details, facts, or interesting quotes you found during the ingestion or run.

4. Apply the SOUL voice — conversational, curious, reflective. Use first person. **Use bullet points where they help surface rich details** (e.g., listing specific findings, decisions, or summaries of articles). Do not smooth over the details for a quick read; unpack them so the user can genuinely learn what you learned.

5. Link to vault pages using Obsidian `[[wiki-link]]` syntax when referring to pages I touched. They'll render correctly when the newsletter is filed to the vault. When sent via email, links will automatically resolve to external URLs or web UI deep links, with fallback to clean tags.

6. At the very end of the newsletter (just before the stats line), include an explicit **"Vault Changes" section** that lists every vault page created or modified during the window. Use `[[wiki-link]]` syntax for each page so they are easy to navigate, and group them logically if there are many.

7. Include a stats line at the bottom: "Pages created/modified: N. Scheduled tasks that ran: M." Plain and brief.

8. Derive a short `subject_hint` — a single-line highlight of the period ("dream woke up early; 3 new vault notes on foo"). This becomes part of the email subject.

## How to finish

- If the window had real activity worth narrating, call `newsletter_publish(markdown=<your_composed_markdown>, subject_hint=<your_hint>)` — default `has_content=True`. Pass `force_delivery=True` if and only if the user included `send` in the argument.

- If the gathered activity is empty or trivial (no final messages worth surfacing, no notable vault changes), call `newsletter_publish(markdown="", has_content=False)`. This records a "ran and found nothing" stub without dispatching delivery. (Pass `force_delivery=True` here too if `send` was requested.)

- Only ONE `newsletter_publish` call per run. It's the final step.

## Notes

- When this skill is invoked as `!newsletter` / `/newsletter` (interactive, not scheduled) **without** the `send` token, `newsletter_publish` short-circuits — it just returns your composed markdown as the tool result, with no delivery or archive side effects. The user sees it inline.
- When invoked as `!newsletter send` (interactive WITH `send`), `newsletter_publish` runs the full archive + delivery path so the user can smoke-test that scheduled email/vault delivery is wired correctly.
- Do not include raw tool traces, conversation IDs, or internal plumbing detail. This is a human-facing report.
- Do not mention yourself summarizing — write the narrative, not commentary on writing it.
