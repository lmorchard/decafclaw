# Session Notes

## 2026-04-21

- Session started for GitHub issue [#292](https://github.com/lmorchard/decafclaw/issues/292) — Notification infrastructure.
- Scope: Phase 1 only (inbox JSONL + `notify()` API + web UI bell + panel + wire a few existing event types as producers).
- Motivation: Les works primarily in the web UI now; Mattermost-based agent notifications aren't reaching him. A bell + inbox in the UI delivers notifications where he already is.
- Day-one producers identified: heartbeat completions, scheduled-task completions, background process exits, compaction events, agent reflection rejections.
- Deferred to Phase 2+: external channel adapters (Mattermost DM / channel, email via #231, vault page).

## 2026-04-22 — Execute

- Executed the 6-step plan autonomously per Les's directive.
- All 5 initial steps landed cleanly: core module + config, REST endpoints, web UI component, 5 producers wired, docs.
- Opened PR #293, requested Copilot review.
- Copilot flagged 6 real issues (all addressed):
  1. Click-outside listener leak when a row click closed the panel via `#navigate()` — centralized open/close helpers fixed it.
  2. `all: unset` on notification rows killed the `:focus-visible` outline — restored.
  3. Multi-user data leak in notification endpoints — documented Phase 1 single-user scope at the route block + in `docs/notifications.md`.
  4. Unused `field` import in `notifications.py`.
  5. Doc/code mismatch: docs said UI polls every `poll_interval_sec` seconds, actual is hardcoded 30s — clarified both `docs/notifications.md` and `docs/config.md`.

## 2026-04-23 — Iterate on feedback

Les browser-tested the UI and called out a batch of issues / opinions:

- **Panel clipped by sidebar `overflow: hidden`** — switched from `position: absolute` to `position: fixed` with `getBoundingClientRect`-computed coords; panel now opens to the right of the bell and floats above the whole page. Repositions on resize.
- **Compaction producer dropped** — mid-turn, conversation-local event; already visible in the UI, so async notification is noise. Removed cleanly.
- **Reflection producer dropped** — same reasoning as compaction. Removed.
- **Click-to-navigate didn't work for most producers** — only the reflection producer set `link`; heartbeat/schedule/background set only `conv_id`. Added JS fallback: if `link` is absent and `conv_id` is set, treat as `conv://<conv_id>`. Also updated the scheduled-task producer to pass `ctx.conv_id` (the synthetic `schedule-<name>-<ts>` id) so clicking the notification jumps to the System > Schedule conversation for that run.

Self-review before final squash found two more issues worth fixing:

- **Stale `requestAnimationFrame` race**: rapid bell toggle could leave the rAF scheduled after `_open` flipped back to false, registering an orphan doc-click listener. Fixed by re-checking `this._open` inside the rAF body.
- **Direct mutation of `rec.read`** in `#onRowClick` — swapped for immutable array replacement (spread + map), matching `#markAllRead`.

Also filed three follow-up issues during the session:

- **#294** — priority-aware dropdown grouping + configurable `badge_min_priority` so routine low-priority events can notify quietly without training users to ignore the badge.
- **#295** — full-page event viewer (history, search, filters, bulk actions) — bell stays for high-priority, viewer owns the complete audit trail.
- **#296** — vault skill is missing `vault_delete` (and arguably `vault_rename`); noticed while looking at a scheduled task. REST endpoint exists; the agent tool doesn't.

Commented on **#241** (background-process agent notification) to frame Phase 1 of #292 as the **user-facing** side of background exits — the agent-facing delivery path (synthetic turn injection, system message on the originating conversation) is still the work that issue tracks.

## Final state

- PR #293 merged to main 2026-04-23.
- Final producer set: heartbeat, schedule, background (3 producers, all fail-open).
- 46 new tests; full suite at 1592 passing.
- Issue #292 kept open for Phase 2+ work (adapters, multi-user, WebSocket push, periodic reports).

## Retro — what worked, what to remember

- **Self-review + Copilot review found real bugs.** The listener leak, the doc/code mismatch on polling interval, the unused import, the multi-user concern — all legitimate catches. Keep running Copilot review after self-review, not in place of it.
- **The "fixup" commits per round before re-squashing kept the review diff legible.** When Les iterated in the browser, pushing incremental fixes meant each feedback cycle was a focused diff instead of re-reading the whole squash. Worth doing this intentionally in future sessions with lots of browser-driven iteration.
- **Rip producers out when they don't fit.** Both compaction and reflection were on the spec's day-one list, but once the UI was working Les could see that mid-turn events produce noise, not signal. Easy to overthink producer lists when designing on paper; the UI tells the truth.
- **Every feedback pass produced ≥1 follow-up issue.** Don't try to absorb future ideas into the current PR; file and move on. #294 / #295 / #296 all surfaced this way and would've scope-crept Phase 1 if merged in.
- **The pattern of "user alert via inbox" vs "agent alert via synthetic turn" is worth naming.** Comment on #241 makes this explicit. Likely applies to more than just background jobs.

## Follow-ups

- #294 — priority badge filter + grouping
- #295 — full-page event viewer
- #296 — vault_delete tool
- #241 — agent-facing background-exit delivery (now has #293 as prior art for the exit hook)
- Session docs under `docs/dev-sessions/2026-04-21-1418-notification-inbox/` can be archived; nothing more to add here.
