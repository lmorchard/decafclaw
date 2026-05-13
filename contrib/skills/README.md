# Contrib Skills

Optional skills that require external services or API keys. Not enabled by default.

## Installation

Two installation styles, depending on whether you want updates to flow with `git pull`.

### Option 1 — Reference (recommended)

Add the skill's directory to your agent's `extra_skill_paths` so the loader picks it up in place. `git pull` then keeps `SKILL.md` up to date automatically; downloaded binaries persist (they're already gitignored).

**Why `$VAR` over relative paths.** Relative entries in `extra_skill_paths` are anchored to `data/{agent_id}/`, not to the repo or the CWD. That only reaches the repo's `contrib/skills/` when `data_home` happens to live inside the repo (the default `./data` dev layout). Production deployments usually keep `data_home` somewhere stable like `~/.decafclaw/`, which is nowhere near the repo — relative paths will silently miss. A `$VAR` decouples the two.

Add to `.env`:

```bash
DECAFCLAW_REPO=/absolute/path/to/decafclaw-repo
```

Then in `data/{agent_id}/config.json`:

```json
{
  "extra_skill_paths": [
    "$DECAFCLAW_REPO/contrib/skills/linkding-ingest",
    "$DECAFCLAW_REPO/contrib/skills/mastodon-ingest"
  ]
}
```

Each entry points at a single skill directory (one with `SKILL.md` at its root). The loader runs `os.path.expandvars` + `~` expansion on every entry before scanning, so plain absolute paths and `~/...` also work. Use whichever fits your deployment.

(If `data_home` is the default `./data` location inside the repo, a relative `../../contrib/skills/<name>` will also work — but the `$VAR` form is portable across deployment layouts.)

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

### kindle

Syncs highlights and notes from your Kindle library (`read.amazon.com/notebook`) into per-book vault pages under `agent/pages/kindle/`. Each book gets one page with frontmatter tracking the ASIN, title, author, and highlight count. Deleted highlights are moved to an `## Archived` section with a date stamp.

**Requires:** A Netscape-format `cookies.txt` file exported from a logged-in Amazon session. Place at `data/{agent_id}/secrets/kindle.cookies.txt` (or configure `skills.kindle.cookies_path`). No binary downloads needed.

**Smoke test:** `uv run python contrib/skills/kindle/smoke.py`

**Tests:** `make test-contrib` (or `uv run pytest contrib/skills/kindle/ -v`)

**Schedule:** Daily at 5am UTC (`0 5 * * *`). Gated by `skills.kindle.enabled` (default `false`); on-demand via `/kindle-sync` always works.

See [docs/kindle.md](../../docs/kindle.md) for full setup and configuration details.

### linkding-ingest

Fetches bookmarks from a [Linkding](https://github.com/sissbruecker/linkding) instance, reads the bookmarked content via Tabstack, and records insights to the wiki knowledge base. Delegates each bookmark to a child agent for parallel processing.

**Requires:** `LINKDING_URL`, `LINKDING_TOKEN` env vars, `linkding-to-markdown` binary

**Schedule:** Every 4 hours (`:45`)

### mastodon-ingest

Fetches recent posts from a Mastodon account and records interesting content to the wiki knowledge base.

**Requires:** `MASTODON_SERVER`, `MASTODON_ACCESS_TOKEN` env vars, `mastodon-to-markdown` binary

**Schedule:** Every 4 hours (`:30`)
