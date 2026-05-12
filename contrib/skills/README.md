# Contrib Skills

Optional skills that require external services or API keys. Not enabled by default.

## Installation

Two installation styles, depending on whether you want updates to flow with `git pull`.

### Option 1 — Reference (recommended)

Add the skill's directory to your agent's `extra_skill_paths` so the loader picks it up in place. `git pull` then keeps `SKILL.md` up to date automatically; downloaded binaries persist (they're already gitignored).

In `data/{agent_id}/config.json`:

```json
{
  "extra_skill_paths": [
    "../../contrib/skills/linkding-ingest",
    "../../contrib/skills/mastodon-ingest"
  ]
}
```

Each entry points at a single skill directory (one with `SKILL.md` at its root). **Relative paths are anchored to `data/{agent_id}/`**, so `../../contrib/skills/<name>` reaches the repo's `contrib/skills/` directory when `data_home` is at its default `./data` location. Absolute paths and `~` / `$VAR` expansion also work — e.g. set `DECAFCLAW_REPO=/path/to/repo` in `.env` and use `$DECAFCLAW_REPO/contrib/skills/<name>` to decouple from the `data_home` layout.

Then download the required binaries (run from the repo root):

```bash
contrib/skills/linkding-ingest/download-binary.sh
```

And set the required environment variables (in `.env` or `config.json` `env` section).

### Option 2 — Copy (fork for customization)

If you want a fully detached copy you can edit per-deployment, copy the skill directory into your agent's admin-level skills folder:

```bash
cp -r contrib/skills/linkding-ingest data/{agent_id}/skills/
data/{agent_id}/skills/linkding-ingest/download-binary.sh
```

A skill at `data/{agent_id}/skills/<name>/` shadows any same-named entry in `extra_skill_paths`, so you can also start with Option 1 and switch to Option 2 later if you need to customize.

## Available Skills

### linkding-ingest

Fetches bookmarks from a [Linkding](https://github.com/sissbruecker/linkding) instance, reads the bookmarked content via Tabstack, and records insights to the wiki knowledge base. Delegates each bookmark to a child agent for parallel processing.

**Requires:** `LINKDING_URL`, `LINKDING_TOKEN` env vars, `linkding-to-markdown` binary

**Schedule:** Every 4 hours (`:45`)

### mastodon-ingest

Fetches recent posts from a Mastodon account and records interesting content to the wiki knowledge base.

**Requires:** `MASTODON_SERVER`, `MASTODON_ACCESS_TOKEN` env vars, `mastodon-to-markdown` binary

**Schedule:** Every 4 hours (`:30`)
