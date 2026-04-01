# Conversation Resume and Graceful Shutdown — Plan

## Steps

1. Archive replay in mattermost.py — when history is first created for a conv_id, check for archive
2. Archive replay in agent.py — same for interactive mode
3. Compaction on replay if history is large (character estimate)
4. Graceful shutdown signal handling
5. Lint, test, smoke test
