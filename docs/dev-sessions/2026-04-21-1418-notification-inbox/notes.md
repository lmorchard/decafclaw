# Session Notes

## 2026-04-21

- Session started for GitHub issue [#292](https://github.com/lmorchard/decafclaw/issues/292) — Notification infrastructure.
- Scope: Phase 1 only (inbox JSONL + `notify()` API + web UI bell + panel + wire a few existing event types as producers).
- Motivation: Les works primarily in the web UI now; Mattermost-based agent notifications aren't reaching him. A bell + inbox in the UI delivers notifications where he already is.
- Day-one producers identified: heartbeat completions, scheduled-task completions, background process exits, compaction events, agent reflection rejections.
- Deferred to Phase 2+: external channel adapters (Mattermost DM / channel, email via #231, vault page).
