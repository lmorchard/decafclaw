# Calendar Events

Calendar events are reminders with an absolute time. An event can be one-shot or repeat on an RFC 5545 RRULE. They are separate from [Scheduled Tasks](schedules.md), which run cron-style tasks in fresh conversations, and from [Background Job Agent Wake](background-wake.md), which covers process completion and file watches.

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
| `status` | `pending`, `fired`, `cancelled`, `orphaned`, or `completed` |
| `last_fired_at` | Time the event last fired |
| `snooze_until` | Time set by the last snooze |
| `recurrence` | RFC 5545 RRULE, or empty for a one-shot event |
| `timezone` | IANA timezone for a recurring event |
| `recurrence_start` | Original first occurrence; the RRULE anchor |
| `occurrence_at` | Original time of the current occurrence |
| `recurrence_until` | Optional end of the series |

## Agent tools

| Tool | What it does |
|------|--------------|
| `calendar_create` | Create a one-shot or recurring event. Target the current conversation or a dedicated session. |
| `calendar_list` | List events for the origin conversation. Each row shows the next occurrence and a short recurrence description. |
| `calendar_update` | Change a pending event. Rescheduling is an update with a new `fire_at`; `recurrence`, `timezone`, and `recurrence_until` change the series. |
| `calendar_cancel` | Cancel a pending event or the whole recurring series. |
| `calendar_snooze` | Move the next occurrence forward by a duration or to an absolute time. The series anchor does not move. |

The tools are available only in interactive user conversations. Child agents, heartbeat runs, and scheduled tasks cannot create calendar events.

`fire_at` must be an ISO-8601 timestamp with a timezone. The agent converts natural language, such as "tomorrow at 08:00", into an absolute time. The server does not parse natural language.

## Recurrence

A recurring event stores an RRULE string, an IANA timezone, and an optional end time. The agent converts natural language into the RRULE; the server does not parse recurrence text.

```text
FREQ=DAILY                      every day
FREQ=WEEKLY;BYDAY=MO,WE,FR      weekdays
FREQ=WEEKLY;INTERVAL=2          every other week
FREQ=MONTHLY;BYMONTHDAY=1       the first of each month
```

`timezone` is required for a recurring event. Occurrences are computed in the event's local wall-clock time and then localized with `zoneinfo`, so `08:00` stays at `08:00` across daylight-saving transitions. `recurrence_until` is an ISO-8601 timestamp with a timezone; the series becomes `completed` after the last occurrence at or before it.

After each fire, the manager computes the next occurrence after `occurrence_at` and keeps the same `event_id`, `target_conv_id`, and dedicated session. A recurring event that became due while the server was offline fires **once** on startup, then schedules the next future occurrence instead of replaying every missed one.

`calendar_snooze` moves only the current occurrence. `occurrence_at` and `recurrence_start` stay at the original anchor, so the following occurrence is computed from the series, not from the snoozed time. `calendar_cancel` cancels the whole series.

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
4. For a one-shot event, marks it `fired`. For a recurring event, computes the next occurrence and keeps it `pending` (or marks it `completed` after `recurrence_until`).
5. Appends a `calendar` notification when no client is connected to the target conversation.

Calendar event turns bypass the wake rate limiter. They are not suppressible with `BACKGROUND_WAKE_OK`. They keep the existing Mattermost `wake_delivery` fallback and write an inbox-only notification for the Web UI.

A current-mode event whose origin conversation no longer exists is marked `orphaned` and writes a notification. If a dedicated session was deleted, the next fire upserts the session again.

## Restart recovery

The startup scan reads every `data/{agent_id}/calendar/*.json` file and resumes pending events. An event that became due while the server was offline fires immediately. The prompt names the original due time.

## Web UI

The **Calendar** tab in the Web UI lists the user's pending events across conversations, grouped by date. It can create, edit, snooze, and cancel events, and target the current conversation or a dedicated session. Agent tool changes publish `calendar_changed`, so an open tab refreshes without a page reload.

The REST API lives under `/api/calendar`:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/calendar/events` | List events; query `from`, `to`, `status`, `conv_id` |
| `POST` | `/api/calendar/events` | Create an event |
| `PUT` | `/api/calendar/events/{event_id}` | Update a pending event |
| `DELETE` | `/api/calendar/events/{event_id}` | Cancel an event or series |
| `POST` | `/api/calendar/events/{event_id}/snooze` | Move the next occurrence |

The route module is `src/decafclaw/web/calendar_routes.py`. It uses the same session-cookie auth as the rest of the Web UI; unauthenticated requests return 401 and unknown event ids return 404.

## Limits

Recurring events use RFC 5545 RRULEs; exception dates, attendees, and attachments are not stored. The Calendar Web UI and calendar views are separate issues. External calendar sync, such as CalDAV or Google Calendar, is out of scope.

The server does not store all-day events, attendees, attachments, or exception dates.
