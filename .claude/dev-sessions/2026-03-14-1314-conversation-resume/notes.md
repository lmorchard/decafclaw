# Session Notes — Conversation Resume and Graceful Shutdown

## Session Info

- **Date:** 2026-03-14, started ~13:14
- **Branch:** `conversation-resume`

## What we built

1. **Conversation resume** — on first access to a conv_id, check for
   existing JSONL archive and replay into history. Works for both
   Mattermost channels/threads and interactive mode.
2. **Graceful shutdown** — SIGTERM/SIGINT set a shutdown event, websocket
   listener stops accepting new events, in-flight tasks complete,
   connection closes cleanly.

## Notes

- Deferred compaction snapshot (store last summary alongside archive to
  avoid re-summarizing on every restart). For now, very long archives
  will just trigger compaction on first message after replay.
- The replay is straightforward since we archive raw message dicts — just
  read the JSONL lines and use them as the history list.
