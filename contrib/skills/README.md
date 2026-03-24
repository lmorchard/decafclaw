# Contrib Skills

Optional skills that require external services or API keys. Not enabled by default.

## Installation

Copy a skill directory to your agent's skills directory:

```bash
cp -r contrib/skills/linkding-ingest data/{agent_id}/skills/
```

Then download the required binaries:

```bash
data/{agent_id}/skills/linkding-ingest/download-binary.sh
```

And set the required environment variables (in `.env` or `config.json` `env` section).

## Available Skills

### linkding-ingest

Fetches bookmarks from a [Linkding](https://github.com/sissbruecker/linkding) instance, reads the bookmarked content via Tabstack, and records insights to the wiki knowledge base. Delegates each bookmark to a child agent for parallel processing.

**Requires:** `LINKDING_URL`, `LINKDING_TOKEN` env vars, `linkding-to-markdown` binary

**Schedule:** Every 4 hours (`:45`)

### mastodon-ingest

Fetches recent posts from a Mastodon account and records interesting content to the wiki knowledge base.

**Requires:** `MASTODON_SERVER`, `MASTODON_ACCESS_TOKEN` env vars, `mastodon-to-markdown` binary

**Schedule:** Every 4 hours (`:30`)
