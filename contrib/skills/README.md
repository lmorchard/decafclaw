# Contrib Skills

Optional skills that require external services or API keys. Not enabled by default.

## Installation

Two installation styles, depending on whether you want updates to flow with `git pull`.

### Option 1 — Reference (recommended)

Add the skill's directory to your agent's `extra_skill_paths` so the loader picks it up in place. `git pull` then keeps `SKILL.md` up to date automatically; downloaded binaries persist (they're already gitignored).

**Why `$VAR` over relative paths.** Relative entries in `extra_skill_paths` are anchored to `data/{agent_id}/`, not to the repo or the CWD. That only reaches the repo's `contrib/skills/` when `data_home` happens to live inside the repo (the default `./data` dev layout). Production deployments usually keep `data_home` somewhere stable like `~/.decafclaw/`, which is nowhere near the repo — relative paths will silently miss. A `$VAR` decouples the two.

In `data/{agent_id}/config.json`:

```json
{
  "extra_skill_paths": [
    "$DECAFCLAW_REPO/contrib/skills/linkding-ingest",
    "$DECAFCLAW_REPO/contrib/skills/mastodon-ingest"
  ]
}
```

`$DECAFCLAW_REPO` is auto-populated by the skill loader when running from a source checkout — it walks up from the installed package and uses the parent directory that contains both `contrib/` and `pyproject.toml`. No `.env` setting required for the common case.

For the very common pattern of pointing at a contrib skill, `$CONTRIB` is auto-populated as a shorthand for `$DECAFCLAW_REPO/contrib`, so you can write:

```json
{
  "extra_skill_paths": [
    "$CONTRIB/skills/linkding-ingest",
    "$CONTRIB/skills/mastodon-ingest"
  ]
}
```

If you need to override (point at a different checkout, run from a wheel-only install without `contrib/` adjacent, etc.), set either explicitly:

```bash
# .env
DECAFCLAW_REPO=/absolute/path/to/decafclaw-repo
# Or override CONTRIB directly if your contrib/ lives somewhere unusual:
# CONTRIB=/path/to/some/other/contrib
```

Explicit env values always win over auto-detection. `$CONTRIB` follows `$DECAFCLAW_REPO` unless you also set it explicitly.

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

### blog-ideas

A daily brainstorm pass: reviews the week-so-far across your ingested activity (`agent/pages/{bookmarks,mastodon,youtube,github,podcasts,music,kindle}`), your Obsidian daily journal (`journals/`), and your blog archive (`blog/drafts`, `blog/daily`, `blog/weeknotes`), then maintains a living tiered "blog ideas this week" page at `agent/pages/blog-ideas/{ISO-week}.md`. Exposes one tool, `blog_ideas_week`, that returns the deterministic ISO-week identity + page path so the agent never hand-computes weeks. Delivery rides the existing newsletter's scheduled-activity aggregation (no newsletter changes).

**Requires:** Nothing — but it's only useful if the vault already holds ingested activity (e.g. via `meta-ingest`) and a synced Obsidian vault with journal + blog folders.

**Schedule:** ships a `SCHEDULE.md` for a daily 06:00 UTC run, but as a contrib skill it's force-disabled — opt in by creating an overlay at `data/{agent_id}/schedules/blog-ideas.md` (copy the skill's `SCHEDULE.md` and set `enabled: true`). Also runs on demand via the `/blog-ideas` command.

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

### meta-ingest

Unified successor to `linkding-ingest` + `mastodon-ingest`. Uses the [`me-to-markdown`](https://github.com/lmorchard/me-to-markdown) orchestrator to fetch **all** registered sources (Mastodon, Linkding, GitHub, Spotify, YouTube, Pocket Casts) over one shared time window, then fans out one child agent per source to analyze it and record insights to the vault (heavy content like article text stays in the children, never the parent).

**Requires:** `me-to-markdown` on `$PATH` (run `me-to-markdown install` + `me-to-markdown auth`). Per-source credentials live in `me-to-markdown`'s own config/env — this skill doesn't read them. No bundled binary / `download-binary.sh`.

**Schedule:** Every 12 hours (`:15`), **disabled by default** (contrib SCHEDULE.md is forced `enabled: false`). Coexists with `linkding-ingest` / `mastodon-ingest`; retire those schedules and enable this one once validated — see the skill's SCHEDULE.md.

### writing-clearly

Edits prose drafts (docs, commit messages, replies, blog posts) using William Strunk Jr.'s *The Elements of Style* (1918). Exposes one tool, `edit_with_strunk(draft, focus="")`, which inlines the rulebook into a `delegate_task` child agent — the corpus (~12k tokens) never enters the parent conversation.

**Requires:** Nothing — public-domain corpus is bundled.

**Optional config:** `WRITING_CLEARLY_MODEL` env var to pin the child to a specific model; otherwise inherits the parent's active model.

Adapted from [obra/the-elements-of-style](https://github.com/obra/the-elements-of-style).
