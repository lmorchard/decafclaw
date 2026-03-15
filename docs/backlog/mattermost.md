# Mattermost Backlog

Mattermost-specific features, not portable to other channels.

## Bot/channel allowlists

Allow listening to specific bots in specific channels.

Config: `bot_username:channel_id` pairs or separate allowlists.

## Channel management tools

Agent tools to create channels, invite users, set headers, archive.

- `create_channel`, `invite_user`, `set_channel_header`, `archive_channel`
- Needs Mattermost bot permissions
- Gated by config/permissions

## Additional Tabstack tools

- Tabstack `automate` with `--guardrails` and `--data` support
- Tabstack geo-targeting (`--geo CC`) for region-specific content
