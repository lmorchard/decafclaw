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

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

UTC = timezone.utc
FIRST_RUN_WINDOW = timedelta(hours=24)
DEFAULT_GUID_CAP = 200
SUMMARY_MAX_CHARS = 500


class _TextExtractor(HTMLParser):
    """Collect text nodes, dropping tags — stdlib-only HTML→text."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def clean_summary(raw: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """Strip HTML tags, unescape entities, collapse whitespace, and truncate
    a feed summary to a plain-text excerpt."""
    if not raw:
        return ""
    parser = _TextExtractor()
    parser.feed(raw)
    text = re.sub(r"\s+", " ", unescape(parser.text())).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


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


def parse_feed(raw: str, feed_name: str) -> list[dict]:
    """Parse raw RSS/Atom text into normalized Entry dicts.

    The ONLY feedparser-dependent function — imported lazily so the module
    stays importable where feedparser is absent (project test env).
    """
    import feedparser  # lazy: confined to this adapter

    parsed = feedparser.parse(raw)
    entries: list[dict] = []
    for e in parsed.entries:
        struct = e.get("published_parsed") or e.get("updated_parsed")
        published = datetime(*struct[:6], tzinfo=UTC) if struct is not None else None
        guid = e.get("id") or e.get("guid") or e.get("link") or ""
        entries.append({
            "guid": guid,
            "title": e.get("title", "(untitled)"),
            "link": e.get("link", ""),
            "published": published,
            "summary": clean_summary(e.get("summary", "")),
            "feed_name": feed_name,
        })
    return entries


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


# --- CLI ---------------------------------------------------------------------


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
        print("[error: no feeds configured. Add one with: fetch.sh add <url>]", file=sys.stderr)
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
        assert url is not None  # main() only calls add with a URL argument
        new = feeds_add(text, url, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new)
        print(f"added: {url}" if new != text else f"already subscribed: {url}")
        return 0
    if action == "remove":
        assert url is not None  # main() only calls remove with a URL argument
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
    pa = sub.add_parser("add")
    pa.add_argument("url")
    pa.add_argument("name", nargs="?")
    pr = sub.add_parser("remove")
    pr.add_argument("url")
    args = p.parse_args(argv)
    if args.cmd == "fetch":
        # Backfill (any --since/--start/--end) does not update stored state.
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
