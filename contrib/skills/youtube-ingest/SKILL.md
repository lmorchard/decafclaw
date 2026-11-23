---
name: youtube-ingest
description: Ingest watched YouTube video transcripts into the vault from your watch history (via agent browser) or on-demand URL.
user-invocable: true
context: inline
argument-hint: "[url-or-video-id]"
allowed-tools: youtube_sync_history, youtube_ingest_video, vault_read, vault_list, vault_write, vault_journal_append, current_time
---

# YouTube Ingest

You ingest YouTube video transcripts into per-video vault pages under `agent/pages/youtube/`.

Argument: `$ARGUMENTS`

## How to run

**Empty argument** — sync recent watch history:
Call `youtube_sync_history()` to inspect your YouTube watch history from the persistent agent browser on `myriad-docker`, filter for videos watched > 50%, fetch transcripts, and save new videos to the vault.

**Non-empty argument** — single video ingest:
If `<arg>` is provided (a YouTube URL or video ID), call `youtube_ingest_video(url_or_id=<arg>)` to ingest that specific video transcript.

Summarize the outcome concisely.
