# Calendar Events

Calendar events are one-shot reminders with an absolute time. They are separate from [Scheduled Tasks](schedules.md), which run cron-style tasks in fresh conversations, and from [Background Job Agent Wake](background-wake.md), which covers process completion and file watches.

A calendar event stores a title, a reason, a fire time, and a target conversation. At the fire time, DecafClaw injects a message into the target conversation and starts a `TurnKind.WAKE` turn with that conversation's history.

## Storage

Events live in per-origin-conversation sidecars:

```
data/{agent_id}/calendar/{conv_id}.json
```

The directory is outside the agent-writable workspace, like `scheduled_wakes/` and `file_watches/`. The origin conversation is the conversation where the event was created. Deleting that conversation cancels its pending events and removes the sidecar.

An event stores:

| Field | Meaning |
|-------|---------|
| `event_id` | Short unique id, such as `cal_1a2b3c4d` |
| `conv_id` | Origin conversation that stores the event |
| `target_conv_id` | Conversation that receives the wake turn |
| `session_mode` | `current` or `dedicated` |
| `session_key` | Stable key for a dedicated session |
| `session_title` | Display title for a dedicated session |
| `title` | Short event title |
| `reason` | What the agent should do or say when the event fires |
| `fire_at` | Absolute ISO-8601 time with a timezone |
| `fire_at_ts` | Unix timestamp used by the timer |
| `status` | `pending`, `fired`, `cancelled`, or `orphaned` |
| `last_fired_at` | Time the event last fired |
| `snooze_until` | Time set by the last snooze |

## Agent tools

| Tool | What it does |
|------|--------------|
| `calendar_create` | Create a one-shot event. Target the current conversation or a dedicated session. |
| `calendar_list` | List events for the origin conversation. Filter by status and time range. |
| `calendar_update` | Change a pending event. Rescheduling is an update with a new `fire_at`. |
| `calendar_cancel` | Cancel a pending event. |
| `calendar_snooze` | Move a pending or fired event forward by a duration or to an absolute time. |

The tools are available only in interactive user conversations. Child agents, heartbeat runs, and scheduled tasks cannot create calendar events.

`fire_at` must be an ISO-8601 timestamp with a timezone. The agent converts natural language, such as "tomorrow at 08:00", into an absolute time. The server does not parse natural language.

## Target conversations

A calendar event targets its origin conversation by default. This keeps the reminder in the conversation where the user asked for it.

An event can instead target a dedicated session. The session key is a stable lowercase slug, such as `morning-briefing`. The session title is a display name, such as "Morning Briefing".

The dedicated session is an upsert:

- If the session exists, reuse it.
- If the session is archived, unarchive it.
- If the session is missing, create it.
- Keep an existing session title.
- If the session is deleted, the next fire creates it again.

The session id is deterministic: `web-{user_id}-cal{hash}`. The hash comes from the session key. Multiple events with the same user and session key share one session.

## Firing

The manager starts one asyncio timer per pending event. At the fire time it:

1. Re-reads the event from the sidecar.
2. Resolves the target conversation.
3. Enqueues a `TurnKind.WAKE` turn with the event title, reason, due time, and target session.
4. Marks the event `fired` after the manager accepts the turn.
5. Appends a `calendar` notification when no client is connected to the target conversation.

Calendar event turns bypass the wake rate limiter. They are not suppressible with `BACKGROUND_WAKE_OK`. They keep the existing Mattermost `wake_delivery` fallback and write an inbox-only notification for the Web UI.

A current-mode event whose origin conversation no longer exists is marked `orphaned` and writes a notification. If a dedicated session was deleted, the next fire upserts the session again.

## Restart recovery

The startup scan reads every `data/{agent_id}/calendar/*.json` file and resumes pending events. An event that became due while the server was offline fires immediately. The prompt names the original due time.

## Limits

This first version supports one-shot events only. Recurring events, the Calendar Web UI, and calendar views are separate issues. External calendar sync, such as CalDAV or Google Calendar, is out of scope.

The server does not store all-day events, attendees, attachments, or exception dates.
