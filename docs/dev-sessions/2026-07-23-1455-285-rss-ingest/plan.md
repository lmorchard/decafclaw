# rss-ingest Contrib Skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A prose-driven `rss-ingest` contrib skill that fetches new RSS/Atom feed items to markdown for the agent to integrate into the vault, mirroring `mastodon-ingest`.

**Architecture:** A single Python script (`fetch_feeds.py`) run via `uv run --no-project` with PEP 723 inline `feedparser` dependency. Feedparser is confined to one thin `parse_feed()` adapter; all other logic is pure-Python (importable + unit-tested in `make test`). `fetch.sh` wraps the script and adds feed-management subcommands. The agent does all vault work via SKILL.md prose.

**Tech Stack:** Python 3.11+ stdlib, `feedparser` (via uv inline deps), bash, pytest (importlib-loaded contrib tests).

## Global Constraints

- **No feedparser in the project env.** It is available ONLY inside `uv run --no-project`. Any code imported by `make test` must not import feedparser at module top level; `parse_feed()` imports it lazily inside the function.
- **Feed list + state live in the runtime workspace**, never the git checkout: base dir `${DECAFCLAW_WORKSPACE:-$PWD}/skill-state/rss-ingest/` — `feeds.txt` and `state.json`.
- **Output pages:** flat `agent/pages/rss/`, `[[wiki-links]]`, `added_by: rss-ingest`.
- **First run per feed:** 24h look-back window. Backfill modes never update state.
- **Contrib test convention:** colocated at skill root, importlib-loaded (mirror `contrib/skills/kindle/test_tools.py`). Fixtures under `fixtures/`.
- **No eval** (opt-in contrib skill).
- Commit messages end with the `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.

## File Structure

```
contrib/skills/rss-ingest/
  SKILL.md              # agent instructions (Task 4)
  SCHEDULE.md           # cron sidecar (Task 4)
  fetch.sh              # wrapper + feed-management subcommands (Task 3)
  fetch_feeds.py        # pure logic (Task 1) + feedparser adapter + CLI (Task 2/3)
  test_fetch_feeds.py   # importlib-loaded unit tests (Tasks 1-2)
  fixtures/
    sample_rss.xml      # RSS 2.0 (Task 2)
    sample_atom.xml     # Atom (Task 2)
