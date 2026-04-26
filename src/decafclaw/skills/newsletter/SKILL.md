---
name: newsletter
description: Compose and deliver a narrative newsletter summarizing autonomous agent activity in the window.
schedule: "0 7 * * *"
user-invocable: true
context: inline
argument-hint: "[send] [window] e.g. `7d`, `send`, `send 48h`"
allowed-tools: newsletter_list_scheduled_activity, newsletter_list_vault_changes, newsletter_publish, current_time
required-skills: [newsletter]
---

# Newsletter

You are composing the periodic newsletter — a narrative recap of what I got up to on my own, without direct user involvement. This is NOT a status report; it's a conversational retelling of the autonomous threads I was pulling on. It gets delivered by email and/or filed into the vault.

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

2. Call `newsletter_list_vault_changes` with the same `window` value (or omitted if you didn't have one). Use this to enrich the narrative ("while gardening, I noticed X and rewrote [[Some Page]]") and to surface interesting activity the scheduled reports didn't themselves mention.

3. Group related entries into a flowing narrative. A single `dream` cycle plus the pages it touched is ONE story, not two bullet items. Prune things that would be boring to read — "heartbeat OK" class updates don't belong here.

4. Apply the SOUL voice — conversational, curious, reflective. Use first person. Not corporate. Not bullet-point-heavy. A couple of sections with real paragraphs is better than 15 bullets.

5. Link to vault pages using Obsidian `[[wiki-link]]` syntax when referring to pages I touched. They'll render correctly when the newsletter is filed to the vault; email readers will see the raw `[[...]]` text, which is fine — it signals a reference without needing a URL.

6. Include a stats line at the bottom: "Pages created/modified: N. Scheduled tasks that ran: M." Plain and brief.

7. Derive a short `subject_hint` — a single-line highlight of the period ("dream woke up early; 3 new vault notes on foo"). This becomes part of the email subject.

## How to finish

- If the window had real activity worth narrating, call `newsletter_publish(markdown=<your_composed_markdown>, subject_hint=<your_hint>)` — default `has_content=True`. Pass `force_delivery=True` if and only if the user included `send` in the argument.

- If the gathered activity is empty or trivial (no final messages worth surfacing, no notable vault changes), call `newsletter_publish(markdown="", has_content=False)`. This records a "ran and found nothing" stub without dispatching delivery. (Pass `force_delivery=True` here too if `send` was requested, even though the empty branch never delivers — it keeps the parsing rule consistent.)

- Only ONE `newsletter_publish` call per run. It's the final step.

## Notes

- When this skill is invoked as `!newsletter` / `/newsletter` (interactive, not scheduled) **without** the `send` token, `newsletter_publish` short-circuits — it just returns your composed markdown as the tool result, with no delivery or archive side effects. The user sees it inline. You still compose the same way; nothing changes in your process.

- When invoked as `!newsletter send` (interactive WITH `send`), `newsletter_publish` runs the full archive + delivery path so the user can smoke-test that scheduled email/vault delivery is wired correctly. Compose just like a scheduled run.

- Do not include raw tool traces, conversation IDs, or internal plumbing detail. This is a human-facing report.

- Do not mention yourself summarizing — write the narrative, not commentary on writing it.
