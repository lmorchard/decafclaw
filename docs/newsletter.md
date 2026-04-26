# Newsletter

The newsletter is a periodic narrative recap of autonomous agent activity — work the agent did on its own, via scheduled skills, without direct user involvement in the conversation.

## How it works

A bundled scheduled skill (`newsletter`) runs daily at 7am (cron `0 7 * * *`). It:

1. Lists scheduled-task conversations from the last 24 hours by globbing `workspace/conversations/schedule-*.jsonl` — this naturally excludes interactive (`web-*`), heartbeat, and child-agent conversations, which use different filename prefixes. Newsletter's own runs (`schedule-newsletter-*`) are explicitly excluded to avoid self-reference.
2. Lists vault pages added or modified in the same window.
3. Composes a conversational narrative in SOUL voice using those two inputs.
4. Writes a local archive to `workspace/newsletter/archive/YYYY-MM-DD.md`.
5. Delivers to each enabled channel: email and/or a dated vault page at `{vault_root}/agent/journal/newsletters/YYYY-MM-DD.md`.
6. Advances `workspace/newsletter/last_run.json`.

## Configuration

Under `config.skills.newsletter` (or via `NEWSLETTER_*` env vars):

| Field | Default | Description |
| --- | --- | --- |
| `window_hours` | `24` | How far back to look |
| `email_enabled` | `false` | Dispatch by email |
| `email_recipients` | `[]` | Destination addresses |
| `email_subject_prefix` | `"[decafclaw newsletter]"` | Prepended to the subject line |
| `vault_page_enabled` | `true` | Write a dated page under the vault |
| `vault_folder` | `"agent/journal/newsletters"` | Relative to vault root |

Email uses `mail.py` directly (bypasses the `send_email` tool's confirmation gate) — the `email_recipients` list is the trust boundary.

## `!newsletter` / `/newsletter`

Invoke interactively in any chat to peek at what a newsletter *would* look like right now, without disturbing the scheduled cadence. The same composition path runs, but the `newsletter_publish` tool short-circuits: no archive, no email, no vault page, no state advance. The markdown is returned as the tool result and shown in the conversation.

**Optional window argument:** pass a compact time-range spec to look back further than the default 24 hours. Accepted forms: `Nh` (hours), `Nd` (days), `Nw` (weeks). Examples:

- `!newsletter` — last 24 hours
- `!newsletter 48h` — last 48 hours
- `!newsletter 7d` — last week
- `!newsletter 2w` — last fortnight

Malformed arguments (e.g., `!newsletter 7days` or `!newsletter yesterday`) return a tool error so the composer can tell the user to retry.

**Force delivery — `!newsletter send`:** include the literal `send` token to smoke-test the full archive + email + vault-page delivery path on demand, instead of the short-circuit. Useful when verifying that scheduled delivery is wired correctly without waiting for the next cron tick. Combine with the window if needed:

- `!newsletter send` — force-deliver, last 24 hours
- `!newsletter send 7d` — force-deliver, last week (token order doesn't matter; `7d send` works too)

Behind the scenes this passes `force_delivery=True` to `newsletter_publish`, which bypasses the interactive short-circuit so archive + delivery run identically to a scheduled invocation. The newsletter's own `last_run.json` advances; the schedule timer's per-task tracking under `workspace/.schedule_last_run/` is independent and unaffected.

## Relationship to other subsystems

- **Notifications** are small, typed, per-event records. Newsletters are narrative multi-paragraph recaps. They share nothing in code — different subsystems, different semantics.
- **Heartbeat** reports operational status ("is everything OK?"). Newsletters report on what the agent *did* on its own. Complementary.
- **Dream consolidation** is itself an input to the newsletter — dream's own scheduled runs get summarized alongside other scheduled activity.

## Phase 2+ (future)

- Mattermost channel delivery (lands with a reusable `mattermost_channel` notification adapter).
- Hourly / weekly cadences.
- Natural-language time-range parsing on `!newsletter` (e.g., `since yesterday`).