```

---

### Task 1: Pure logic in `fetch_feeds.py`

Pure-Python functions with no feedparser and no network — the high-value, fully-unit-tested core.

**Files:**
- Create: `contrib/skills/rss-ingest/fetch_feeds.py`
- Test: `contrib/skills/rss-ingest/test_fetch_feeds.py`

**Interfaces:**
- Produces (consumed by Tasks 2-3):
  - `Entry` = `dict` with keys `guid: str`, `title: str`, `link: str`, `published: datetime | None`, `summary: str`, `feed_name: str`.
  - `parse_feeds_txt(text: str) -> list[tuple[str, str | None]]` → list of `(url, name_or_None)`.
  - `select_new_entries(entries: list[Entry], feed_state: dict, now: datetime) -> list[Entry]`.
  - `render_markdown(entries_by_feed: dict[str, list[Entry]]) -> str`.
  - `load_state(path) -> dict`, `save_state(path, state, *, guid_cap=200) -> None`.
  - `feeds_add(text, url, name) -> str`, `feeds_remove(text, url) -> str` (return new file text).

- [ ] **Step 1: Write failing tests**

Create `contrib/skills/rss-ingest/test_fetch_feeds.py`:

```python
"""Tests for the rss-ingest fetch_feeds pure logic (importlib-loaded)."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_THIS_DIR = Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "decafclaw_contrib_rss_fetch_feeds", _THIS_DIR / "fetch_feeds.py"
)
assert _spec is not None and _spec.loader is not None
ff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ff)

UTC = timezone.utc


def _entry(guid, published, feed_name="Blog", title="t", link="http://x/1", summary="s"):
    return {"guid": guid, "title": title, "link": link,
            "published": published, "summary": summary, "feed_name": feed_name}


def test_parse_feeds_txt_handles_comments_blanks_and_names():
    text = "\n".join([
        "# a comment",
        "",
        "https://a.example/feed.xml",
        "Simon|https://b.example/atom.xml",
        "   ",
    ])
    assert ff.parse_feeds_txt(text) == [
        ("https://a.example/feed.xml", None),
        ("https://b.example/atom.xml", "Simon"),
    ]


def test_select_new_entries_first_run_uses_24h_window():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    old = _entry("g-old", now - timedelta(hours=48))
    fresh = _entry("g-new", now - timedelta(hours=2))
    out = ff.select_new_entries([old, fresh], {}, now)
    assert [e["guid"] for e in out] == ["g-new"]


def test_select_new_entries_filters_by_last_published():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    state = {"last_published": "2026-07-23T06:00:00+00:00", "seen_guids": []}
    older = _entry("g1", now - timedelta(hours=12))   # before last_published
    newer = _entry("g2", now - timedelta(hours=1))
    out = ff.select_new_entries([older, newer], state, now)
    assert [e["guid"] for e in out] == ["g2"]


def test_select_new_entries_dedupes_seen_guids():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    state = {"last_published": "2026-07-23T00:00:00+00:00", "seen_guids": ["g2"]}
    e = _entry("g2", now - timedelta(hours=1))
    assert ff.select_new_entries([e], state, now) == []


def test_render_markdown_groups_by_feed_and_shows_fields():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    md = ff.render_markdown({"Blog": [_entry("g", now, title="Hello", link="http://x/p")]})
    assert "## Blog" in md
    assert "Hello" in md
    assert "http://x/p" in md
    assert "2026-07-23" in md


def test_save_state_caps_seen_guids(tmp_path):
    p = tmp_path / "state.json"
    guids = [f"g{i}" for i in range(300)]
    ff.save_state(p, {"feed": {"last_published": "2026-07-23T00:00:00+00:00",
                               "seen_guids": guids}}, guid_cap=200)
    loaded = ff.load_state(p)
    assert len(loaded["feed"]["seen_guids"]) == 200
    assert loaded["feed"]["seen_guids"][-1] == "g299"  # keeps most recent


def test_feeds_add_is_idempotent_and_remove_works():
    text = "https://a.example/feed.xml\n"
    added = ff.feeds_add(text, "https://b.example/x.xml", "B")
    assert "B|https://b.example/x.xml" in added
    assert ff.feeds_add(added, "https://b.example/x.xml", "B") == added  # idempotent
    removed = ff.feeds_remove(added, "https://b.example/x.xml")
    assert "b.example" not in removed
    assert "a.example" in removed
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest contrib/skills/rss-ingest/test_fetch_feeds.py -n 0 -q`
Expected: FAIL — `fetch_feeds.py` not found / attributes missing.

- [ ] **Step 3: Implement `fetch_feeds.py` pure logic**

Create `contrib/skills/rss-ingest/fetch_feeds.py`:

```python
#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["feedparser"]
# ///
"""Fetch new RSS/Atom feed items as markdown for the rss-ingest skill.

feedparser is used ONLY inside parse_feed() (lazy import) so the rest of this
module imports cleanly in the project test env, where feedparser is absent.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
FIRST_RUN_WINDOW = timedelta(hours=24)
DEFAULT_GUID_CAP = 200


