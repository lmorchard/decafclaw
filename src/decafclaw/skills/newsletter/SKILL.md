---
name: newsletter
description: Compose and deliver a narrative newsletter summarizing autonomous agent activity in the window.
schedule: "0 7 * * *"
user-invocable: true
context: inline
argument-hint: "[window] e.g. 7d, 48h, 2w (default 24h)"
allowed-tools: newsletter_list_scheduled_activity, newsletter_list_vault_changes, newsletter_publish, current_time
required-skills: [newsletter]
---

# Newsletter

You are composing the periodic newsletter — a narrative recap of what I got up to on my own, without direct user involvement. This is NOT a status report; it's a conversational retelling of the autonomous threads I was pulling on. It gets delivered by email and/or filed into the vault.

## Window

If the user passed an argument (shown below as "Argument: $ARGUMENTS"), it's a compact time-range spec like `7d`, `48h`, or `2w`. Pass it as the `window` parameter to both list tools.

If no argument was provided, omit `window` and the tools will default to 24 hours.

Argument: $ARGUMENTS

## How to compose

1. Call `newsletter_list_scheduled_activity` to see what my scheduled tasks did. If the Argument above is non-empty, pass `window="$ARGUMENTS"`; otherwise call with no arguments. Each entry gives you the skill name, when it ran, what it reported at the end, and which vault pages it wrote. Skip entries with empty final messages — they didn't have anything coherent to say.

2. Call `newsletter_list_vault_changes` (pass the same `window` if Argument was provided) to see which vault pages moved (new or modified). Use this to enrich the narrative ("while gardening, I noticed X and rewrote [[Some Page]]") and to surface interesting activity the scheduled reports didn't themselves mention.

3. Group related entries into a flowing narrative. A single `dream` cycle plus the pages it touched is ONE story, not two bullet items. Prune things that would be boring to read — "heartbeat OK" class updates don't belong here.

4. Apply the SOUL voice — conversational, curious, reflective. Use first person. Not corporate. Not bullet-point-heavy. A couple of sections with real paragraphs is better than 15 bullets.

5. Link to vault pages using Obsidian `[[wiki-link]]` syntax when referring to pages I touched. They'll render correctly when the newsletter is filed to the vault; email readers will see the raw `[[...]]` text, which is fine — it signals a reference without needing a URL.

6. Include a stats line at the bottom: "Pages created/modified: N. Scheduled tasks that ran: M." Plain and brief.

7. Derive a short `subject_hint` — a single-line highlight of the period ("dream woke up early; 3 new vault notes on foo"). This becomes part of the email subject.

## How to finish

- If the window had real activity worth narrating, call `newsletter_publish(markdown=<your_composed_markdown>, subject_hint=<your_hint>)` — default `has_content=True`.

- If the gathered activity is empty or trivial (no final messages worth surfacing, no notable vault changes), call `newsletter_publish(markdown="", has_content=False)`. This records a "ran and found nothing" stub without dispatching delivery.

- Only ONE `newsletter_publish` call per run. It's the final step.

## Notes

- When this skill is invoked as `!newsletter` / `/newsletter` (interactive, not scheduled), `newsletter_publish` automatically short-circuits — it just returns your composed markdown as the tool result, with no delivery or archive side effects. The user sees it inline. You still compose the same way; nothing changes in your process.

- Do not include raw tool traces, conversation IDs, or internal plumbing detail. This is a human-facing report.

- Do not mention yourself summarizing — write the narrative, not commentary on writing it.