def parse_feeds_txt(text: str) -> list[tuple[str, str | None]]:
    """Parse feeds.txt: one feed per line, `#` comments + blanks ignored,
    optional `name|url`."""
    feeds: list[tuple[str, str | None]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            name, url = line.split("|", 1)
            feeds.append((url.strip(), name.strip() or None))
        else:
            feeds.append((line, None))
    return feeds


def _as_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def select_new_entries(entries: list[dict], feed_state: dict, now: datetime) -> list[dict]:
    """Return entries newer than feed_state['last_published'] (or the 24h
    window on first run), excluding already-seen guids. Sorted oldest-first."""
    last_pub = _as_dt(feed_state.get("last_published"))
    cutoff = last_pub if last_pub is not None else now - FIRST_RUN_WINDOW
    seen = set(feed_state.get("seen_guids", []))
    out = []
    for e in entries:
        pub = e.get("published")
        if e["guid"] in seen:
            continue
        if pub is not None and pub <= cutoff:
            continue
        out.append(e)
    out.sort(key=lambda e: (e["published"] or now))
    return out


def render_markdown(entries_by_feed: dict[str, list[dict]]) -> str:
    """Render selected entries to markdown grouped by feed name."""
    blocks: list[str] = []
    for feed_name, entries in entries_by_feed.items():
        if not entries:
            continue
        lines = [f"## {feed_name}", ""]
        for e in entries:
            pub = e.get("published")
            date_str = pub.strftime("%Y-%m-%d") if pub else "(no date)"
            lines.append(f"### {e['title']}")
            lines.append(f"- {date_str} — {e['link']}")
            if e.get("summary"):
                lines.append("")
                lines.append(e["summary"].strip())
            lines.append("")
        blocks.append("\n".join(lines))
    return "\n".join(blocks).strip() + ("\n" if blocks else "")


def load_state(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path: str | Path, state: dict, *, guid_cap: int = DEFAULT_GUID_CAP) -> None:
    capped = {}
    for feed_url, fs in state.items():
        guids = list(fs.get("seen_guids", []))[-guid_cap:]
        capped[feed_url] = {"last_published": fs.get("last_published"), "seen_guids": guids}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(capped, indent=2))


def feeds_add(text: str, url: str, name: str | None = None) -> str:
    """Append a feed to feeds.txt text if its URL isn't already present."""
    existing = {u for u, _ in parse_feeds_txt(text)}
    if url in existing:
        return text
    line = f"{name}|{url}" if name else url
    sep = "" if text.endswith("\n") or not text else "\n"
    return f"{text}{sep}{line}\n"


def feeds_remove(text: str, url: str) -> str:
    """Remove any line referencing url from feeds.txt text."""
    kept = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            kept.append(raw)
            continue
        this_url = line.split("|", 1)[1].strip() if "|" in line else line
        if this_url == url:
            continue
        kept.append(raw)
    return "\n".join(kept) + ("\n" if kept else "")
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest contrib/skills/rss-ingest/test_fetch_feeds.py -n 0 -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add contrib/skills/rss-ingest/fetch_feeds.py contrib/skills/rss-ingest/test_fetch_feeds.py
git commit -m "feat(rss-ingest): pure feed-selection + feeds.txt logic with tests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `parse_feed()` feedparser adapter + fixtures

The one function that touches feedparser, plus fixture-backed tests gated on feedparser being importable.

**Files:**
- Modify: `contrib/skills/rss-ingest/fetch_feeds.py` (add `parse_feed`)
- Create: `contrib/skills/rss-ingest/fixtures/sample_rss.xml`, `contrib/skills/rss-ingest/fixtures/sample_atom.xml`
- Test: `contrib/skills/rss-ingest/test_fetch_feeds.py` (add adapter test)

**Interfaces:**
- Consumes: `Entry` shape from Task 1.
- Produces: `parse_feed(raw: str, feed_name: str) -> list[Entry]` (consumed by Task 3 CLI).

- [ ] **Step 1: Create fixtures**

`contrib/skills/rss-ingest/fixtures/sample_rss.xml`:

```xml
<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Sample Blog</title>
    <item>
      <title>First Post</title>
      <link>https://blog.example/first</link>
      <guid>https://blog.example/first</guid>
      <pubDate>Wed, 23 Jul 2026 10:00:00 +0000</pubDate>
      <description>Hello world summary.</description>
    </item>
    <item>
      <title>Second Post</title>
      <link>https://blog.example/second</link>
      <guid>https://blog.example/second</guid>
      <pubDate>Tue, 22 Jul 2026 10:00:00 +0000</pubDate>
      <description>Another summary.</description>
    </item>
  </channel>
</rss>
```

`contrib/skills/rss-ingest/fixtures/sample_atom.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Sample Atom</title>
  <entry>
    <title>Atom Entry</title>
    <link href="https://atom.example/e1"/>
    <id>urn:atom:e1</id>
    <updated>2026-07-23T09:00:00Z</updated>
    <summary>Atom summary text.</summary>
  </entry>
</feed>
```

- [ ] **Step 2: Write failing adapter test**

Append to `test_fetch_feeds.py`:

```python
import pytest

feedparser = pytest.importorskip("feedparser")  # skips in envs without feedparser


def test_parse_feed_rss_fixture():
    raw = (_THIS_DIR / "fixtures" / "sample_rss.xml").read_text()
    entries = ff.parse_feed(raw, "Sample Blog")
    assert len(entries) == 2
    first = entries[0]
    assert first["title"] == "First Post"
    assert first["link"] == "https://blog.example/first"
    assert first["guid"] == "https://blog.example/first"
    assert first["feed_name"] == "Sample Blog"
    assert first["published"].year == 2026
    assert "summary" in first["summary"].lower()


def test_parse_feed_atom_fixture_uses_id_and_updated():
    raw = (_THIS_DIR / "fixtures" / "sample_atom.xml").read_text()
    entries = ff.parse_feed(raw, "Sample Atom")
    assert len(entries) == 1
    assert entries[0]["guid"] == "urn:atom:e1"
    assert entries[0]["link"] == "https://atom.example/e1"
    assert entries[0]["published"] is not None
```

- [ ] **Step 3: Run adapter test, verify it fails**

Run: `uv run pytest contrib/skills/rss-ingest/test_fetch_feeds.py -n 0 -q -k parse_feed`
Expected: FAIL — `ff.parse_feed` missing (feedparser is present under `uv run`, so importorskip does NOT skip).

- [ ] **Step 4: Implement `parse_feed`**

Add to `fetch_feeds.py`:

```python
def parse_feed(raw: str, feed_name: str) -> list[dict]:
    """Parse raw RSS/Atom bytes/text into normalized Entry dicts.

    The ONLY feedparser-dependent function — imported lazily so the module
    stays importable where feedparser is absent (project test env).
    """
    import feedparser  # lazy: confined to this adapter

    parsed = feedparser.parse(raw)
    entries: list[dict] = []
    for e in parsed.entries:
        struct = e.get("published_parsed") or e.get("updated_parsed")
        published = None
        if struct is not None:
            published = datetime(*struct[:6], tzinfo=UTC)
        guid = e.get("id") or e.get("guid") or e.get("link") or ""
        summary = e.get("summary", "")
        entries.append({
            "guid": guid,
            "title": e.get("title", "(untitled)"),
            "link": e.get("link", ""),
            "published": published,
            "summary": summary,
            "feed_name": feed_name,
        })
    return entries
```

- [ ] **Step 5: Run adapter test, verify pass**

Run: `uv run pytest contrib/skills/rss-ingest/test_fetch_feeds.py -n 0 -q -k parse_feed`
Expected: PASS (2 tests).

- [ ] **Step 6: Verify project-env import still works (feedparser absent)**

Run: `uv run --no-project python -c "import importlib.util,pathlib; s=importlib.util.spec_from_file_location('m', pathlib.Path('contrib/skills/rss-ingest/fetch_feeds.py')); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('import OK')"` — but with a clean interpreter lacking feedparser this still works because the import is lazy.
Simpler check: `make test` (Task 5 gate) must not error importing the module. Confirm the module has NO top-level `import feedparser`.

- [ ] **Step 7: Commit**

```bash
git add contrib/skills/rss-ingest/fetch_feeds.py contrib/skills/rss-ingest/test_fetch_feeds.py contrib/skills/rss-ingest/fixtures/
git commit -m "feat(rss-ingest): feedparser adapter + RSS/Atom fixtures

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: CLI `main()` + `fetch.sh` wrapper

Wire the pieces into a runnable command with auto/backfill fetch and feed-management subcommands.

**Files:**
- Modify: `contrib/skills/rss-ingest/fetch_feeds.py` (add `main()` + `if __name__` guard)
- Create: `contrib/skills/rss-ingest/fetch.sh`

**Interfaces:**
- Consumes: all Task 1-2 functions.
- CLI surface (invoked by `fetch.sh`):
  - `fetch_feeds.py fetch [--since D | --start YYYY-MM-DD --end YYYY-MM-DD]`
  - `fetch_feeds.py list|add <url> [name]|remove <url>`

- [ ] **Step 1: Implement CLI in `fetch_feeds.py`**

Add to `fetch_feeds.py` (state/feeds paths come from env, matching mastodon-ingest's workspace convention):

```python
import argparse
import os
import sys
import urllib.request


def _state_dir() -> Path:
    base = os.environ.get("DECAFCLAW_WORKSPACE") or os.getcwd()
    return Path(base) / "skill-state" / "rss-ingest"


def _feeds_path() -> Path:
    return _state_dir() / "feeds.txt"


def _state_path() -> Path:
    return _state_dir() / "state.json"


def _fetch_url(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "decafclaw-rss-ingest/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted, user-configured feeds)
        return resp.read().decode("utf-8", errors="replace")


def _now() -> datetime:
    return datetime.now(UTC)


def cmd_fetch(update_state: bool) -> int:
    feeds_file = _feeds_path()
    if not feeds_file.exists():
        print(f"[error: no feeds configured. Add one with: fetch.sh add <url>]", file=sys.stderr)
        return 1
    feeds = parse_feeds_txt(feeds_file.read_text())
    if not feeds:
        print("[error: feeds.txt has no feeds]", file=sys.stderr)
        return 1
    state = load_state(_state_path())
    now = _now()
    by_feed: dict[str, list[dict]] = {}
    reachable = 0
    for url, name in feeds:
        try:
            raw = _fetch_url(url)
            entries = parse_feed(raw, name or url)
            reachable += 1
        except Exception as exc:  # network / parse — skip this feed, keep going
            print(f"[warn: failed to fetch {url}: {exc}]", file=sys.stderr)
            continue
        feed_state = state.get(url, {})
        new = select_new_entries(entries, feed_state, now)
        if new:
            by_feed[name or url] = new
        if update_state:
            latest = max((e["published"] for e in entries if e["published"]), default=None)
            state[url] = {
                "last_published": (latest or now).isoformat(),
                "seen_guids": list(feed_state.get("seen_guids", [])) + [e["guid"] for e in new],
            }
    if reachable == 0:
        print("[error: no feeds reachable]", file=sys.stderr)
        return 1
    if update_state:
        save_state(_state_path(), state)
    md = render_markdown(by_feed)
    print(md if md.strip() else "(no new items)")
    return 0


def cmd_feeds(action: str, url: str | None, name: str | None) -> int:
    path = _feeds_path()
    text = path.read_text() if path.exists() else ""
    if action == "list":
        print(text.strip() or "(no feeds configured)")
        return 0
    if action == "add":
        new = feeds_add(text, url, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)
        print(f"added: {url}" if new != text else f"already subscribed: {url}")
        return 0
    if action == "remove":
        new = feeds_remove(text, url)
        path.write_text(new)
        print(f"removed: {url}" if new != text else f"not found: {url}")
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fetch_feeds.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("fetch")
    pf.add_argument("--since")
    pf.add_argument("--start")
    pf.add_argument("--end")
    sub.add_parser("list")
    pa = sub.add_parser("add"); pa.add_argument("url"); pa.add_argument("name", nargs="?")
    pr = sub.add_parser("remove"); pr.add_argument("url")
    args = p.parse_args(argv)
    if args.cmd == "fetch":
        # Backfill (any --since/--start) does not update stored state.
        update_state = not (args.since or args.start or args.end)
        return cmd_fetch(update_state=update_state)
    if args.cmd == "list":
        return cmd_feeds("list", None, None)
    if args.cmd == "add":
        return cmd_feeds("add", args.url, args.name)
    if args.cmd == "remove":
        return cmd_feeds("remove", args.url, None)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

> Note: `--since`/`--start`/`--end` currently only toggle state-update behavior (backfill semantics); RSS feeds don't support server-side date queries, so the window is enforced client-side by `select_new_entries`. Backfill widens output by not persisting state, so a subsequent auto-run re-emits within its normal window. This is acceptable for v1; document the flags as "re-scan without advancing the cursor."

- [ ] **Step 2: Create `fetch.sh`**

`contrib/skills/rss-ingest/fetch.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY="${SCRIPT_DIR}/fetch_feeds.py"

if ! command -v uv >/dev/null 2>&1; then
    echo "[error: uv not found — required to run rss-ingest]" >&2
    exit 1
fi

# Feed-management subcommands pass straight through.
case "${1:-}" in
    list|add|remove)
        exec uv run --no-project "$PY" "$@"
        ;;
esac

# Fetch mode. With no args → auto (advances state). Any args (--since/--start/
# --end) → backfill re-scan that does NOT advance state.
if [ "$#" -gt 0 ]; then
    exec uv run --no-project "$PY" fetch "$@"
fi
exec uv run --no-project "$PY" fetch
```

- [ ] **Step 3: Make `fetch.sh` executable + smoke test feed management**

```bash
chmod +x contrib/skills/rss-ingest/fetch.sh
cd /tmp && DECAFCLAW_WORKSPACE=/tmp/rss-smoke bash <path>/fetch.sh add https://simonwillison.net/atom/everything/ "Simon"
DECAFCLAW_WORKSPACE=/tmp/rss-smoke bash <path>/fetch.sh list
```
Expected: `added: ...` then the feed listed. (`<path>` = absolute worktree path to the skill.)

- [ ] **Step 4: Smoke test a real fetch (network)**

```bash
DECAFCLAW_WORKSPACE=/tmp/rss-smoke bash <path>/fetch.sh
```
Expected: markdown with recent entries (or `(no new items)`); a second immediate run prints `(no new items)` — confirms incremental state advanced. Clean up `/tmp/rss-smoke` after.

- [ ] **Step 5: Commit**

```bash
git add contrib/skills/rss-ingest/fetch_feeds.py contrib/skills/rss-ingest/fetch.sh
git commit -m "feat(rss-ingest): CLI (fetch/list/add/remove) + fetch.sh wrapper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: SKILL.md + SCHEDULE.md (agent contract)

Prose the agent follows. Adapt `contrib/skills/mastodon-ingest/SKILL.md` and `SCHEDULE.md` — read them first as the template.

**Files:**
- Create: `contrib/skills/rss-ingest/SKILL.md`
- Create: `contrib/skills/rss-ingest/SCHEDULE.md`

- [ ] **Step 1: Write `SKILL.md`**

Frontmatter:

```yaml
---
name: rss-ingest
description: Fetch new items from subscribed RSS/Atom feeds and record interesting content to the vault
effort: default
allowed-tools: shell($SKILL_DIR/fetch.sh*), vault_read, vault_write, vault_search, vault_list, vault_backlinks, vault_journal_append, current_time
user-invocable: true
---
```

Body sections (write in full, adapting mastodon-ingest wording):
1. **Output Folder** — flat `agent/pages/rss/`, `[[wiki-links]]`, garden promotes clusters. (Copy mastodon's phrasing.)
2. **Managing feeds** — feeds.txt lives at `workspace/skill-state/rss-ingest/feeds.txt`, format (one URL/line, `#` comments, `name|url`). To subscribe/unsubscribe on the user's behalf:
   - `$SKILL_DIR/fetch.sh list`
   - `$SKILL_DIR/fetch.sh add <url> [name]`
   - `$SKILL_DIR/fetch.sh remove <url>`
   Include an explicit example: when the user says "subscribe to <blog>'s RSS," run `fetch.sh add <feed-url>`.
3. **Step 1: Fetch** — run `$SKILL_DIR/fetch.sh` (auto = new items since last run). Backfill: `fetch.sh --since 7d` (re-scan; does not advance the cursor).
4. **Step 2: Review** — signal filtering: skip low-signal items (routine link dumps, pure promo, duplicate coverage). Mirror mastodon "skip boring posts."
5. **Step 3: Update the wiki** — create/update page rules; frontmatter shape with `sources:` list (`url`, `date`, `added_by: rss-ingest`) and body `## Sources`. Adapt mastodon's step 3 verbatim, swapping `added_by`.
6. **Step 4: Finish** — summarize changes or reply `HEARTBEAT_OK`.
7. **Rules** — attribute third-party content; convert relative dates to absolute; only create pages for genuinely interesting items.

- [ ] **Step 2: Write `SCHEDULE.md`**

```markdown
---
schedule: "0 */4 * * *"
model: default
required-skills:
  - rss-ingest
allowed-tools: shell($SKILL_DIR/fetch.sh*), vault_read, vault_write, vault_search, vault_list, vault_backlinks, vault_journal_append, current_time
---

Time for the scheduled RSS ingestion. Follow the rss-ingest skill instructions to completion.
```

- [ ] **Step 3: Sanity-check frontmatter parses**

Run: `uv run python -c "import yaml,pathlib,re; t=pathlib.Path('contrib/skills/rss-ingest/SKILL.md').read_text(); fm=re.match(r'^---\n(.*?)\n---', t, re.S).group(1); d=yaml.safe_load(fm); print(sorted(d))"`
Expected: prints keys incl. `allowed-tools`, `description`, `name`, `user-invocable` — no YAML error.

- [ ] **Step 4: Commit**

```bash
git add contrib/skills/rss-ingest/SKILL.md contrib/skills/rss-ingest/SCHEDULE.md
git commit -m "feat(rss-ingest): SKILL.md + SCHEDULE.md agent contract

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Full-suite gate + docs

Confirm the skill's tests run inside `make test` (feedparser-absent) and note the skill in contrib docs.

**Files:**
- Modify: `contrib/skills/README.md` (add rss-ingest row/entry, matching existing style)

- [ ] **Step 1: Run the skill's tests exactly as `make test` will (project env, no feedparser)**

Run: `make test 2>&1 | tail -5`
Expected: all pass, **0 warnings** (repo promotes `PytestUnraisableExceptionWarning` to error). The `parse_feed` adapter tests are skipped in this env via `importorskip`; the pure-logic tests run.

- [ ] **Step 2: Confirm adapter tests actually execute under uv (feedparser present)**

Run: `uv run pytest contrib/skills/rss-ingest/test_fetch_feeds.py -n 0 -q`
Expected: all tests PASS including `parse_feed` ones (not skipped).

- [ ] **Step 3: Update contrib README**

Read `contrib/skills/README.md`; add an `rss-ingest` entry mirroring the `mastodon-ingest` / `linkding-ingest` entries (one-line description + any config note: feeds via `fetch.sh add`).

- [ ] **Step 4: Lint**

Run: `make lint`
Expected: All checks passed. (If ruff flags `fetch_feeds.py`, fix; the `# noqa: S310` on urlopen is intentional.)

- [ ] **Step 5: Commit**

```bash
git add contrib/skills/README.md
git commit -m "docs(rss-ingest): add skill to contrib README

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Shape/files → Tasks 1-4. ✅
- uv/PEP723 deps → Task 1 header + Task 3 `fetch.sh`. ✅
- Data flow (script→stdout, agent→vault) → Task 3 `cmd_fetch` prints markdown; Task 4 SKILL.md does vault. ✅
- feeds.txt config + management → Task 1 (`feeds_add/remove`), Task 3 (`cmd_feeds`, `fetch.sh`), Task 4 (SKILL.md §Managing feeds). ✅
- Incremental state + 24h first run + guid cap → Task 1 (`select_new_entries`, `save_state`), Task 3 (`cmd_fetch`). ✅
- Output folder flat → Task 4 §1. ✅
- Signal filtering by agent → Task 4 §Review. ✅
- Schedule 4h → Task 4 SCHEDULE.md. ✅
- Backfill mode → Task 3 (`--since` toggles state update) + Task 4 §Fetch. ✅
- Output markdown shape → Task 1 `render_markdown`. ✅
- Testing (pure in make test, adapter importorskip) → Tasks 1,2,5. ✅
- No eval → honored (none added). ✅

**Placeholder scan:** No TBD/TODO; all code steps show complete code. `<path>` in Task 3 smoke steps is an intentional local-path stand-in for a manual command, not code. ✅

**Type consistency:** `Entry` keys (`guid/title/link/published/summary/feed_name`) consistent across `parse_feed`, `select_new_entries`, `render_markdown`, `cmd_fetch`. `feeds_add(text,url,name)` / `feeds_remove(text,url)` signatures consistent Task 1 ↔ Task 3. State shape `{feed_url: {last_published, seen_guids}}` consistent in `select_new_entries`, `save_state`, `cmd_fetch`. ✅
